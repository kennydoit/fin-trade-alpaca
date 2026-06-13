import numpy as np
import pandas as pd

from sandbox.predict_short_term import prepare_features


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

    X, feat_cols = prepare_features(df)

    assert set(feat_cols) == {"ret_1d", "vol_10d", "price_sma10_z"}
    assert np.isfinite(X.to_numpy()).all()
    assert not np.any(np.isnan(X.to_numpy()))
