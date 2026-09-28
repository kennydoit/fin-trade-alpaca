import numpy as np
import pandas as pd

from yfinance.screener.feature_engineering import build_dataset
from yfinance.screener.model_training import make_time_split
from yfinance.screener.predict_short_term import prepare_features, score_latest


def test_prepare_features_replaces_inf_and_nan_values():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "symbol": ["AAA", "AAA"],
            "fwd_ret": [0.01, -0.02],
            "ret_1d": [np.inf, np.nan],
            "vol_10d": [1.0, 2.0],
            "price_sma10_z": [np.inf, 3.0],
        }
    )

    X, feat_cols, scaler = prepare_features(df)

    assert scaler is None
    assert set(feat_cols) == {"ret_1d", "vol_10d", "price_sma10_z"}
    assert np.isfinite(X.to_numpy()).all()
    assert not np.any(np.isnan(X.to_numpy()))


def test_build_dataset_does_not_copy_latest_snapshot_metadata_to_historical_rows(monkeypatch):
    dates = pd.date_range("2024-01-01", periods=40, freq="B")
    price = pd.DataFrame({"AAA": np.linspace(100, 130, len(dates))}, index=dates)
    monkeypatch.setattr("yfinance.screener.feature_engineering.download_price_history", lambda *args, **kwargs: price)

    cand_df = pd.DataFrame([
        {"symbol": "AAA", "sector": "Technology", "industry": "Software", "screener_rank": 99}
    ])

    df = build_dataset(cand_df, ["AAA"], lookback=40, return_days=5)

    assert not df.empty
    assert df["sector"].isna().all()
    assert df["industry"].isna().all()
    assert df["screener_rank"].isna().all()


def test_make_time_split_purges_overlapping_windows():
    dates = pd.to_datetime([
        "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
        "2024-01-06", "2024-01-07", "2024-01-08", "2024-01-09", "2024-01-10",
    ])
    df = pd.DataFrame({
        "date": dates,
        "symbol": ["AAA"] * len(dates),
        "fwd_ret_target": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    })

    train, test = make_time_split(df, return_days=3, train_fraction=0.7)

    assert train["date"].max() < test["date"].min()
    assert len(train) + len(test) == len(df)


def test_score_latest_reindexes_to_trained_feature_columns():
    class DummyModel:
        def predict(self, X):
            assert list(X.columns) == ["ret_1d", "vol_10d"]
            assert len(X) == 1
            return np.array([0.5])

    dates = pd.date_range("2024-01-01", periods=60, freq="B")
    cand_df = pd.DataFrame([{"symbol": "AAA", "sector": "Technology", "industry": "Software"}])

    out = score_latest(
        DummyModel(),
        ["ret_1d", "vol_10d"],
        cand_df,
        ["AAA"],
        5,
        scaler=None,
        metrics=None,
        use_enhanced_features=False,
    )

    assert not out.empty
    assert "pred_ret" in out.columns


def test_train_and_evaluate_includes_baseline_and_regime_metrics():
    dates = pd.date_range("2024-01-01", periods=40, freq="B")
    rows = []
    for i, date in enumerate(dates):
        symbol = "AAA" if i % 2 == 0 else "BBB"
        rows.append(
            {
                "date": date,
                "symbol": symbol,
                "fwd_ret": 0.01 * (i % 5 - 2),
                "ret_1d": 0.01 * (i % 4 - 1.5),
                "vol_10d": 0.5 + (i % 3) * 0.1,
                "mom_20d": 0.02 * (i % 5 - 2),
                "screener_rank": 10 + i,
                "sector": "Technology",
            }
        )

    df = pd.DataFrame(rows)
    df["fwd_ret_target"] = df["fwd_ret"].rank(pct=True)

    model, _, metrics, _, _ = __import__("yfinance.screener.model_training", fromlist=["train_and_evaluate"]).train_and_evaluate(
        df, return_days=1, use_enhanced_features=False, model_config={"type": "random_forest"}, train_fraction=0.7
    )

    assert model is not None
    assert "baseline_constant_predictor_ic" in metrics
    assert "rolling_5d_mean_daily_ic" in metrics
    assert "bull_regime_mean_daily_ic" in metrics
    assert "sector_mean_daily_ic" in metrics


def test_score_latest_output_distinguishes_raw_return_target_and_live_score():
    class DummyModel:
        def predict(self, X):
            assert len(X) == 1
            return np.array([0.9])

    cand_df = pd.DataFrame([{"symbol": "AAA", "sector": "Technology", "industry": "Software"}])

    out = score_latest(
        DummyModel(),
        ["ret_1d", "vol_10d"],
        cand_df,
        ["AAA"],
        5,
        scaler=None,
        metrics={"model_type": "dummy"},
        use_enhanced_features=False,
    )

    assert {"raw_fwd_ret", "rank_target", "live_rank_score", "pred_ret"}.issubset(out.columns)
    assert np.allclose(out["pred_ret"].to_numpy(), out["live_rank_score"].to_numpy())
