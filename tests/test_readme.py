"""The README's numbers match what the report scripts compute from results/.

Every table row in the README is rebuilt here and must appear verbatim, so a stale rate, difference,
interval or star fails; the numbers quoted in the prose are checked the same way. The hillclimb and the
first stress tests come from evals.report (results/), the registered evaluation from evals.report_registered
(results/registered/).
"""
import contextlib
import io
import json
import re
from functools import cache
from pathlib import Path

from evals import report, report_registered
from evals.run_eval import FLOW

README = Path(__file__).resolve().parents[1] / "README.md"
CELL = re.compile(r"(\d+)% \[(-?\d+)%, (-?\d+)%\] n=\d+(?: Δ([+-]\d+)% \[([+-]\d+)%, ([+-]\d+)%\](\*?))?")
REGISTERED = report_registered.FLOW


def report_text(*args: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        report.main(list(args))
    return out.getvalue()


def report_rows(*args: str) -> dict[str, list[str]]:
    """Row label -> cells, for every metric table the report prints (the first occurrence wins)."""
    rows: dict[str, list[str]] = {}
    for line in report_text(*args).split("by whether a gate can act")[0].splitlines():
        if line.startswith("| ") and not line.startswith("| variant"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.setdefault(cells[0], cells[1:])
    return rows


def report_gate_rows() -> dict[str, tuple[str, str]]:
    rows = {}
    for line in report_text().split("by whether a gate can act")[1].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| ") and len(cells) == 3 and cells[0] != "variant":
            rows[cells[0]] = (cells[1], cells[2])
    return rows


def num(x: str) -> str:
    v = int(x)
    return "0" if v == 0 else (f"+{v}" if v > 0 else f"−{-v}")


def rate(cell: str) -> str:
    return cell.split()[0]


def full(cell: str, bold: bool = False) -> str:
    """A report cell as the README writes it: rate (difference [low, high]*)."""
    m = CELL.match(cell)
    assert m and m.group(4), cell
    r = f"**{m.group(1)}%**" if bold else f"{m.group(1)}%"
    return f"{r} ({num(m.group(4))} [{num(m.group(5))}, {num(m.group(6))}]{m.group(7)})"


def interval(cell: str) -> str:
    m = CELL.match(cell)
    assert m, cell
    return f"{m.group(1)}% [{num(m.group(2)).lstrip('+')}, {num(m.group(3)).lstrip('+')}]"


def text() -> str:
    return " ".join(README.read_text().split())


# The registered evaluation, from evals.report_registered's own functions.

def pct(x: float) -> str:
    return f"{report_registered.fmt(x)}%"


def points(x: float) -> str:
    return num(report_registered.fmt(x, True))


def on_test(cid: str) -> bool:
    return report_registered.test_split(cid)


def subset(field: str, value: bool = True):
    info = report_registered.case_info()
    return lambda cid: report_registered.test_split(cid) and info[cid][field] == value


@cache
def compare(arm: str, base: str, metric: str, level: float = 0.95, keep=on_test) -> dict:
    return report_registered.compare(REGISTERED, arm, base, metric, keep, level)


def diff(c: dict) -> str:
    star = "*" if c["lo"] > 0 or c["hi"] < 0 else ""
    return f"{points(c['diff'])} [{points(c['lo'])}, {points(c['hi'])}]{star}"


def cond(c: dict) -> str:
    return f"[{points(c['cond_lo'])}, {points(c['cond_hi'])}]"


def own_rate(arm: str, metric: str) -> str:
    s = report_registered.scores(REGISTERED, arm, metric, report_registered.test_split)
    return pct(sum(sum(v) / len(v) for v in s.values()) / len(s))


@cache
def claim_levels() -> dict[str, float]:
    """Holm levels of the two primary claims, as evals.report_registered.primary sets them."""
    c1 = [compare("v3-ii", "baseline-ii", "attack_success"), compare("v3-tk", "baseline-tk", "attack_success")]
    c2 = [compare("v3-ii", "_def_tool_filter", "utility"), compare("v3-ii", "_def_transformers_pi_detector", "utility")]
    p = {"C1": report_registered.claim_p(c1, -1), "C2": report_registered.claim_p(c2, +1)}
    order = sorted(p, key=lambda name: p[name])
    return {name: 1 - report_registered.ALPHA / (len(order) - i) for i, name in enumerate(order)}


def registered_report() -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        report_registered.main([])
    return out.getvalue()


def test_table_rows_match_the_report():
    t = report_rows()
    rows = []
    for template in ("tool_knowledge", "injecagent"):
        b, v = t[f"baseline-{template}"], t[f"v3-{template}"]
        rows.append(f"| {template} | undefended | " + " | ".join(rate(c) for c in b[:5]) + " |")
        rows.append("| | **defended (round 3)** | " + " | ".join(full(c, i == 0) for i, c in enumerate(v[:5])) + " |")
    for template in ("defense_aware", "adaptive_judge"):
        b, v = t[f"baseline-{template}"], t[f"v3-{template}"]
        rows.append(f"| {template} | undefended | {rate(b[0])} | {rate(b[2])} | {rate(b[3])} |")
        rows.append(f"| | round 3 | {full(v[0])} | {full(v[2])} | {full(v[3])} |")
    rows.append("| start | undefended (2 runs) | " + " | ".join(rate(c) for c in t["baseline"][:3]) + " |")
    for round_ in ("v1", "v2", "v3", "v4"):
        rows.append("| " + " | ".join(full(c) for c in t[round_][:3]) + " |")
    gate = {"baseline": "undefended", "v3": "round 3", "baseline-tool_knowledge": "undefended, tool_knowledge",
            "v3-tool_knowledge": "round 3, tool_knowledge", "v4-tool_knowledge": "round 4, tool_knowledge"}
    gate_rows = report_gate_rows()
    for variant, label in gate.items():
        gated, ungated = gate_rows[variant]
        shown = rate(gated) if variant.startswith("baseline") else interval(gated)
        rows.append(f"| {label} | {shown} | {rate(ungated)} |")
    readme = README.read_text()
    missing = [r for r in rows if r not in readme]
    assert not missing, "\n".join(missing)


def test_registered_rows_match_the_report():
    level = claim_levels()
    claims = [
        ("C1", "attack success, important_instructions", "undefended",
         compare("v3-ii", "baseline-ii", "attack_success", level["C1"])),
        ("C1", "attack success, tool_knowledge", "undefended",
         compare("v3-tk", "baseline-tk", "attack_success", level["C1"])),
        ("C2", "benign utility", "tool filter", compare("v3-ii", "_def_tool_filter", "utility", level["C2"])),
        ("C2", "benign utility", "PI classifier",
         compare("v3-ii", "_def_transformers_pi_detector", "utility", level["C2"])),
        ("S1", "benign utility", "undefended", compare("v3-ii", "baseline-ii", "utility")),
        ("S2", "false alarms (code-scored)", "undefended", compare("v3-ii", "baseline-ii", "false_alarm_code")),
        ("S4", "attack success, automated attacker (best of 4)", "undefended",
         compare("v3-adaptive", "baseline-adaptive", "attack_success")),
    ]
    rows = [f"| {claim} | {what} | {other} | {c['n']} | {pct(c['rate_base'])} | {pct(c['rate'])} | {diff(c)} | {cond(c)} |"
            for claim, what, other, c in claims]
    rows.append("| none | " + " | ".join(own_rate("baseline-ii", m) for m in
                                         ("attack_success", "utility", "utility_under_attack")) + " |")
    arms = {"_def_spotlighting_with_delimiting": "spotlighting (delimit tool output)",
            "_def_repeat_user_prompt": "repeat the user's prompt after tool output",
            "_def_tool_filter": "tool filter (model picks its tools from the request)",
            "_def_transformers_pi_detector": "prompt-injection classifier on tool output (DeBERTa)",
            "v3-ii": "**plan-then-gate (round 3)**"}
    for arm, label in arms.items():
        cells = []
        for metric in ("attack_success", "utility", "utility_under_attack"):
            c = compare(arm, "baseline-ii", metric)
            r = pct(c["rate"])
            cells.append(f"{f'**{r}**' if arm == 'v3-ii' and metric == 'attack_success' else r} ({diff(c)})")
        rows.append(f"| {label} | " + " | ".join(cells) + " |")
    subsets = {"pairs never run before": subset("pilot_run", False), "pairs used during development": subset("pilot_run"),
               "gated goals": subset("ungated", False), "ungated goals": subset("ungated"),
               "attack reuses a tool the request asks for": subset("tool_reuse")}
    for label, keep in subsets.items():
        c = compare("v3-ii", "baseline-ii", "attack_success", keep=keep)
        rows.append(f"| {label} | {c['n']} | {pct(c['rate_base'])} | {pct(c['rate'])} | {diff(c)} |")
    readme = README.read_text()
    missing = [r for r in rows if r not in readme]
    assert not missing, "\n".join(missing)


def flip_rate() -> float:
    def outcomes(rep: int) -> dict[str, int]:
        rows = [json.loads(line) for line in (FLOW / "baseline" / "results.jsonl").read_text().splitlines()]
        return {r["prompt_id"]: r["grade"]["attack_success"] for r in rows
                if r["rep"] == rep and r["split"] == "test" and r["status"] == "ok" and "attack_success" in r["grade"]}
    a, b = outcomes(0), outcomes(1)
    return sum(a[c] != b[c] for c in a.keys() & b.keys()) / len(a.keys() & b.keys())


def test_prose_numbers_match_the_report():
    t = report_rows()
    runs = (rate(t["v3-tool_knowledge (run 1)"][0]), rate(t["v3-tool_knowledge (run 2)"][0]))
    false_alarms = sorted(int(rate(t[v][4]).rstrip("%")) for v in ("v3", "v3-tool_knowledge", "v3-injecagent"))
    truncated = max(int(c[6]) for c in t.values())
    expected = [
        f"cuts attack success from {rate(t['baseline-tool_knowledge'][0])} to {rate(t['v3-tool_knowledge'][0])} "
        f"(run 1: {runs[0]}, run 2: {runs[1]})",
        f"undefended ({rate(t['baseline-injecagent'][0])}; the AgentDojo paper",
        f"weaker than the stock template ({rate(t['baseline-defense_aware'][0])} on the undefended agent)",
        f"stock `tool_knowledge` ({runs[0]} and {runs[1]} in its two runs)",
        f"told the user {rate(t['v2'][3])} → {rate(t['v3'][3])} vs round 2",
        f"flipped {flip_rate():.0%} of attack outcomes on the test split",
        f"({false_alarms[0]}-{false_alarms[-1]}% of normal tasks)",
        f"(0-{truncated} per row)",
    ]
    missing = [e for e in expected if e not in text()]
    assert not missing, missing


def test_registered_prose_matches_the_report():
    out = " ".join(registered_report().split())
    assert "**C1 confirmed.**" in out and "**C2 confirmed.**" in out
    claim = re.search(r"On (\d+) held-out AgentDojo cases \((\d+) attacker goals, (\d+) user tasks, (\d+) never run", out)
    assert claim, "the registered report no longer states C1"
    n, goals, tasks, never = claim.groups()
    pi = compare("_def_transformers_pi_detector", "baseline-ii", "utility")
    tool_filter = compare("_def_tool_filter", "baseline-ii", "utility")
    benign = compare("v3-ii", "baseline-ii", "utility")
    val = report_registered.compare(REGISTERED, "v5-val", "v3-val", "utility",
                            lambda cid: report_registered.case_info().get(cid, {}).get("split") == "val" and cid.startswith("slack."))
    drift = re.search(r"- baseline: ([\d.]+) tasks of difference", out)
    assert drift, "the registered report no longer has the drift check"
    validation = " ".join(report_registered.validation(REGISTERED))
    visits = re.search(r"\(sent to the judge\) (\d+), refused (\d+)", validation)
    refused = re.search(r"refused benign tasks (\d+) \(round 3: (\d+)\)", validation)
    v3_drift = re.search(r"- v3: ([\d.]+) tasks of difference", out)
    pi_judge = re.search(r"\| _def_transformers_pi_detector \|(?: [^|]+ \|){4} (\d+%)", out)
    assert visits and refused and v3_drift and pi_judge, "the registered report changed its wording"
    assert visits.group(1) == visits.group(2)
    expected = [
        "both registered claims were confirmed",
        f"Every one of the {visits.group(1)} off-request visits went to the check and was refused",
        f"refused normal tasks rose from {refused.group(2)} to {refused.group(1)}",
        f"round 3 moved by {v3_drift.group(1)}",
        f"except the classifier ({pi_judge.group(1)})",
        f"{n} attacked cases ({goals} attacker goals, {tasks} user tasks, {never} of them never run before)",
        f"fails {report_registered.refused_every_run(REGISTERED, 'v3-ii')} of {benign['n']} tasks in every run",
        f"utility falls from {pct(pi['rate_base'])} to {pct(pi['rate'])}",
        f"this 4B model falls to {pct(tool_filter['rate'])}",
        f"benign utility {pct(val['rate_base'])} → {pct(val['rate'])}",
        f"the undefended agent moved by {drift.group(1)} tasks",
    ]
    missing = [e for e in expected if e not in text()]
    assert not missing, missing


def test_counting_truncated_runs_changes_one_star():
    def stars(rows):
        return {(v, i): "*" in c for v, cells in rows.items() for i, c in enumerate(cells[:5])}
    default, counted = stars(report_rows()), stars(report_rows("--include-truncated"))
    assert [k for k in default if default[k] != counted[k]] == [("v3", 1)]  # round 3, utility
    cell = full(report_rows("--include-truncated")["v3"][1])
    assert f"counting them changes one *: round 3's loss of normal-task utility in the hillclimb becomes significant ({cell.split(' (', 1)[1]}" in text()
