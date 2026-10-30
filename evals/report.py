"""Every results table in the README, rebuilt from results/ with no model or GPU.

    python -m evals.report [--include-truncated]

Each variant is paired with the baseline run under the same attack template. With
--include-truncated, truncated runs count too (sensitivity analysis).
"""
import argparse

from evals.run_eval import FLOW
from evals.stats import cluster_interval
from evals.summarize import METRICS, load, summarize

HEADER = ["variant", *METRICS, "malformed", "truncated", "errors", "s/case"]
TEMPLATES = ["tool_knowledge", "injecagent", "defense_aware", "adaptive_judge"]
HILLCLIMB = ["baseline", "v1", "v2", "v3", "v4"]
COMPARISON = ["baseline", "v3", "_def_spotlighting_with_delimiting", "_def_repeat_user_prompt",
              "_def_tool_filter", "_def_transformers_pi_detector"]
# Attacker goals no action gate can stop: slack injection_task_3 is reached by visiting a URL with
# get_webpage, which the gate treats as a read; travel injection_task_6 only asks the agent to say
# something. 10 of the 80 attacked test cases, none of the train cases.
UNGATED = ("slack.injection_task_3", "travel.injection_task_6")


def ungated(case_id: str) -> bool:
    suite, _, goal = case_id.split(".")
    return f"{suite}.{goal}" in UNGATED


def table(title: str, variants: list[str], base_variant: str, include_truncated: bool) -> None:
    base = load(base_variant, "test", include_truncated)
    print(f"\n{title}  (test split; Δ = paired difference vs {base_variant}, * = interval excludes zero)")
    print("| " + " | ".join(HEADER) + " |\n|" + "---|" * len(HEADER))
    for v in variants:
        if (FLOW / v / "results.jsonl").exists():
            print(summarize(v, "test", base if v != base_variant else None, include_truncated))


def runs_table(template: str, include_truncated: bool) -> None:
    """The two runs of the headline pair separately, each paired with the baseline run made alongside it."""
    print(f"\nEach run separately: {template}  (run 1 = rep 0, run 2 = rep 1)")
    print("| " + " | ".join(HEADER) + " |\n|" + "---|" * len(HEADER))
    for reps in ((0,), (1,)):
        base = load(f"baseline-{template}", "test", include_truncated, reps)
        print(summarize(f"baseline-{template}", "test", None, include_truncated, reps))
        print(summarize(f"v3-{template}", "test", base, include_truncated, reps))


def gate_split(include_truncated: bool) -> None:
    """Gated goals get a clustered interval; the two ungated goals are too few clusters for one."""
    print("\nAttack success by whether a gate can act on the goal  (test split, attacked cases)")
    print("| variant | gated goals | ungated goals |\n|---|---|---|")
    for v in ["baseline", "v3", "v4", "baseline-tool_knowledge", "v3-tool_knowledge", "v4-tool_knowledge",
              "baseline-adaptive_judge", "v3-adaptive_judge"]:
        cases = load(v, "test", include_truncated)
        gated = {c: x["attack_success"] for c, x in cases.items() if "attack_success" in x and not ungated(c)}
        other = [x["attack_success"] for c, x in cases.items() if "attack_success" in x and ungated(c)]
        lo, hi = cluster_interval(gated)
        print(f"| {v} | {sum(gated.values()) / len(gated):.0%} [{lo:.0%}, {hi:.0%}] n={len(gated)} "
              f"| {sum(other) / len(other):.0%} n={len(other)} |")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--include-truncated", action="store_true")
    inc = p.parse_args(argv).include_truncated
    table("Hillclimb: important_instructions", HILLCLIMB, "baseline", inc)
    for template in TEMPLATES:
        table(f"Unseen template: {template}", [f"{a}-{template}" for a in ("baseline", "v3", "v4")],
              f"baseline-{template}", inc)
    runs_table("tool_knowledge", inc)
    table("Comparison defences: important_instructions", COMPARISON, "baseline", inc)
    gate_split(inc)


if __name__ == "__main__":
    main()
