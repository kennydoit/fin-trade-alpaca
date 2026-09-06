"""Optional diagnostic charting for the prediction pipeline.

Isolated from model training so environments without matplotlib (e.g. AWS
Lambda, where it's dropped to stay under the deployment package size limit)
can still import and run the training/scoring code paths.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore

    MATPLOTLIB_INSTALLED = True
except Exception:
    MATPLOTLIB_INSTALLED = False


def save_actual_vs_predicted_chart(eval_df: pd.DataFrame, out_path: Path) -> Path | None:
    """Save an actual-vs-predicted return scatter plot to disk.

    Returns None (without raising) if matplotlib is not installed - this keeps
    the prediction pipeline usable in minimal environments (e.g. AWS Lambda)
    that omit matplotlib to stay under deployment package size limits.
    """
    if not MATPLOTLIB_INSTALLED:
        return None

    plot_df = pd.DataFrame(
        {
            "actual_ret": pd.to_numeric(eval_df.get("actual_ret", pd.Series(dtype=float)), errors="coerce"),
            "pred_ret": pd.to_numeric(eval_df.get("pred_ret", pd.Series(dtype=float)), errors="coerce"),
        }
    ).dropna()

    if plot_df.empty:
        raise ValueError("No actual/predicted values available to plot")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(plot_df["actual_ret"], plot_df["pred_ret"], alpha=0.75)

    min_val = min(plot_df["actual_ret"].min(), plot_df["pred_ret"].min())
    max_val = max(plot_df["actual_ret"].max(), plot_df["pred_ret"].max())
    span = max(max_val - min_val, 1e-6)
    ax.plot(
        [min_val - 0.05 * span, max_val + 0.05 * span],
        [min_val - 0.05 * span, max_val + 0.05 * span],
        "r--",
        linewidth=1,
        label="Ideal fit",
    )

    ax.set_xlabel("Actual forward return")
    ax.set_ylabel("Predicted forward return")
    ax.set_title("Actual vs Predicted Returns")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return out_path
