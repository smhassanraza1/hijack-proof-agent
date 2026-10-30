"""The README figure: attack success against normal-task utility for each defence, registered evaluation (test split).

    python -m evals.figure      # writes docs/figures/defences.png
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from evals import report_registered  # noqa: E402
from evals.stats import cluster_interval  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "figures" / "defences.png"
DEFENCES = [  # arm, label, label offset in points
    ("baseline-ii", "no defence", (-62, 3)),
    ("_def_spotlighting_with_delimiting", "spotlighting", (8, 3)),
    ("_def_repeat_user_prompt", "repeat prompt", (-74, 3)),
    ("_def_tool_filter", "tool filter", (8, 3)),
    ("_def_transformers_pi_detector", "PI classifier", (8, -10)),
    ("v3-ii", "this project (round 3)", (8, -12)),
]


def rate(arm: str, metric: str) -> tuple[float, float, float]:
    runs = report_registered.scores(report_registered.FLOW, arm, metric, report_registered.test_split)
    vals = {cid: sum(v) / len(v) for cid, v in runs.items()}
    mean = sum(vals.values()) / len(vals)
    lo, hi = cluster_interval(vals)
    return mean, mean - lo, hi - mean


def main() -> None:
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=200)
    for arm, label, offset in DEFENCES:
        x, xlo, xhi = rate(arm, "utility")
        y, ylo, yhi = rate(arm, "attack_success")
        ours = arm == "v3-ii"
        colour = "#c2410c" if ours else ("#111827" if arm == "baseline-ii" else "#6b7280")
        ax.errorbar(x * 100, y * 100, xerr=[[xlo * 100], [xhi * 100]], yerr=[[ylo * 100], [yhi * 100]],
                    fmt="o", color=colour, ecolor=colour, elinewidth=0.8, capsize=2, markersize=6 if ours else 5,
                    alpha=0.9)
        ax.annotate(label, (x * 100, y * 100), textcoords="offset points", xytext=offset, fontsize=8.5,
                    color=colour, fontweight="bold" if ours else "normal")
    ax.set_xlabel("utility on normal tasks (%)  →  better")
    ax.set_ylabel("attack success (%)  ↓  better")
    ax.set_xlim(20, 100)
    ax.set_ylim(0, 70)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.25, linewidth=0.5)
    ax.set_title("Qwen3.5-4B on AgentDojo, held-out test cases (95% clustered interval of each rate)", fontsize=9.5, loc="left")
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
