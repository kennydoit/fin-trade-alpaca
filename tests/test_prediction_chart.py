import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from yfinance.screener.predict_short_term import save_actual_vs_predicted_chart


def test_save_actual_vs_predicted_chart(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "actual_ret": [0.01, 0.02, -0.01, 0.03],
            "pred_ret": [0.015, 0.018, -0.005, 0.025],
        }
    )

    out_path = tmp_path / "actual_vs_predicted.png"
    result = save_actual_vs_predicted_chart(df, out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 0
