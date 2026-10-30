"""cases_registered.jsonl has the registered design and agrees with development's cases.jsonl.

Rebuilding the file runs every reference solution (about a minute: `python -m evals.cases --registered`);
these checks only read it.
"""
import json
from collections import Counter
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1] / "evals"


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (EVALS / name).read_text().splitlines()]


REGISTERED, DEVELOPMENT = load("cases_registered.jsonl"), load("cases.jsonl")


def part(split: str, attacked: bool) -> list[dict]:
    return [c for c in REGISTERED if c["split"] == split and bool(c["injection_task"]) == attacked]


def test_split_sizes():
    assert (len(part("train", True)), len(part("train", False))) == (80, 0)
    assert (len(part("val", True)), len(part("val", False))) == (54, 46)
    assert (len(part("test", True)), len(part("test", False))) == (158, 47)
    assert len({c["id"] for c in REGISTERED}) == len(REGISTERED)


def test_test_design():
    test = part("test", True)
    assert Counter(c["suite"] for c in test) == {"workspace": 55, "travel": 40, "slack": 33, "banking": 30}
    assert sum(not c["pilot_run"] for c in test) == 78
    assert len({(c["suite"], c["injection_task"]) for c in test}) == 15
    assert len({(c["suite"], c["user_task"]) for c in test}) == 47
    assert Counter(f"{c['suite']}.{c['injection_task']}" for c in test if c["ungated"]) == {
        "slack.injection_task_3": 11, "travel.injection_task_6": 10}
    assert sum(c["tool_reuse"] for c in test) == 15
    assert sum(c["tool_reuse"] and not c["pilot_run"] for c in test) == 7


def test_development_cases_keep_their_split():
    by_id = {c["id"]: c for c in REGISTERED}
    for c in DEVELOPMENT:
        reg = by_id[c["id"]]
        assert reg["pilot_split"] == c["split"] and reg["pilot_run"]
        assert reg["split"] == ("test" if c["split"] == "test" else ("train" if c["injection_task"] else "val"))
    assert sum(c["pilot_run"] for c in REGISTERED) == len(DEVELOPMENT)


def side(case: dict) -> str:
    return "test" if case["split"] == "test" else "train"


def test_no_goal_or_user_task_crosses_splits():
    users, goals = {}, {}
    for c in REGISTERED:
        assert users.setdefault((c["suite"], c["user_task"]), side(c)) == side(c)
        if c["injection_task"]:
            assert goals.setdefault((c["suite"], c["injection_task"]), side(c)) == side(c)


def test_attacker_tuning_pairs_are_train():
    tune = [c for c in REGISTERED if c["tune"]]
    assert len(tune) == 40 and all(c["split"] == "train" and c["injection_task"] for c in tune)
