# Feature engineering and model tuning for the prediction screener

## What changed

The prediction screener now uses an enhanced feature set before model training:

- base technical features (returns, volatility, momentum, SMA, z-score)
- sector-relative signals
- interaction terms (momentum × volume, growth/valuation, volatility-adjusted return)
- lagged features for persistence and reversal effects
- percentile-rank features within each date
- missing-data indicators and better imputation for fundamental fields

## How to use it

Run the screener as usual:

```powershell
python src/runners/predict_screener.py --limit 100 --return-days 3
```

The runner will now:

1. train using the enhanced feature set
2. print the top feature importances
3. write a CSV of feature importances to reports/screener_results/feature_importance_YYYYMMDD.csv

## Configuring model tuning

The model settings live in configs/prediction_screener.json under the `model` block.

Example:

```json
{
  "model": {
    "type": "lightgbm",
    "n_estimators": 200,
    "learning_rate": 0.05,
    "random_state": 42,
    "tune": true,
    "tune_n_iter": 6,
    "tune_cv": 3
  }
}
```

Notes:

- `tune: false` disables randomized hyperparameter search.
- `tune: true` uses RandomizedSearchCV on the training split.
- `tune_n_iter` controls how many candidate settings are tried.
- `tune_cv` controls cross-validation folds.

## Interpreting feature importances

The feature-importance output is saved to the reports folder and can be used to:

- identify which signals matter most
- compare models across horizons
- prune weak or unstable features later

## Verification

The current run was verified with:

```powershell
python src/runners/predict_screener.py --limit 20 --return-days 3 --out reports/screener_results/feature_tuning_test.csv
```

This produced:

- Spearman IC = 0.1072
- R2 = -0.2770
- MAE = 0.074281
- feature importance file written to reports/screener_results/feature_importance_20260613.csv
