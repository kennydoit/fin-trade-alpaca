import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fin_trade_alpaca.screener_selection import pick_top_n_from_screener
from runners.predict_screener import DEFAULT_PREDICTION_CONFIG, resolve_runtime_config
from yfinance.screener.feature_engineering import (
    build_dataset,
    compute_breakout_target,
    make_breakout_features,
    prepare_features,
)
from yfinance.screener.model_training import train_and_evaluate_breakout
from yfinance.screener.scoring import score_latest_breakout


def _ohlcv(symbols, periods):
    dates = pd.date_range("2024-01-01", periods=periods, freq="B")
    close, high, low, volume = {}, {}, {}, {}
    for i, sym in enumerate(symbols):
        base = 100 + 10 * i + np.cumsum(np.sin(np.arange(periods) / 3.0 + i)) + np.arange(periods) * 0.1
        close[sym] = pd.Series(base, index=dates)
        high[sym] = close[sym] * 1.01
        low[sym] = close[sym] * 0.99
        volume[sym] = pd.Series(1_000_000 + 10_000 * np.arange(periods) % 7919, index=dates, dtype=float)
    return {
        "close": pd.DataFrame(close),
        "high": pd.DataFrame(high),
        "low": pd.DataFrame(low),
        "volume": pd.DataFrame(volume),
    }


def test_breakout_features_are_point_in_time():
    data = _ohlcv(["AAA"], 80)
    full = make_breakout_features(*(data[k]["AAA"] for k in ("close", "high", "low", "volume")))
    cut = 60
    truncated = make_breakout_features(*(data[k]["AAA"].iloc[:cut] for k in ("close", "high", "low", "volume")))

    np.testing.assert_allclose(full.iloc[cut - 1].to_numpy(), truncated.iloc[-1].to_numpy(), equal_nan=True)


def test_compute_breakout_target_uses_volatility_scaled_forward_max():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=4, freq="B"),
            "vol_10d": [0.01, 0.01, 0.0, np.nan],
            "fwd_max_ret": [0.05, 0.01, 0.05, 0.05],
        }
    )

    out = compute_breakout_target(df, return_days=4, breakout_k=2.0)

    # threshold = 2 * 0.01 * sqrt(4) = 0.04; zero/NaN volatility rows are dropped
    assert out["breakout_label"].tolist() == [1.0, 0.0]


def test_compute_breakout_target_requires_breakout_dataset():
    with pytest.raises(ValueError, match="include_breakout"):
        compute_breakout_target(pd.DataFrame({"vol_10d": [0.1]}), return_days=5)


def test_prepare_features_excludes_breakout_label_columns():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "symbol": ["AAA", "AAA"],
            "fwd_ret": [0.01, 0.02],
            "fwd_max_ret": [0.03, 0.04],
            "breakout_label": [0.0, 1.0],
            "breakout_threshold": [0.02, 0.02],
            "ret_1d": [0.1, 0.2],
        }
    )

    _, feat_cols, _ = prepare_features(df)

    assert feat_cols == ["ret_1d"]


def test_build_dataset_breakout_records_future_path_without_leaking_into_features(monkeypatch):
    data = _ohlcv(["AAA"], 45)
    monkeypatch.setattr("yfinance.screener.feature_engineering.download_ohlcv_history", lambda *a, **k: data)

    df = build_dataset(pd.DataFrame({"symbol": ["AAA"]}), ["AAA"], lookback=40, return_days=5, include_breakout=True)

    series = data["close"]["AAA"]
    first = df.iloc[0]
    expected = series.iloc[31:36].max() / series.iloc[30] - 1.0
    assert first["fwd_max_ret"] == pytest.approx(expected)
    assert {"dist_high_20d", "atr_ratio_10_30", "bb_width_rel", "vol_surge_5_20"}.issubset(df.columns)

    _, feat_cols, _ = prepare_features(df)
    assert "fwd_max_ret" not in feat_cols
    assert "fwd_ret" not in feat_cols


def _synthetic_breakout_dataset(seed=0, n_dates=60, n_symbols=8):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    rows = []
    for date in dates:
        for s in range(n_symbols):
            vol = 0.01 + 0.01 * rng.random()
            signal = rng.random()
            threshold = 1.5 * vol * np.sqrt(5)
            rows.append(
                {
                    "date": date,
                    "symbol": f"S{s}",
                    "close": 100.0,
                    "vol_10d": vol,
                    "mom_20d": rng.normal(),
                    "dist_high_20d": signal,
                    "ret_1d": rng.normal(),
                    "fwd_ret": rng.normal(0, 0.02),
                    "fwd_max_ret": threshold * (0.5 + signal + rng.normal(0, 0.2)),
                }
            )
    return pd.DataFrame(rows)


def test_train_and_evaluate_breakout_reports_held_out_classification_metrics():
    df = _synthetic_breakout_dataset()

    model, feat_cols, metrics, _fi, scaler = train_and_evaluate_breakout(
        df, return_days=5, model_config={"type": "random_forest", "n_estimators": 30}, train_fraction=0.7
    )

    assert scaler is None
    assert "close" not in feat_cols
    assert "fwd_max_ret" not in feat_cols
    assert metrics["target_name"] == "breakout_label"
    for key in (
        "roc_auc",
        "average_precision",
        "base_rate_test",
        "precision_top_decile",
        "lift_top_decile",
        "mean_daily_top_decile_hit_rate",
        "mean_daily_ic_vs_fwd_ret",
        "daily_ic_std",
        "baseline_momentum_auc",
        "baseline_proximity_to_high_auc",
        "n_test_dates",
        "min_symbols_per_test_date",
    ):
        assert key in metrics
    assert metrics["n_test_dates"] > 0
    # the signal is genuinely informative in this synthetic set, so held-out AUC should beat chance
    assert metrics["roc_auc"] > 0.6
    assert list(model.classes_) == [0, 1]


