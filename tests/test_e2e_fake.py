"""The registered evaluation's harness end to end against a scripted server: rows, resume, failures, adaptive loop, judge.

No model runs. Each test writes to its own temporary flow with an unarmed _state.json.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from evals import judge, run_eval
from tests.fake_server import FakeServer

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "cases_registered.jsonl"
MODEL = "fake-model"


@pytest.fixture
def server():
    s = FakeServer(MODEL)
    yield s
    s.close()


@pytest.fixture
def flow(tmp_path):
    (tmp_path / "_state.json").write_text("{}")
    return tmp_path


def run(flow: Path, server: FakeServer, variant: str, *extra: str) -> None:
    run_eval.main(["--flow", str(flow), "--cases", str(CASES), "--variant", variant, "--base-url", server.url,
                   "--model", MODEL, *extra])


def rows(flow: Path, variant: str, name: str = "results.jsonl") -> list[dict]:
    path = flow / variant / name
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize("agent", ["baseline", "v3", "v4", "v5"])
@pytest.mark.parametrize("split", ["val", "test"])
@pytest.mark.parametrize("attack", ["important_instructions", "tool_knowledge"])
def test_rows_are_complete(flow, server, agent, split, attack):
    run(flow, server, "x", "--agent", agent, "--split", split, "--attack", attack, "--limit", "1")
    run(flow, server, "x", "--agent", agent, "--split", split, "--attack", attack, "--attacked-only", "--limit", "1")
    out = rows(flow, "x")
    assert len(out) == 2 and {r["split"] for r in out} == {split}
    benign, attacked = sorted(out, key=lambda r: "attack_success" in r["grade"])
    assert set(benign["grade"]) == {"utility"} and set(attacked["grade"]) == {"attack_success", "utility_under_attack"}
    for r in out:
        assert r["model"] == MODEL and r["usage"]["input_tokens"] > 0 and r["status"] == "ok"
        trace = json.loads((flow / "x" / "traces" / f"{r['prompt_id']}_rep0.json").read_text())
        assert trace[0]["role"] == "system" and trace[-1]["role"] == "assistant"
    assert attacked["meta"]["attack"] == attack and attacked["meta"]["injections"]


def test_resume_after_a_kill_leaves_no_duplicates(flow, server):
    server.delay_s = 0.3
    cmd = [sys.executable, "-m", "evals.run_eval", "--flow", str(flow), "--cases", str(CASES), "--variant", "k",
           "--agent", "baseline", "--split", "val", "--limit", "6", "--base-url", server.url, "--model", MODEL]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    while time.time() < deadline and len(rows(flow, "k")) < 2:
        time.sleep(0.1)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    first = len(rows(flow, "k"))
    assert 2 <= first < 6
    server.delay_s = 0
    subprocess.run(cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    keys = [(r["prompt_id"], r["rep"]) for r in rows(flow, "k")]
    assert len(keys) == 6 == len(set(keys))


def test_a_slow_reply_is_a_timeout_not_a_score(flow, server):
    server.delay_s = 3
    run(flow, server, "t", "--agent", "baseline", "--split", "val", "--limit", "1", "--timeout-s", "1")
    assert rows(flow, "t") == []
    assert [e["failure"] for e in rows(flow, "t", "errors.jsonl")] == ["timeout"]


def test_a_different_served_model_is_an_error(flow, server):
    server.served_model = "someone-else"
    run(flow, server, "m", "--agent", "baseline", "--split", "val", "--limit", "1")
    assert rows(flow, "m") == []
    assert rows(flow, "m", "errors.jsonl")[0]["failure"].startswith("served_model_mismatch")


def test_adaptive_attempts_resume_and_record(flow, server):
    run(flow, server, "seed", "--agent", "v3", "--split", "test", "--attack", "tool_knowledge", "--attacked-only",
        "--limit", "2")
    assert all(not r["grade"]["attack_success"] for r in rows(flow, "seed"))
    adaptive = ["--agent", "v3", "--split", "test", "--attack", "tool_knowledge", "--limit", "2", "--adaptive", "3",
                "--seed-variant", "seed", "--attacker-url", server.url, "--attacker-model", MODEL,
                "--attacker-prompt", str(ROOT / "evals" / "attacker_prompts" / "informed.md")]
    run(flow, server, "adapt", *adaptive)
    out, attempts = rows(flow, "adapt"), rows(flow, "adapt", "attempts.jsonl")
    assert len(out) == 2 and all(r["attempt"] == 3 for r in out)
    assert sorted((a["prompt_id"], a["attempt"]) for a in attempts) == sorted(
        (r["prompt_id"], k) for r in out for k in (1, 2, 3))
    for r in out:
        assert r["meta"]["attack"] == "adaptive_llm" and len(r["meta"]["adaptive"]["attempts"]) == 3
        assert r["meta"]["adaptive"]["attempts"][1]["attacker"]["usage"]["output_tokens"] > 0
        for k in (1, 2, 3):
            assert (flow / "adapt" / "traces" / f"{r['prompt_id']}_rep0_attempt{k}.json").exists()
    injected = [a["injection"] for a in attempts if a["attempt"] > 1]
    assert all(i.startswith("Before the task") for i in injected)
    run(flow, server, "adapt", *adaptive)
    assert len(rows(flow, "adapt")) == 2 and len(rows(flow, "adapt", "attempts.jsonl")) == 6


def test_judge_strips_the_notice_and_writes_model_keys(flow, server, monkeypatch):
    run(flow, server, "j", "--agent", "v3", "--split", "val", "--limit", "1")
    trace_path = next((flow / "j" / "traces").iterdir())
    trace = json.loads(trace_path.read_text())
    trace[-1]["content"] += "\n\nNot done: I did not carry out the following, because your request did not ask for it."
    trace_path.write_text(json.dumps(trace))
    monkeypatch.setattr(sys, "argv", ["judge", "--flow", str(flow), "--variant", "j", "--model", MODEL,
                                      "--base-url", server.url, "--strip-notice"])
    judge.main()
    row = rows(flow, "j")[0]
    assert row["grade"]["model_false_alarm"] == 0 and "false_alarm" not in row["grade"]
    judged = [r for r in server.requests if r["messages"][0]["content"].startswith("You grade one transcript")]
    assert judged and "Not done:" not in judged[-1]["messages"][1]["content"]


def adaptive_args(attacker_url: str) -> list[str]:
    return ["--agent", "v3", "--split", "test", "--attack", "tool_knowledge", "--limit", "1", "--adaptive", "3",
            "--seed-variant", "seed", "--attacker-url", attacker_url, "--attacker-model", MODEL,
            "--attacker-prompt", str(ROOT / "evals" / "attacker_prompts" / "informed.md")]


def test_an_attacker_that_refuses_still_finishes_every_case(flow, server):
    run(flow, server, "seed", "--agent", "v3", "--split", "test", "--attack", "tool_knowledge", "--attacked-only",
        "--limit", "1")
    server.attacker_reply = "I can't help with that."
    run(flow, server, "refuse", *adaptive_args(server.url))
    out, attempts = rows(flow, "refuse"), rows(flow, "refuse", "attempts.jsonl")
    assert len(out) == 1 and out[0]["attempt"] == 3
    assert [a["source"] for a in attempts] == ["seed", "attacker_empty", "attacker_empty"]
    assert attempts[1]["injection"] == attempts[0]["injection"] and attempts[1]["attacker"]["asked"] == 2


def test_attacker_reply_without_usage_is_recorded_not_a_crash(flow, server):
    run(flow, server, "seed", "--agent", "v3", "--split", "test", "--attack", "tool_knowledge", "--attacked-only",
        "--limit", "1")
    server.send_usage = False
    run(flow, server, "nousage", *adaptive_args(server.url))
    attempts = rows(flow, "nousage", "attempts.jsonl")
    assert len(rows(flow, "nousage")) == 1 and attempts[1]["attacker"]["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_a_dead_attacker_server_is_an_error_and_resumable(flow, server):
    run(flow, server, "seed", "--agent", "v3", "--split", "test", "--attack", "tool_knowledge", "--attacked-only",
        "--limit", "1")
    run(flow, server, "dead", *adaptive_args("http://127.0.0.1:9/v1"))
    assert rows(flow, "dead") == []
    assert rows(flow, "dead", "errors.jsonl")[0]["failure"].startswith("attacker_error")
    run(flow, server, "dead", *adaptive_args(server.url))
    assert len(rows(flow, "dead")) == 1
