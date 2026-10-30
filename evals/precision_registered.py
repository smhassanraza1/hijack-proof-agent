"""Expected 95% interval half-widths for the registered evaluation's test design, simulated from development per-case outcomes (no model).

Design simulated: all 158 verified test x test pairs (development test users x test goals) + 47 benign test
tasks, R reps per variant. Unrun pairs take the joint (baseline, defence) outcome of a random
observed pair with the same attacker goal (keeps paired correlation and goal heterogeneity).
Run noise: each rep flips a case's outcome with prob f (f from the measured flip rate between two
identical runs: 2f(1-f) = flip). Intervals: evals/stats.cluster_interval (two-way cluster bootstrap,
the reported interval) and evals/stats.conditional_interval (run noise only: the cases are fixed).

    python -m evals.precision_registered > registration/precision.out
"""
import json
import math
from pathlib import Path

import numpy as np

from evals.stats import cluster_interval, conditional_interval
from evals.summarize import load

R = Path(__file__).resolve().parent.parent

SIMS, DRAWS = 120, 2000
rng = np.random.default_rng(1)
cases = [json.loads(line) for line in (R / "evals/cases_registered.jsonl").read_text().splitlines()]
TEST_PAIRS = sorted(c["id"] for c in cases if c["split"] == "test" and c["injection_task"])
BENIGN = sorted(c["id"] for c in cases if c["split"] == "test" and not c["injection_task"])
def goal(c: str) -> str:
    return c.split(".")[0] + "." + c.split(".")[2]
print(f"test pairs {len(TEST_PAIRS)}, goals {len({goal(c) for c in TEST_PAIRS})}, users {len({c.rsplit('.',1)[0] for c in TEST_PAIRS})}; benign {len(BENIGN)}")

def flip_p(rate):  # per-rep flip prob from the two-run disagreement rate
    return (1 - math.sqrt(1 - 2 * rate)) / 2

def observed(base_v, def_v, metric, ids):
    B, D = load(base_v, "test"), load(def_v, "test")
    out = {}
    for c in ids:
        if c in B and c in D and metric in B[c] and metric in D[c]:
            out[c] = (B[c][metric], D[c][metric])
    return out

def fill(obs, ids):
    """Each id -> (p_base, p_def): observed, else a random observed pair with the same goal."""
    by_goal = {}
    for c, v in obs.items():
        by_goal.setdefault(goal(c), []).append(v)
    allv = list(obs.values())
    out = {}
    for c in ids:
        if c in obs:
            out[c] = obs[c]
        else:
            donors = by_goal.get(goal(c)) or allv
            out[c] = donors[rng.integers(len(donors))]
    return out

def sim_once(truth, reps, f, modify=None):
    ids = sorted(truth)
    pb = np.array([truth[c][0] for c in ids])
    pd = np.array([truth[c][1] for c in ids])
    if modify is not None:
        pd = modify(ids, pb, pd)
    def draw(p):
        base = (rng.random((len(p), reps)) < p[:, None]).astype(float)  # p in [0,1] (rep means from development)
        flips = rng.random(base.shape) < f
        return np.where(flips, 1 - base, base)
    xb, xd = draw(pb), draw(pd)
    diff = xd.mean(1) - xb.mean(1)
    lo, hi = cluster_interval(dict(zip(ids, diff)), draws=DRAWS, seed=int(rng.integers(1e9)))
    # conditional: run noise only (cases fixed), the registered pooled run-noise model with the true f
    _, clo, chi = conditional_interval({c: list(xd[k]) for k, c in enumerate(ids)},
                                       {c: list(xb[k]) for k, c in enumerate(ids)}, f=f, f_base=f)
    return diff.mean(), lo, hi, clo, chi

def report(name, truth, f, reps_list=(1, 2, 3), modify=None, margin=None):
    for reps in reps_list:
        res = np.array([sim_once(truth, reps, f, modify) for _ in range(SIMS)])
        m, lo, hi, clo, chi = res.T
        hw = (hi - lo) / 2
        chw = (chi - clo) / 2
        excl0 = np.mean((hi < 0) | (lo > 0))
        line = (f"{name:58s} R={reps} n={len(truth):3d} delta={np.mean(m):+.3f} "
                f"cluster half-width={np.median(hw):.3f} (p10-p90 {np.percentile(hw,10):.3f}-{np.percentile(hw,90):.3f}) "
                f"P(cluster CI excl 0)={excl0:.2f}")
        if margin is not None:
            line += f" P(lower>{-margin:+.2f})={np.mean(lo > -margin):.2f}"
        line += f" | conditional half-width={np.nanmedian(chw):.3f} P(cond excl 0)={np.mean((chi<0)|(clo>0)):.2f}"
        print(line, flush=True)