def test_train_and_evaluate_breakout_fails_loudly_on_single_class_training_window():
    df = _synthetic_breakout_dataset()
    df["fwd_max_ret"] = -1.0

    with pytest.raises(ValueError, match="single breakout class"):
        train_and_evaluate_breakout(df, return_days=5, model_config={"type": "random_forest", "n_estimators": 5})


class _DummyClassifier:
    classes_ = np.array([0, 1])

    def predict_proba(self, x):
        assert list(x.columns) == ["dist_high_20d", "ret_1d"]
        return np.array([[0.7, 0.3], [0.2, 0.8]])


def test_score_latest_breakout_output_is_probability_not_return(monkeypatch):
    data = _ohlcv(["AAA", "BBB"], 60)
    monkeypatch.setattr("yfinance.screener.scoring.download_ohlcv_history", lambda *a, **k: data)
    cand_df = pd.DataFrame([{"symbol": "AAA", "sector": "Tech"}, {"symbol": "BBB", "sector": "Health"}])

    out = score_latest_breakout(
        _DummyClassifier(), ["dist_high_20d", "ret_1d"], cand_df, ["AAA", "BBB"], 5, metrics={"roc_auc": 0.6}
    )

    assert "pred_ret" not in out.columns
    assert {"breakout_prob", "target_name", "target_semantics", "score_name", "prediction_rank"}.issubset(out.columns)
    assert out["target_name"].eq("breakout_label").all()
    assert out["breakout_prob"].tolist() == [0.8, 0.3]
    assert out["symbol"].tolist() == ["BBB", "AAA"]
    assert out["prediction_rank"].tolist() == [1, 2]


def test_score_latest_breakout_includes_candidate_metadata_features(monkeypatch):
    data = _ohlcv(["AAA"], 60)
    monkeypatch.setattr("yfinance.screener.scoring.download_ohlcv_history", lambda *a, **k: data)
    cand_df = pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "sector": "Tech",
                "industry": "Software",
                "pct_1w": 0.02,
                "pct_1m": 0.05,
                "rel_volume": 1.5,
                "revenueGrowth": 0.12,
                "earningsQuarterlyGrowth": 0.08,
                "pegRatio": 1.2,
                "trailingPE": 20.0,
                "screener_rank": 1,
            }
        ]
    )

    class MetaAwareClassifier:
        classes_ = np.array([0, 1])

        def predict_proba(self, x):
            assert "pct_1w" in x.columns
            assert "rel_volume" in x.columns
            assert x["pct_1w"].tolist() == [0.02]
            return np.array([[0.9, 0.1]])

    out = score_latest_breakout(
        MetaAwareClassifier(),
        ["dist_high_20d", "ret_1d", "pct_1w", "rel_volume", "sector"],
        cand_df,
        ["AAA"],
        5,
    )

    assert out["breakout_prob"].tolist() == [0.1]


def test_score_latest_breakout_fails_loudly_on_missing_features(monkeypatch):
    data = _ohlcv(["AAA"], 60)
    monkeypatch.setattr("yfinance.screener.scoring.download_ohlcv_history", lambda *a, **k: data)

    with pytest.raises(ValueError, match="nonexistent_feature"):
        score_latest_breakout(_DummyClassifier(), ["nonexistent_feature"], pd.DataFrame(), ["AAA"], 5)


def test_score_latest_breakout_returns_empty_without_data(monkeypatch):
    monkeypatch.setattr("yfinance.screener.scoring.download_ohlcv_history", lambda *a, **k: {})

    assert score_latest_breakout(_DummyClassifier(), ["ret_1d"], pd.DataFrame(), ["AAA"], 5).empty


class _Args:
    config = None
    target_type = None


def _runtime(tmp_path, config=None, target_type=None):
    args = _Args()
    if config is not None:
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        args.config = path
    args.target_type = target_type
    return resolve_runtime_config(args)


def test_target_type_defaults_to_ranking_and_does_not_mutate_defaults(tmp_path):
    runtime = _runtime(tmp_path, {"limit": 10})

    assert runtime["target"]["type"] == "ranking"
    runtime["target"]["type"] = "breakout"
    assert DEFAULT_PREDICTION_CONFIG["target"]["type"] == "ranking"


def test_target_type_and_breakout_k_come_from_config_and_cli_overrides(tmp_path):
    cfg = {"target": {"type": "breakout", "breakout_k": 2.5}}

    runtime = _runtime(tmp_path, cfg)
    assert runtime["target"]["type"] == "breakout"
    assert runtime["target"]["breakout_k"] == 2.5

    runtime = _runtime(tmp_path, cfg, target_type="ranking")
    assert runtime["target"]["type"] == "ranking"


def test_invalid_target_type_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unsupported target type"):
        _runtime(tmp_path, {"target": {"type": "nonsense"}})


def test_selection_uses_breakout_prob_when_no_pred_ret(tmp_path):
    path = tmp_path / "breakout_predictions.csv"
    rows = [
        {"symbol": "AAA", "regularMarketPrice": 10.0, "avg_ret": 0.9, "breakout_prob": 0.1},
        {"symbol": "BBB", "regularMarketPrice": 10.0, "avg_ret": 0.1, "breakout_prob": 0.9},
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    picks = pick_top_n_from_screener(path, 1)

    assert [symbol for symbol, _ in picks] == ["BBB"]
