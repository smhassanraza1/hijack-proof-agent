"""evals/report_registered on simulated registered-evaluation rows: tables render and the registered wording follows the data."""
import json
from pathlib import Path

import numpy as np

from evals import report_registered

CASES = [json.loads(line) for line in report_registered.CASES.read_text().splitlines()]
TEST = [c for c in CASES if c["split"] == "test"]


def write_arm(flow: Path, variant: str, p_attack: float, p_utility: float, reps: int, seed: int,
              attacked_only: bool = False, notice_tasks: int = 0) -> None:
    rng = np.random.default_rng(seed)
    goal_shift = {g: rng.normal(0, 0.15) for g in sorted({(c["suite"], c["injection_task"]) for c in TEST if c["injection_task"]})}
    (flow / variant / "traces").mkdir(parents=True)
    rows = []
    benign_seen = 0
    for c in TEST:
        if attacked_only and not c["injection_task"]:
            continue
        for rep in range(reps):
            if c["injection_task"]:
                p = min(1, max(0, p_attack + goal_shift[(c["suite"], c["injection_task"])]))
                grade = {"attack_success": int(rng.random() < p), "utility_under_attack": int(rng.random() < 0.5)}
                reply = "Done."
            else:
                refuse = benign_seen < notice_tasks
                grade = {"utility": 0 if refuse else int(rng.random() < p_utility), "model_false_alarm": 0}
                reply = "Done." + ("\n\n" + report_registered.NOTICE + ", because ..." if refuse else "")
            rows.append({"prompt_id": c["id"], "rep": rep, "split": "test", "grade": grade, "status": "ok",
                         "malformed_calls": 0, "latency_s": 10.0})
            trace = [{"role": "user", "content": "x"}, {"role": "assistant", "content": reply}]
            (flow / variant / "traces" / f"{c['id']}_rep{rep}.json").write_text(json.dumps(trace))
        benign_seen += not c["injection_task"]
    (flow / variant / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def build(flow: Path, defended_attack: float, defended_utility: float) -> str:
    write_arm(flow, "baseline-ii", 0.45, 0.75, 2, 1)
    write_arm(flow, "v3-ii", defended_attack, defended_utility, 2, 2, notice_tasks=4)
    write_arm(flow, "baseline-tk", 0.5, 0.75, 1, 3, attacked_only=True)
    write_arm(flow, "v3-tk", defended_attack, defended_utility, 1, 4, attacked_only=True)
    write_arm(flow, "_def_tool_filter", 0.1, 0.35, 1, 5)
    write_arm(flow, "_def_transformers_pi_detector", 0.05, 0.3, 1, 6)
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        report_registered.main(["--flow", str(flow)])
    return out.getvalue()


def test_large_effects_are_confirmed(tmp_path):
    text = build(tmp_path, 0.05, 0.7)
    assert "**C1 confirmed.**" in text and "**C2 confirmed.**" in text
    assert "On 158 held-out AgentDojo cases (15 attacker goals, 47 user tasks, 78 never run before)" in text
    assert "**S2.** Round 3 raises false alarms" in text
    assert "| never-run pairs | 78 |" in text


def test_no_effect_is_not_confirmed(tmp_path):
    text = build(tmp_path, 0.45, 0.35)
    assert "**C1 not confirmed.**" in text and "**C2 not confirmed.**" in text
    assert "C1 confirmed" not in text.replace("C1 not confirmed", "")
