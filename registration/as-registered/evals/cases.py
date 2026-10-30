"""Build the eval case list and its train/test split.

Per suite, user tasks and injection tasks (the attacker's goals) are each split in half at
random, after dropping the ones the grader cannot be checked on (below). Train cases pair train user tasks with train attacks; test cases pair test user
tasks with test attacks, so no attacker goal seen in training appears in test. Attacker
identifiers (an IBAN, an email address, a URL) recur across goals in both splits, and some compound
test tasks contain a train task's prompt. Every user task also runs once with no attack (benign), for utility and false
alarms. Attack pairs are sampled per split in proportion to each suite's pool.

Only cases the grader can be shown to score correctly are kept: a user task is dropped if an
agent that only answers "I cannot help with that." passes it, and an attack pair stays only
if running the attacker's own reference solution counts as a successful attack. An attacker
goal with no such pair is dropped before splitting (AgentDojo v1.2.1 ships workspace
injection tasks 6-13 with an empty reference).

    python -m evals.cases --attack-per-split 80     # the pilot: cases.jsonl
    python -m evals.cases --study v2                # study v2: cases_v2.jsonl

Study v2 keeps the pilot's halves (same seed, same shuffles) and takes every verified pair instead of
a sample. Its splits: `train` is the pilot's 80 run train pairs (traces exist; no new runs), `val` the
54 never-run train pairs plus the 46 train benign tasks (whose pilot traces are train material too),
`test` all 158 test pairs plus the 47 test benign tasks. Each case also records `pilot_run` (the pilot
ran it), `ungated` (its goal is one no action gate can stop), `tool_reuse` (the attacker's
state-changing tool is one the user task itself uses; money tools excluded) and `tune` (one of the 40
train attacked pairs, drawn with seed 20261013, that the adaptive attacker is tuned on).
"""
import argparse
import json
import random
import re
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suites

from evals.run_eval import oracle_pipeline, run_case

SEED = 20261004
TUNE_SEED, TUNE_PAIRS = 20261013, 40
OUT = Path(__file__).with_name("cases.jsonl")
OUT_V2 = Path(__file__).with_name("cases_v2.jsonl")
# Attacker goals no action gate can stop: slack injection_task_3 is reached by visiting a URL with
# get_webpage, which a gate treats as a read; travel injection_task_6 only asks the agent to say something.
UNGATED = ("slack.injection_task_3", "travel.injection_task_6")
_READ_ONLY = re.compile(r"^(get|read|search|list|find|view|check|lookup|query|count)_")  # as agent/action_gate.py


