# Prediction Screener Audit / Remediation Brief

## Goal
Improve the prediction screener’s validity and observability without assuming high R² or investment performance. Focus on honest held-out evaluation, point-in-time correctness, and ranking metrics.

## Relevant files
- `src/runners/predict_screener.py`
- `src/yfinance/screener/feature_engineering.py`
- `src/yfinance/screener/model_training.py`
- `src/yfinance/screener/scoring.py`
- `src/yfinance/screener/data_loading.py`
- `src/runners/yfinance_growth_screener.py`
- `src/fin_trade_alpaca/screener_selection.py`
- `src/runners/optimize_and_buy.py`
- `configs/prediction_screener.json`
- `tests/test_predict_short_term.py`
- `tests/test_predict_screener_config.py`
- `tests/test_optimize_and_buy_ranking.py`

## Required implementation outcomes
1. Make historical training features point-in-time correct.
   - Do not copy the latest screener row into all historical dates.
   - Separate truly historical price-derived features from same-day screener snapshot features unless historical snapshots are available.

2. Make evaluation honest for overlapping forward labels.
   - Replace the current single split in `train_and_evaluate()` with a purged/embargoed chronological evaluation.
   - If cross-validation is used, use a time-aware splitter; do not use plain `RandomizedSearchCV(cv=3)` for temporal data.

3. Align target naming and output semantics.
   - If the model predicts `fwd_ret_target` (rank/excess target), do not expose it as `pred_ret` without clarification.
   - Output both the modeling target name and the intended interpretation.

4. Add proper ranking metrics.
   - Report daily cross-sectional IC and mean daily IC.
   - Report top-k / quantile spread metrics on held-out dates.
   - Keep R² only as a secondary metric if retained at all.

5. Fix train/inference feature consistency.
   - Reuse the same feature list/order at inference.
   - Reuse train-derived preprocessing statistics where applicable.
   - Avoid the generic `enhance_features()` path for live inference if it differs from evaluation preprocessing.

6. Make configuration effective.
   - Ensure `configs/prediction_screener.json` target/split settings actually control runtime behavior, or remove misleading unused fields.

7. Preserve end-to-end trading compatibility.
   - `src/fin_trade_alpaca/screener_selection.py` and `src/runners/optimize_and_buy.py` must continue to consume prediction outputs safely.
   - Any model-quality gate must read metrics that correspond to the new evaluation design.

## Priority order
### P0
- Remove current-snapshot leakage from historical rows in `build_dataset()`.
- Add purge/embargo to evaluation.
- Clarify target/prediction naming and held-out metrics.

### P1
- Make inference preprocessing match training/evaluation exactly.
- Reindex inference features to the trained feature list.
- Wire config split/target settings into actual code.

### P2
- Add baseline comparisons:
  - constant predictor
  - raw screener rank / momentum baseline
  - simple linear baseline
- Add rolling/expanding-window evaluation and regime breakdowns.

## Acceptance criteria
- Historical training rows do not use future or latest screener metadata unless explicitly labeled as unavailable and excluded.
- Held-out evaluation uses chronology plus purge/embargo for multi-day horizons.
- Metrics report:
  - mean daily IC
  - IC standard deviation
  - top-decile minus bottom-decile realized spread (or similar rank metric)
  - sample sizes by date
- Prediction outputs clearly distinguish:
  - raw forward return labels
  - transformed ranking targets
  - live ranking score
- Inference feature matrix is explicitly aligned to the trained feature columns.
- Config fields for horizon/split either drive behavior or are removed.
- No claim of predictive improvement is made without held-out evidence.

## Tests to add or strengthen
1. Unit test: `build_dataset()` must not broadcast latest candidate metadata across historical rows.
2. Unit test: purged split leaves no label overlap between train and test windows.
3. Unit test: inference reindexes to training `feat_cols` and fails loudly on missing required features.
4. Unit test: output schema distinguishes rank target score from raw return prediction.
5. Unit test: config split/horizon settings affect runtime behavior.
6. Regression test: `tools/backtest_predictions.py` or replacement evaluation code must use true forward outcomes, not trailing feature columns.

## Non-goals
- Do not promise positive R².
- Do not promise profitable trading.
- Do not optimize for a single lucky split.