F15, F01 = flip_p(0.15), flip_p(0.013)
if __name__ == "__main__":
    print(f"per-rep flip prob: 15% pair flip -> {F15:.3f}; 1.3% -> {F01:.3f}")

    # Development reality check: the 80 observed pairs, as run (no simulation)
    for bv, dv in [("baseline", "v3"), ("baseline-tool_knowledge", "v3-tool_knowledge")]:
        o = observed(bv, dv, "attack_success", TEST_PAIRS)
        d = {c: v[1] - v[0] for c, v in o.items()}
        lo, hi = cluster_interval(d)
        print(f"hillclimb as run {dv}: n={len(d)} delta={np.mean(list(d.values())):+.3f} CI [{lo:+.3f},{hi:+.3f}] hw={(hi-lo)/2:.3f}")

    print("\n== attack success, v3 - undefended, 158 test pairs ==")
    for bv, dv, tag in [("baseline", "v3", "important_instructions"), ("baseline-tool_knowledge", "v3-tool_knowledge", "tool_knowledge")]:
        obs = observed(bv, dv, "attack_success", TEST_PAIRS)
        for f, ft in [(F15, "flip15%"), (F01, "flip1%")]:
            truth = fill(obs, TEST_PAIRS)
            report(f"ASR {tag} {ft}", truth, f)
        # only the 80 observed pairs, for comparison with the development design
        report(f"ASR {tag} flip15% (80 dev-run pairs only)", obs, F15, reps_list=(1, 3))

    print("\n== attack success, round 5 - round 3 (hypothetical: round 5 stops get_webpage goal; else = round 3) ==")
    obs = observed("baseline-tool_knowledge", "v3-tool_knowledge", "attack_success", TEST_PAIRS)
    truth = fill(obs, TEST_PAIRS)
    truth_v5 = {c: (v[1], v[1]) for c, v in truth.items()}  # base := v3, def := v3 then modified
    def stop_webpage(ids, pb, pd):
        pd = pd.copy()
        for k, c in enumerate(ids):
            if c.startswith("slack.") and c.endswith("injection_task_3"):
                pd[k] = 0.05
        return pd
    report("ASR v5-v3 tk (inj3 -> 5%) flip15%", truth_v5, F15, modify=stop_webpage)
    ung = {c: v for c, v in truth_v5.items() if c.startswith("slack.") and c.endswith("injection_task_3")}
    report("ASR v5-v3 tk, slack inj3 pairs only", ung, F15, modify=stop_webpage)

    print("\n== utility under attack, v3 - undefended ==")
    for bv, dv, tag in [("baseline", "v3", "ii"), ("baseline-tool_knowledge", "v3-tool_knowledge", "tk")]:
        obs = observed(bv, dv, "utility_under_attack", TEST_PAIRS)
        report(f"UuA {tag} flip11%", fill(obs, TEST_PAIRS), flip_p(0.114))

    print("\n== benign utility, 47 test tasks ==")
    for bv, dv, tag in [("baseline", "v3", "v3 ii-run"), ("baseline-tool_knowledge", "v3-tool_knowledge", "v3 tk-run")]:
        obs = observed(bv, dv, "utility", BENIGN)
        report(f"utility {tag} flip2%", obs, flip_p(0.021), margin=0.10)
        report(f"utility {tag} flip2% (margin 0.15)", obs, flip_p(0.021), reps_list=(2, 3), margin=0.15)
    for dv in ["_def_tool_filter", "_def_transformers_pi_detector", "_def_repeat_user_prompt"]:
        o = observed("v3", dv, "utility", BENIGN)
        report(f"utility {dv} - v3", o, flip_p(0.021), reps_list=(1, 2))

    print("\n== false alarms (benign), v3 - undefended ==")
    obs = observed("baseline", "v3", "false_alarm", BENIGN)
    report("false_alarm v3", obs, 0.0)
