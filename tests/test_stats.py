"""The interval arithmetic behind every table, and the notice that counts as a false alarm."""
import numpy as np

from agent.action_gate import _notice
from agent.variants.v4.action_gate import _notice as v4_notice
from evals.stats import cluster_interval, cluster_pvalue, conditional_interval, flip_probability, holm
from evals.summarize import NOTICE


def cases(n_tasks: int, n_goals: int, value) -> dict[str, float]:
    return {f"slack.user_task_{t}.injection_task_{g}": value(t, g) for t in range(n_tasks) for g in range(n_goals)}


def width(vals: dict[str, float]) -> float:
    lo, hi = cluster_interval(vals)
    return hi - lo


def test_interval_contains_the_mean_and_is_reproducible():
    vals = cases(10, 8, lambda t, g: float((t * 7 + g * 3) % 5 == 0))
    lo, hi = cluster_interval(vals)
    mean = sum(vals.values()) / len(vals)
    assert lo <= mean <= hi and (lo, hi) == cluster_interval(vals)


def test_independent_cases_match_the_normal_approximation():
    rng = np.random.default_rng(0)
    vals = {f"slack.user_task_{i}.none": float(rng.random() < 0.3) for i in range(400)}
    p = sum(vals.values()) / len(vals)
    half = 1.96 * np.sqrt(p * (1 - p) / len(vals))
    assert abs(width(vals) / 2 - half) < 0.01


def test_benign_cases_across_suites_resample_only_user_tasks():
    rng = np.random.default_rng(1)
    vals = {f"{suite}.user_task_{i}.none": float(rng.random() < 0.8)
            for suite in ("workspace", "travel", "banking", "slack") for i in range(100)}
    p = sum(vals.values()) / len(vals)
    half = 1.96 * np.sqrt(p * (1 - p) / len(vals))
    assert abs(width(vals) / 2 - half) < 0.01


def test_shared_goals_widen_the_interval():
    # Outcome fixed by the attacker goal: 80 cases but only 8 independent units.
    clustered = cases(10, 8, lambda t, g: float(g < 3))
    independent = {f"slack.user_task_{i}.injection_task_{i}": v for i, v in enumerate(clustered.values())}
    assert width(clustered) > 1.5 * width(independent)


def test_level_defaults_to_95_and_widens_at_97_5():
    vals = cases(10, 8, lambda t, g: float((t + g) % 3 == 0))
    assert cluster_interval(vals) == cluster_interval(vals, level=0.95)
    lo, hi = cluster_interval(vals)
    lo2, hi2 = cluster_interval(vals, level=0.975)
    assert lo2 <= lo and hi2 >= hi


def test_pvalue_agrees_with_the_interval():
    shifted = cases(12, 8, lambda t, g: -0.3 + 0.1 * ((t * g) % 3))
    centred = cases(12, 8, lambda t, g: (t - g) % 3 - 1.0)
    for vals in (shifted, centred):
        lo, hi = cluster_interval(vals)
        assert (cluster_pvalue(vals) < 0.05) == (lo > 0 or hi < 0)


def test_holm_steps_down():
    assert holm({"a": 0.01, "b": 0.04}) == {"a": True, "b": True}
    assert holm({"a": 0.03, "b": 0.04}) == {"a": False, "b": False}
    assert holm({"a": 0.001, "b": 0.06}) == {"a": True, "b": False}


def test_flip_probability_inverts_two_run_disagreement():
    f = 0.1
    disagree = 2 * f * (1 - f)  # 0.18: 18 of 100 cases disagree
    runs = {f"c{i}": [0, 1] if i < 18 else [1, 1] for i in range(100)}
    assert abs(flip_probability(runs) - f) < 1e-9 and abs(disagree - 0.18) < 1e-12


def test_conditional_interval_covers_single_runs_and_agreeing_runs():
    two = {f"c{i}": [1, 1] if i % 2 else [0, 0] for i in range(40)} | {"x": [0, 1], "y": [1, 0]}
    est, lo, hi = conditional_interval(two)
    assert lo < est < hi  # cases whose two runs agree still carry the pooled noise
    one = {c: r[:1] for c, r in two.items()}
    _, lo1, hi1 = conditional_interval(one, f=flip_probability(two))
    assert hi1 - lo1 > hi - lo  # one run per case is noisier than two
    est_d, lo_d, hi_d = conditional_interval(two, base=two)
    assert est_d == 0 and lo_d < 0 < hi_d


def test_notice_marker_matches_the_defence():
    assert _notice([("send_email", ("a@b.org",))]).startswith(NOTICE)
    assert v4_notice([("send_email", ("a@b.org",))]).startswith(NOTICE)