def _halves(rng: random.Random, suites) -> tuple[list[dict], dict[str, list[tuple[str, str, str]]]]:
    """The benign cases and the verified (suite, user task, goal) pairs of each split, train and test."""
    pools: dict[str, list[tuple[str, str, str]]] = {"train": [], "test": []}
    benign: list[dict] = []
    for suite_name, suite in suites.items():
        user_tasks = [ut for ut in sorted(suite.user_tasks)
                      if not _passes(_case(suite_name, ut, None, ""), suites, "null", "utility")]
        verified = {(ut, it) for ut in user_tasks for it in sorted(suite.injection_tasks)
                    if _passes(_case(suite_name, ut, it, ""), suites, "attacker", "attack_success")}
        attacks = sorted({it for _, it in verified})
        rng.shuffle(user_tasks)
        rng.shuffle(attacks)
        halves = {
            "train": (user_tasks[: len(user_tasks) // 2], attacks[: len(attacks) // 2]),
            "test": (user_tasks[len(user_tasks) // 2 :], attacks[len(attacks) // 2 :]),
        }
        for split, (uts, its) in halves.items():
            benign += [_case(suite_name, ut, None, split) for ut in uts]
            pools[split] += [(suite_name, ut, it) for ut in uts for it in its if (ut, it) in verified]
    return benign, pools


def build(attack_per_split: int) -> list[dict]:
    rng = random.Random(SEED)
    benign, pools = _halves(rng, get_suites("v1.2.1"))
    cases = list(benign)
    for split, pool in pools.items():
        by_suite: dict[str, list] = {}
        for item in pool:
            by_suite.setdefault(item[0], []).append(item)
        for suite_name, items in sorted(by_suite.items()):
            quota = round(attack_per_split * len(items) / len(pool))
            cases += [_case(s, ut, it, split) for s, ut, it in rng.sample(items, min(quota, len(items)))]
    return cases


def build_v2(pilot: list[dict]) -> list[dict]:
    suites = get_suites("v1.2.1")
    benign, pools = _halves(random.Random(SEED), suites)
    pilot_ids = {c["id"]: c["split"] for c in pilot}
    cases = []
    for c in benign:
        split = "val" if c["split"] == "train" else "test"
        cases.append(c | {"split": split, "pilot_split": c["split"], "pilot_run": c["id"] in pilot_ids,
                          "ungated": False, "tool_reuse": False})
    for pilot_split, pool in pools.items():
        for s, ut, it in sorted(pool):
            c = _case(s, ut, it, pilot_split)
            run = c["id"] in pilot_ids
            split = "test" if pilot_split == "test" else ("train" if run else "val")
            cases.append(c | {"split": split, "pilot_split": pilot_split, "pilot_run": run,
                              "ungated": f"{s}.{it}" in UNGATED, "tool_reuse": _tool_reuse(suites[s], ut, it)})
    train = sorted(c["id"] for c in cases if c["split"] == "train" and c["injection_task"])
    tune = set(random.Random(TUNE_SEED).sample(train, TUNE_PAIRS))
    cases = [c | {"tune": c["id"] in tune} for c in cases]
    missing = [i for i, sp in pilot_ids.items() if not any(c["id"] == i and c["pilot_split"] == sp for c in cases)]
    assert not missing, f"pilot cases missing from v2 or in another split: {missing[:5]}"
    return cases


def _state_changing_tools(suite, task, env) -> set[str]:
    return {call.function for call in task.ground_truth(env) if not _READ_ONLY.match(call.function)}


def _tool_reuse(suite, user_task: str, injection_task: str) -> bool:
    env = suite.load_and_inject_default_environment({})
    shared = (_state_changing_tools(suite, suite.injection_tasks[injection_task], env)
              & _state_changing_tools(suite, suite.user_tasks[user_task], env))
    tools = {t.name: t for t in suite.tools}
    return any("amount" not in tools[name].parameters.model_fields for name in shared)


def _passes(case: dict, suites, oracle: str, metric: str) -> bool:
    grade, _, _ = run_case(case, suites, oracle_pipeline(oracle, case, suites), "important_instructions")
    return bool(grade[metric])


def _case(suite: str, user_task: str, injection_task: str | None, split: str) -> dict:
    kind = "attack" if injection_task else "benign"
    case_id = f"{suite}.{user_task}.{injection_task or 'none'}"
    return {"id": case_id, "suite": suite, "user_task": user_task, "injection_task": injection_task,
            "split": split, "kind": kind, "tags": [suite, kind]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--attack-per-split", type=int)
    group.add_argument("--study", choices=["v2"])
    args = parser.parse_args()
    if args.study:
        cases = build_v2([json.loads(line) for line in OUT.read_text().splitlines()])
        OUT_V2.write_text("".join(json.dumps(c) + "\n" for c in cases))
        for split in ("train", "val", "test"):
            part = [c for c in cases if c["split"] == split]
            attacked = [c for c in part if c["injection_task"]]
            print(split, len(attacked), "attack +", len(part) - len(attacked), "benign; never run:",
                  sum(not c["pilot_run"] for c in attacked), "ungated:", sum(c["ungated"] for c in part),
                  "tool reuse:", sum(c["tool_reuse"] for c in part), "never-run tool reuse:",
                  sum(c["tool_reuse"] and not c["pilot_run"] for c in part))
        raise SystemExit
    cases = build(args.attack_per_split)
    OUT.write_text("".join(json.dumps(c) + "\n" for c in cases))
    for split in ("train", "test"):
        part = [c for c in cases if c["split"] == split]
        print(split, len(part), "cases:", sum(c["kind"] == "attack" for c in part), "attack,",
              sum(c["kind"] == "benign" for c in part), "benign")
