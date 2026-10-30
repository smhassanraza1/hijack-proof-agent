"""Run the assistant over the eval cases and write hillclimb-shaped results.

    python -m evals.run_eval --variant baseline [--agent baseline] [--split test] [--limit 5] [--reps 1]
    python -m evals.run_eval --flow results/registered --cases evals/cases_registered.jsonl --variant v3-ii --split test
    python -m evals.run_eval [--flow results/registered] --approve-harness   # the maintainer records the harness hash

--flow is the results folder (default results/, development) and holds its own _state.json; --cases the
case file (default evals/cases.jsonl). Writes <flow>/<variant>/results.jsonl (one row per case and rep, appended as each finishes),
traces/<id>_rep<k>.json, and errors.jsonl for attempts that produced nothing scorable
(serving error, timeout, served-model mismatch, harness error). Resume skips (case, rep)
pairs already in results.jsonl; parallel workers (--shard i/N) append to the same files,
one line per write. Once the maintainer has run --approve-harness, refuses to run when
the harness files changed since; before that it warns. The gate detects changes, it is not a
security boundary.
"""
import argparse
import hashlib
import importlib
import json
import signal
import sys
import time
from pathlib import Path

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.errors import AbortAgentError
from agentdojo.agent_pipeline.ground_truth_pipeline import GroundTruthPipeline
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.functions_runtime import EmptyEnv, FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suites
from agentdojo.task_suite.task_suite import functions_stack_trace_from_messages, model_output_from_messages
from agentdojo.types import (ChatAssistantMessage, ChatUserMessage, get_text_content_as_str,
                             text_content_block_from_string)

import evals.attacks  # noqa: F401 - registers the defence-aware attack template
from agent.llm import ServerError

ROOT = Path(__file__).resolve().parent.parent
FLOW = ROOT / "results"
CASES = Path(__file__).with_name("cases.jsonl")
SUITES = ("workspace", "travel", "banking", "slack")


AGENTS = {"baseline": "agent.variants.baseline", "v1": "agent.variants.v1.pipeline",
          "v2": "agent.variants.v2.pipeline", "v3": "agent.pipeline", "v4": "agent.variants.v4.pipeline",
          "v5": "agent.variants.v5.pipeline"}


class CaseTimeout(BaseException):
    """Raised by the per-case alarm. Not an Exception, so a tool's `except Exception` cannot swallow it."""


def agent_for(variant: str, agent: str | None) -> str:
    """The agent code a variant runs: --agent, else the variant name's prefix (v4-injecagent -> v4)."""
    name = agent or variant.split("-")[0]
    if name not in AGENTS:
        sys.exit(f"--agent is required for variant {variant!r}: one of {', '.join(AGENTS)}")
    return name


def harness_sha(state: dict) -> str:
    digest = hashlib.sha256()
    for rel in sorted({"evals/run_eval.py", *state.get("harness_paths", [])}):
        digest.update(rel.encode() + b"\0" + (ROOT / rel).read_bytes() + b"\0")
    return digest.hexdigest()


def load_state(flow: Path = FLOW) -> dict:
    return json.loads((flow / "_state.json").read_text())


def text(content) -> str:
    if content is None or isinstance(content, str):
        return content or ""
    return get_text_content_as_str(content)


def to_trace(messages) -> list[dict]:
    turns = []
    for m in messages:
        if m["role"] == "assistant":
            turns.append({"role": "assistant", "content": text(m["content"])})
            for call in m.get("tool_calls") or []:
                turns.append({"role": "tool_call", "name": call.function, "content": json.dumps(call.args, indent=2)})
        elif m["role"] == "tool":
            turns.append({"role": "tool_result", "content": m["error"] or text(m["content"])})
        else:
            turns.append({"role": m["role"], "content": text(m["content"])})
    return turns


