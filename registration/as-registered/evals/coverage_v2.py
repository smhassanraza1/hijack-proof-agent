"""How often the two-way cluster bootstrap interval covers the truth, on study v2's real test structure.

Offline (no model): the 158 test pairs (47 user tasks x 15 attacker goals, as in cases_v2.jsonl) and
the 47 benign test tasks keep their ids; only the outcomes are simulated. Each simulation draws a fresh
population effect for every goal and task, so the target is the mean over new tasks and goals like
these (the generalisation level the paper reports), computed by Monte Carlo. Outcomes are Bernoulli
per run, averaged over R runs, as in the study.

Attack success, undefended:  logit p = mu_b + G_goal + U_task
Attack success, defended:    logit q = mu_d + c G_goal + H_goal + U_task
Benign utility:              logit p = mu + V_task, defended adds D_task

Goal spread is set from the pilot (per-goal logit SD about 2 undefended, 1 defended). The script
reports the coverage of the nominal 95% and 97.5% intervals for the paired difference and for a single
arm's rate, over a grid around those values; registered rule (TASKS §9): below 90% means registering a
wider level or a studentised interval before stage 1.

    python -m evals.coverage_v2 [--sims 400] > study2/coverage.out
    python -m evals.coverage_v2 --wide >> study2/coverage.out   # goal x defence spread as wide as the pilot's
"""
import argparse
import json
from pathlib import Path

import numpy as np

from evals.stats import cluster_interval

CASES = Path(__file__).with_name("cases_v2.jsonl")
SEED = 20261013
TRUTH_DRAWS = 200_000


def expit(x):
    return 1 / (1 + np.exp(-x))


def structure() -> tuple[list[str], np.ndarray, np.ndarray, list[str]]:
    cases = [json.loads(line) for line in CASES.read_text().splitlines()]
    attacked = sorted(c["id"] for c in cases if c["split"] == "test" and c["injection_task"])
    benign = sorted(c["id"] for c in cases if c["split"] == "test" and not c["injection_task"])
    task = np.unique([c.rsplit(".", 1)[0] for c in attacked], return_inverse=True)[1]
    goal = np.unique([c.split(".")[0] + "." + c.rsplit(".", 1)[1] for c in attacked], return_inverse=True)[1]
    return attacked, task, goal, benign


def attack_world(rng, sg, su, sh, mu_b, mu_d, c, n):
    g, h, u = rng.normal(0, sg, n), rng.normal(0, sh, n), rng.normal(0, su, n)
    return expit(mu_b + g + u), expit(mu_d + c * g + h + u)


def benign_world(rng, sv, sd, mu, shift, n):
    v, d = rng.normal(0, sv, n), rng.normal(0, sd, n)
    return expit(mu + v), expit(mu + v + shift + d)


def run(sims: int, wide: bool = False) -> None:
    rng = np.random.default_rng(SEED)
    attacked, task, goal, benign = structure()
    n_t, n_g = task.max() + 1, goal.max() + 1
    print(f"structure: {len(attacked)} attacked test pairs ({n_t} tasks x {n_g} goals), {len(benign)} benign tasks;"
          f" {sims} simulations per row, bootstrap 4000 draws\n")
    print("| quantity | sd goal | sd task | sd goal x defence | R | truth | cover 95% | cover 97.5% | mean half-width 95% |")
    print("|---|---|---|---|---|---|---|---|---|")
    grid = [(sg, su, sh) for sg in (1.0, 2.0, 3.0) for su in (0.5, 1.0) for sh in (0.5, 1.5)]
    if wide:  # half-widths near the pilot-based ±0.19-0.21 (TASKS §3), so goals vary as much as in the real data
        grid = [(sg, 1.0, sh) for sg in (2.0, 3.0) for sh in (2.5, 3.0)]
    for sg, su, sh in grid:
        for label, mu_b, mu_d, c in (("attack Δ, effect", -0.6, -3.2, 0.5), ("attack Δ, null", -0.6, -0.6, 1.0)):
            p_t, q_t = attack_world(rng, sg, su, sh, mu_b, mu_d, c, TRUTH_DRAWS)
            truth_diff, truth_rate = float(np.mean(q_t - p_t)), float(np.mean(p_t))
            cov = {"diff95": 0, "diff975": 0, "rate95": 0}
            widths = []
            for _ in range(sims):
                g, h, u = rng.normal(0, sg, n_g), rng.normal(0, sh, n_g), rng.normal(0, su, n_t)
                p = expit(mu_b + g[goal] + u[task])
                q = expit(mu_d + c * g[goal] + h[goal] + u[task])
                yb, yd = rng.binomial(2, p) / 2, rng.binomial(2, q) / 2
                diff = dict(zip(attacked, yd - yb))
                lo, hi = cluster_interval(diff)
                lo2, hi2 = cluster_interval(diff, level=0.975)
                rlo, rhi = cluster_interval(dict(zip(attacked, yb)))
                cov["diff95"] += lo <= truth_diff <= hi
                cov["diff975"] += lo2 <= truth_diff <= hi2
                cov["rate95"] += rlo <= truth_rate <= rhi
                widths.append((hi - lo) / 2)
            print(f"| {label} | {sg} | {su} | {sh} | 2 | {truth_diff:+.3f} | {cov['diff95'] / sims:.1%} "
                  f"| {cov['diff975'] / sims:.1%} | {np.mean(widths):.3f} |")
            if label.endswith("null"):
                print(f"| attack rate, undefended | {sg} | {su} | – | 2 | {truth_rate:.3f} | {cov['rate95'] / sims:.1%} | – | – |")
    for sv, sd, shift in () if wide else ((1.5, 0.5, -0.4), (1.5, 1.5, -0.4), (2.5, 1.5, -0.4), (1.5, 0.5, 0.0)):
        p_t, q_t = benign_world(rng, sv, sd, 1.0, shift, TRUTH_DRAWS)
        truth = float(np.mean(q_t - p_t))
        hits = hits2 = 0
        for _ in range(sims):
            v, d = rng.normal(0, sv, len(benign)), rng.normal(0, sd, len(benign))
            p, q = expit(1.0 + v), expit(1.0 + v + shift + d)
            diff = dict(zip(benign, (rng.binomial(2, q) - rng.binomial(2, p)) / 2))
            lo, hi = cluster_interval(diff)
            lo2, hi2 = cluster_interval(diff, level=0.975)
            hits += lo <= truth <= hi
            hits2 += lo2 <= truth <= hi2
        print(f"| benign utility Δ | – | {sv} | {sd} (task x defence) | 2 | {truth:+.3f} | {hits / sims:.1%} "
              f"| {hits2 / sims:.1%} | – |")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sims", type=int, default=400)
    parser.add_argument("--wide", action="store_true")
    args = parser.parse_args()
    run(args.sims, args.wide)
