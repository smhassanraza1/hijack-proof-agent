"""Fail if a change copies literal content from test cases.

    python -m evals.leak_check <file> [<file> ...]      # e.g. a round's change.patch

Collects identifying strings (emails, IBANs, URLs, quoted phrases, long numbers, capitalised
names) from the test split's user requests and attacker goals, minus any that also appear in
the train split, and reports each one found in the given files. Exit status 1 on a hit.
"""
import json
import re
import sys
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suites

CASES = Path(__file__).with_name("cases.jsonl")
PATTERNS = [
    r"[\w.+-]+@[\w-]+\.[\w.]+",            # emails
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b",    # IBANs
    r"\b(?:https?://)?(?:www\.)?[\w-]+\.(?:com|org|net|io)[\w/.-]*",  # URLs
    r"'[^']{4,60}'|\"[^\"]{4,60}\"",        # quoted phrases
    r"\b\d{4,}\b",                          # long numbers
    r"\b[A-Z][a-z]+ [A-Z][a-z]+\b",         # two-word names
]


def strings(split: str) -> set[str]:
    suites = get_suites("v1.2.1")
    found: set[str] = set()
    for case in map(json.loads, CASES.read_text().splitlines()):
        if case["split"] != split:
            continue
        suite = suites[case["suite"]]
        texts = [suite.user_tasks[case["user_task"]].PROMPT]
        if case["injection_task"]:
            texts.append(suite.injection_tasks[case["injection_task"]].GOAL)
        for text in texts:
            for pattern in PATTERNS:
                found.update(m.strip("'\" ") for m in re.findall(pattern, text))
    return found


def main() -> None:
    test_only = strings("test") - strings("train")
    hits = []
    for path in sys.argv[1:]:
        text = Path(path).read_text()
        hits += [(path, s) for s in sorted(test_only) if s in text]
    for path, s in hits:
        print(f"LEAK {path}: {s!r}")
    print(f"checked {len(sys.argv) - 1} file(s) against {len(test_only)} test-only strings: {len(hits)} hit(s)")
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()