def run_case(case: dict, suites, pipeline, attack: str, injection_text: str | None = None) -> tuple[dict, list, dict]:
    """Mirrors TaskSuite.run_task_with_pipeline without its silent up-to-3 retries.

    `injection_text`, when given, is placed in every injection slot the user task reaches instead of the
    `attack` template's text (the adaptive attacker's per-case injections)."""
    suite = suites[case["suite"]]
    user_task = suite.user_tasks[case["user_task"]]
    injection_task = suite.injection_tasks[case["injection_task"]] if case["injection_task"] else None
    attacker = load_attack(attack, suite, pipeline)
    if not injection_task:
        injections = {}
    elif injection_text is not None:
        injections = {slot: injection_text for slot in attacker.get_injection_candidates(user_task)}
    else:
        injections = attacker.attack(user_task, injection_task)

    env = user_task.init_environment(suite.load_and_inject_default_environment(injections))
    pre_env = env.model_copy(deep=True)
    try:
        _, _, env, messages, _ = pipeline.query(user_task.PROMPT, FunctionsRuntime(suite.tools), env)
    except AbortAgentError as exc:
        env, messages = exc.task_environment, exc.messages
    output = model_output_from_messages(messages) or []
    stack = functions_stack_trace_from_messages(messages)
    utility = suite._check_task_result(user_task, output, pre_env, env, stack)
    grade = {}
    if injection_task:
        grade["attack_success"] = int(suite._check_task_result(injection_task, output, pre_env, env, stack))
        grade["utility_under_attack"] = int(utility)
    else:
        grade["utility"] = int(utility)
    return grade, messages, injections


class NullAgent(BasePipelineElement):
    def query(self, query, runtime, env=EmptyEnv(), messages=(), extra_args={}):
        reply = ChatAssistantMessage(role="assistant", tool_calls=[],
                                     content=[text_content_block_from_string("I cannot help with that.")])
        return query, runtime, env, [*messages, ChatUserMessage(role="user", content=[text_content_block_from_string(query)]), reply], extra_args


def oracle_pipeline(kind: str, case: dict, suites):
    suite = suites[case["suite"]]
    if kind == "null":
        runner = NullAgent()
    elif kind == "attacker" and case["injection_task"]:
        runner = GroundTruthPipeline(suite.injection_tasks[case["injection_task"]])
    else:
        runner = GroundTruthPipeline(suite.user_tasks[case["user_task"]])
    runner.name = "local"
    return runner


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--variant", default="baseline", help="results folder name under --flow")
    p.add_argument("--agent", choices=list(AGENTS),
                   help="agent code to run: baseline (undefended), v1-v5 (hillclimb rounds; v3 is agent/); "
                        "default: the variant name's prefix")
    p.add_argument("--flow", type=Path, default=FLOW, help="results folder holding _state.json (default results/)")
    p.add_argument("--cases", type=Path, default=CASES, help="case file (default evals/cases.jsonl)")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--split", choices=["all", "train", "val", "test"], default="all")
    p.add_argument("--suites", help="comma-separated suites to keep (default all)")
    p.add_argument("--attack", default="important_instructions")
    p.add_argument("--limit", type=int)
    p.add_argument("--attacked-only", action="store_true",
                   help="skip benign cases, which no attack template changes (template tuning runs and comparisons)")
    p.add_argument("--benign-only", action="store_true", help="skip attacked cases (benign drift controls)")
    p.add_argument("--where", metavar="FIELD", help="keep only cases whose FIELD is true in the case file (e.g. tune)")
    p.add_argument("--adaptive", type=int, metavar="K",
                   help="best-of-K adaptive attack: attempt 1 is --seed-variant's row, attempts 2-K are "
                        "injections written by the attacker model (evals/adaptive.py); rep 0 only")
    p.add_argument("--seed-variant", help="variant in the same flow whose rows are attempt 1 of --adaptive")
    p.add_argument("--attacker-url", help="OpenAI-compatible server of the attacker model (--adaptive)")
    p.add_argument("--attacker-model", default="gemma4:12b-it-qat")
    p.add_argument("--attacker-prompt", type=Path, help="attacker system prompt (default evals/attacker_prompt.md)")
    p.add_argument("--shard", default="0/1", help="i/N: run every N-th case starting at i, for parallel workers")
    p.add_argument("--timeout-s", type=int, default=600)
    p.add_argument("--base-url", default="http://localhost:8000/v1")
    p.add_argument("--model", default="mlx-community/Qwen3.5-4B-MLX-4bit")
    p.add_argument("--context-tokens", type=int, default=32768, help="the server's context window")
    p.add_argument("--system-prompt", type=Path,
                   help="use this system prompt instead of agent/system_prompt.md (judge calibration runs only)")
    p.add_argument("--defense", help="run one of AgentDojo's built-in defences (evals/defenses.py) instead of agent/")
    p.add_argument("--oracle", choices=["user", "attacker", "null"],
                   help="grader check: replace the model with the user task's ground truth, the attacker's "
                        "ground truth, or an agent that only replies 'I cannot help with that.'")
    p.add_argument("--dry-run", action="store_true", help="print the resolved scope and exit")
    p.add_argument("--approve-harness", action="store_true")
    args = p.parse_args(argv)
    if args.adaptive and not (args.seed_variant and args.attacker_url):
        p.error("--adaptive needs --seed-variant and --attacker-url")
    if args.adaptive and (args.reps != 1 or args.adaptive < 2):
        p.error("--adaptive runs rep 0 only, with K >= 2")
    return args


