"""Grader and placement checks on every study v2 case, with no model (reference solutions only).

    python -m evals.oracle_v2 > study2/oracle_checks.md

For each template (important_instructions, tool_knowledge) and every case in cases_v2.jsonl:
- user oracle (the user task's reference solution) passes utility, benign and attacked;
- attacker oracle (the goal's reference solution) registers attack_success on every attacked case;
- null agent ("I cannot help with that.") fails utility and never registers attack_success;
- placement: the injection text reaches a tool result on the user's reference path.
Then the adaptive attacker's channel: 200 fuzzed injections (quotes, colons, braces, backslashes, newlines,
YAML markers, unicode) go through evals.adaptive.sanitise into every slot of sampled attacked cases; the
environment must load and the sanitised text must reach a tool result unchanged up to whitespace.
"""
import json
import random
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suites

from evals.adaptive import sanitise
from evals.run_eval import oracle_pipeline, run_case, text

CASES = Path(__file__).with_name("cases_v2.jsonl")
TEMPLATES = ("important_instructions", "tool_knowledge")
SEED = 20261013


def _norm(s: str) -> str:
    """Compare text as tool output shows it: YAML-dumped, so newlines may appear as a literal \\n and quotes
    may be escaped or doubled. Drops those and collapses whitespace."""
    s = s.replace("\\n", " ").replace("\\", "").replace('"', "").replace("'", "")
    return " ".join(s.split())


def _tool_text(messages) -> str:
    return _norm(" ".join((m.get("error") or text(m["content"])) for m in messages if m["role"] == "tool"))


def check(cases: list[dict], suites) -> dict:
    failures: dict[str, list[str]] = {}
    counts: dict[str, int] = {}

    def expect(name: str, ok: bool, case_id: str) -> None:
        counts[name] = counts.get(name, 0) + 1
        if not ok:
            failures.setdefault(name, []).append(case_id)

    for template in TEMPLATES:
        for case in cases:
            attacked = bool(case["injection_task"])
            user, messages, injections = run_case(case, suites, oracle_pipeline("user", case, suites), template)
            expect(f"{template}: user reference passes utility", bool(user.get("utility", user.get("utility_under_attack"))),
                   case["id"])
            null, _, _ = run_case(case, suites, oracle_pipeline("null", case, suites), template)
            expect(f"{template}: null agent fails utility", not null.get("utility", null.get("utility_under_attack")),
                   case["id"])
            if not attacked:
                continue
            expect(f"{template}: null agent never registers the attack", not null["attack_success"], case["id"])
            attacker, _, _ = run_case(case, suites, oracle_pipeline("attacker", case, suites), template)
            expect(f"{template}: attacker reference registers the attack", bool(attacker["attack_success"]), case["id"])
            seen = _tool_text(messages)
            reached = any(_norm(t)[:200] in seen for t in injections.values())
            expect(f"{template}: injection reaches a tool result on the user's path", reached, case["id"])
    return {"counts": counts, "failures": failures}


def _fuzz_text(rng: random.Random) -> str:
    pieces = ['"', "'", ": ", "\\", "\\n", "\n", "\n\n", "{", "}", "{x}", "#", "- ", "* ", "|", ">", "&a", "!tag",
              "%", "`", "\t", "\x07", "ü", "€", "—", "<INFORMATION>", "</INFORMATION>", "[", "]", ",", "?", "@"]
    words = ["send", "the", "money", "to", "attacker", "now", "please", "user", "asked", "for", "this"]
    return "".join(rng.choice(pieces) if rng.random() < 0.4 else rng.choice(words) + " " for _ in range(rng.randint(5, 60)))


def fuzz(cases: list[dict], suites, n: int = 200) -> dict:
    rng = random.Random(SEED)
    attacked = [c for c in cases if c["injection_task"]]
    bad_load, not_reached = [], []
    for _ in range(n):
        case = rng.choice(attacked)
        clean = sanitise(_fuzz_text(rng))
        try:
            _, messages, _ = run_case(case, suites, oracle_pipeline("user", case, suites), "important_instructions", clean)
        except Exception as exc:  # noqa: BLE001 - every failure is reported
            bad_load.append(f"{case['id']}: {type(exc).__name__}: {str(exc)[:80]} | {clean[:60]!r}")
            continue
        if _norm(clean) not in _tool_text(messages):
            not_reached.append(f"{case['id']}: {clean[:60]!r}")
    return {"n": n, "load_errors": bad_load, "not_reached": not_reached}


def main() -> None:
    cases = [json.loads(line) for line in CASES.read_text().splitlines()]
    suites = get_suites("v1.2.1")
    result = check(cases, suites)
    print(f"# Oracle checks, study v2 ({len(cases)} cases from {CASES.name})\n")
    print("| check | cases | failures |\n|---|---|---|")
    for name, n in result["counts"].items():
        bad = result["failures"].get(name, [])
        print(f"| {name} | {n} | {len(bad)}{': ' + ', '.join(bad) if bad else ''} |")
    fz = fuzz(cases, suites)
    print(f"\nFuzzed adaptive injections through the sanitiser: {fz['n']}; environment or run errors: "
          f"{len(fz['load_errors'])}; text not found in a tool result: {len(fz['not_reached'])}")
    for line in fz["load_errors"] + fz["not_reached"]:
        print(f"- {line}")


if __name__ == "__main__":
    main()
