"""Intervals that account for cases sharing a user task or an attacker goal.

The 80 attacked test cases pair about 25 user tasks with 15 attacker goals, so cases that share
either are correlated and an interval that treats them as independent is too narrow. Both
functions here resample user tasks and attacker goals independently with replacement (a two-way
cluster bootstrap) and weight each case by how often its task and its goal were drawn. Benign cases
have no attacker goal, so for them this reduces to resampling user tasks.

Case ids are `<suite>.<user_task>.<injection_task or none>`; clusters are kept within a suite.
"""
import numpy as np

DRAWS = 4000
SEED = 20261007


def _clusters(case_ids: list[str]) -> tuple[np.ndarray, np.ndarray]:
    tasks = [c.rsplit(".", 1)[0] for c in case_ids]
    # Benign cases share one "goal", so for them only user tasks are resampled.
    goals = ["none" if c.endswith(".none") else c.split(".")[0] + "." + c.rsplit(".", 1)[1] for c in case_ids]
    return np.unique(tasks, return_inverse=True)[1], np.unique(goals, return_inverse=True)[1]


def _weights(case_ids: list[str], draws: int, seed: int) -> np.ndarray:
    task_idx, goal_idx = _clusters(case_ids)
    rng = np.random.default_rng(seed)
    n_tasks, n_goals = task_idx.max() + 1, goal_idx.max() + 1
    task_counts = rng.multinomial(n_tasks, np.full(n_tasks, 1 / n_tasks), size=draws)
    goal_counts = rng.multinomial(n_goals, np.full(n_goals, 1 / n_goals), size=draws)
    return task_counts[:, task_idx] * goal_counts[:, goal_idx]


def cluster_interval(values: dict[str, float], draws: int = DRAWS, seed: int = SEED,
                     level: float = 0.95) -> tuple[float, float]:
    """Percentile interval (95% by default) of the mean of per-case values (a rate or a paired difference)."""
    if len(values) < 2:
        return float("nan"), float("nan")
    ids = sorted(values)
    x = np.array([values[c] for c in ids], dtype=float)
    w = _weights(ids, draws, seed)
    total = w.sum(axis=1)
    means = (w @ x)[total > 0] / total[total > 0]
    tail = round(50 * (1 - level), 10)
    lo, hi = np.percentile(means, [tail, 100 - tail])
    return float(lo), float(hi)


def cluster_pvalue(values: dict[str, float], draws: int = DRAWS, seed: int = SEED) -> float:
    """Two-sided bootstrap p-value for "the mean is zero", from the same draws as `cluster_interval`.

    The percentile interval at level L excludes zero exactly when this is below 1 - L (up to ties at zero),
    so a claim can be tested at a Holm-adjusted level by comparing p-values."""
    ids = sorted(values)
    x = np.array([values[c] for c in ids], dtype=float)
    w = _weights(ids, draws, seed)
    total = w.sum(axis=1)
    means = (w @ x)[total > 0] / total[total > 0]
    below, above = np.mean(means <= 0), np.mean(means >= 0)
    return float(min(1.0, 2 * min(below, above)))


def holm(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm's step-down: which hypotheses are rejected at family-wise level alpha."""
    order = sorted(pvalues, key=pvalues.get)
    rejected, still = {}, True
    for i, name in enumerate(order):
        still = still and pvalues[name] <= alpha / (len(order) - i)
        rejected[name] = still
    return rejected


def flip_probability(runs: dict[str, list[int]]) -> float:
    """Per-run flip probability f of a binary outcome, from how often two runs of a case disagree.

    Each run is modelled as the case's outcome flipped with probability f, independently, so two runs
    disagree with probability 2f(1-f). Pooled over every case with two or more runs (first two used)."""
    pairs = [r[:2] for r in runs.values() if len(r) >= 2]
    if not pairs:
        return float("nan")
    disagree = sum(a != b for a, b in pairs) / len(pairs)
    return (1 - np.sqrt(max(0.0, 1 - 2 * disagree))) / 2


def conditional_interval(runs: dict[str, list[int]], base: dict[str, list[int]] | None = None,
                         f: float | None = None, f_base: float | None = None,
                         level: float = 0.95) -> tuple[float, float, float]:
    """Mean (or paired difference with `base`) and its interval from run-to-run noise alone.

    The cases are fixed (inference about these benchmark cases, not new tasks or goals). Each arm's
    per-run flip probability f is pooled over its cases (`flip_probability`, or given), so a case run
    once still gets variance f(1-f), and one whose two runs agree is not treated as noise-free. A case's
    score is the mean of its R runs, with variance f(1-f)/R; the two arms' runs are independent. Normal
    approximation. Returns (estimate, low, high); cases missing from either arm are dropped."""
    from statistics import NormalDist
    ids = sorted(c for c in runs if runs[c] and (base is None or base.get(c)))
    if not ids:
        return float("nan"), float("nan"), float("nan")
    f = flip_probability(runs) if f is None else f
    mean = np.mean([np.mean(runs[c]) for c in ids])
    var = sum(f * (1 - f) / len(runs[c]) for c in ids)
    if base is not None:
        f_base = flip_probability(base) if f_base is None else f_base
        mean -= np.mean([np.mean(base[c]) for c in ids])
        var += sum(f_base * (1 - f_base) / len(base[c]) for c in ids)
    half = NormalDist().inv_cdf(0.5 + level / 2) * np.sqrt(var) / len(ids)
    return float(mean), float(mean - half), float(mean + half)