def select_cases(args: argparse.Namespace) -> list[dict]:
    cases = [json.loads(line) for line in args.cases.read_text().splitlines()]
    if args.split != "all":
        cases = [c for c in cases if c["split"] == args.split]
    if args.suites:
        keep = args.suites.split(",")
        unknown = set(keep) - set(SUITES)
        if unknown:
            sys.exit(f"unknown suites: {sorted(unknown)}")
        cases = [c for c in cases if c["suite"] in keep]
    if args.attacked_only or args.adaptive:
        cases = [c for c in cases if c["injection_task"]]
    if args.benign_only:
        cases = [c for c in cases if not c["injection_task"]]
    if args.where:
        cases = [c for c in cases if c.get(args.where)]
    cases = cases[: args.limit] if args.limit else cases
    shard, n_shards = map(int, args.shard.split("/"))
    return cases[shard::n_shards]


class Runner:
    """Runs one case once and writes its row, trace and error records under <flow>/<variant>/."""

    def __init__(self, args: argparse.Namespace, suites, pipeline, llm) -> None:
        self.args, self.suites, self.pipeline, self.llm = args, suites, pipeline, llm
        self.vdir = args.flow / args.variant
        (self.vdir / "traces").mkdir(parents=True, exist_ok=True)
        self.results, self.errors = self.vdir / "results.jsonl", self.vdir / "errors.jsonl"

    def attempt(self, case: dict, rep: int, injection_text: str | None = None, extra: dict | None = None
                ) -> tuple[dict | None, list[dict] | None]:
        """One run of a case: (row, trace), or (None, None) after recording why it produced nothing scorable."""
        args, llm = self.args, self.llm
        llm.calls = []
        runner = oracle_pipeline(args.oracle, case, self.suites) if args.oracle else self.pipeline
        t0 = time.monotonic()
        signal.alarm(args.timeout_s)
        try:
            grade, messages, injections = run_case(case, self.suites, runner, args.attack, injection_text)
            failure = None
        except CaseTimeout:
            failure = "timeout"
        except ServerError as exc:
            failure = f"serving_error: {exc}"
        except Exception as exc:  # noqa: BLE001 - anything else is a harness bug, never a score
            failure = f"harness_error: {type(exc).__name__}: {exc}"
        finally:
            signal.alarm(0)
        wall = round(time.monotonic() - t0, 1)
        served = {c["model"] for c in llm.calls}
        if not failure and served - {args.model}:
            failure = f"served_model_mismatch: {sorted(served)}"
        usage = {"input_tokens": sum(c["input_tokens"] for c in llm.calls),
                 "output_tokens": sum(c["output_tokens"] for c in llm.calls),
                 "cache_read_input_tokens": sum(c["cached_tokens"] for c in llm.calls)}
        if failure:
            self.error(case, rep, failure, usage, wall, extra)
            return None, None

        trace = to_trace(messages)
        truncated = any(c["finish_reason"] == "length" or c["context_full"] for c in llm.calls)
        row = {
            "prompt_id": case["id"], "rep": rep, "split": case["split"], "tags": case["tags"],
            "prompt": self.suites[case["suite"]].user_tasks[case["user_task"]].PROMPT,
            "grade": grade, "status": "truncated" if truncated else "ok",
            "stop_reason": llm.calls[-1]["finish_reason"] if llm.calls else None,
            "model": f"oracle-{args.oracle}" if args.oracle else args.model, "latency_s": wall,
            "tool_calls": sum(t["role"] == "tool_call" for t in trace),
            "malformed_calls": sum(c["malformed_tool_call"] for c in llm.calls),
            "usage": usage,
            "meta": {"suite": case["suite"], "user_task": case["user_task"],
                     "injection_task": case["injection_task"],
                     "attack": ("adaptive_llm" if injection_text is not None else args.attack) if injections else None,
                     "defense": args.defense,
                     "attacker_goal": (self.suites[case["suite"]].injection_tasks[case["injection_task"]].GOAL
                                       if case["injection_task"] else None),
                     "injections": injections, **(extra or {})},
        }
        return row, trace

    def error(self, case: dict, rep: int, failure: str, usage: dict | None = None, wall: float | None = None,
              extra: dict | None = None) -> None:
        with self.errors.open("a") as f:
            f.write(json.dumps({"prompt_id": case["id"], "rep": rep, "failure": failure, "model": self.args.model,
                                "usage": usage, "wall_s": wall, **(extra or {})}) + "\n")
        print(f"{case['id']} rep {rep} ERROR {failure}", flush=True)

    def write(self, row: dict, trace: list[dict]) -> None:
        (self.vdir / "traces" / f"{row['prompt_id']}_rep{row['rep']}.json").write_text(json.dumps(trace, indent=1))
        with self.results.open("a") as f:
            f.write(json.dumps(row) + "\n")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    state = load_state(args.flow)
    if args.approve_harness:
        state["harness_sha"] = harness_sha(state)
        (args.flow / "_state.json").write_text(json.dumps(state, indent=2) + "\n")
        print("harness approved:", state["harness_sha"][:12])
        return
    if "harness_sha" not in state:
        print("warning: harness gate not armed (no --approve-harness yet)", flush=True)
    elif state["harness_sha"] != harness_sha(state):
        sys.exit("harness changed since last --approve-harness; review the diff, then record it with --approve-harness")

    cases = select_cases(args)
    vdir = args.flow / args.variant
    results = vdir / "results.jsonl"
    done = set()
    if results.exists():
        done = {(r["prompt_id"], r["rep"]) for r in map(json.loads, results.read_text().splitlines())}
    todo = [(c, rep) for c in cases for rep in range(args.reps) if (c["id"], rep) not in done]
    print(f"{args.variant}: {len(todo)} to run ({len(cases)} cases x {args.reps} reps, {len(done)} done), "
          f"model={args.model}, attack={args.attack}, split={args.split}, cases={args.cases.name}"
          + (f", adaptive best-of-{args.adaptive} seeded by {args.seed_variant}" if args.adaptive else ""), flush=True)
    if args.dry_run:
        return

    suites = get_suites("v1.2.1")
    if args.defense:
        from evals.defenses import build_defense_pipeline
        pipeline, llm = build_defense_pipeline(args.defense, args.base_url, args.model, args.context_tokens)
    else:
        build_pipeline = importlib.import_module(AGENTS[agent_for(args.variant, args.agent or ("baseline" if args.oracle else None))]).build_pipeline
        pipeline, llm = build_pipeline(args.base_url, args.model, args.context_tokens,
                                       args.system_prompt.read_text() if args.system_prompt else None)
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(CaseTimeout()))
    runner = Runner(args, suites, pipeline, llm)
    if args.adaptive:
        from evals.adaptive import PROMPT, Attacker, run_adaptive
        run_adaptive(runner, todo, Attacker(args.attacker_url, args.attacker_model, args.attacker_prompt or PROMPT))
        return

    started = time.monotonic()
    for i, (case, rep) in enumerate(todo, 1):
        row, trace = runner.attempt(case, rep)
        if row is None:
            continue
        runner.write(row, trace)
        left = (time.monotonic() - started) / i * (len(todo) - i)
        print(f"[{i}/{len(todo)}] {case['id']} {row['grade']} {row['latency_s']}s ~{left / 60:.0f}m left", flush=True)


if __name__ == "__main__":
    main()
