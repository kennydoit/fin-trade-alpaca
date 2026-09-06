# Code Quality Tools

This project uses modern Python linting and formatting tools configured in `pyproject.toml`.

## Quick Commands

### Ruff (Linter & Formatter)

```powershell
# Check code quality issues
python -m ruff check .

# Check with auto-fix
python -m ruff check . --fix

# Format code
python -m ruff format .

# Check specific directory
python -m ruff check src/

# Show statistics
python -m ruff check . --statistics
```

### Vulture (Dead Code Detection)

```powershell
# Find unused code
python -m vulture src/ tools/

# With higher confidence threshold
python -m vulture src/ --min-confidence 90
```

### mypy (Static Type Checking)

```powershell
# Type-check a specific module
python -m mypy src/fin_trade_alpaca/
```

`[tool.mypy]` in `pyproject.toml` starts permissive (`ignore_missing_imports = true`) so it can be
adopted incrementally. Tighten it module-by-module as type hints are added -- trading-critical code
(order execution, credentials, position sizing) should graduate to strict checking first.

### pytest

```powershell
pytest tests/ -q
```

`[tool.pytest.ini_options]` restricts discovery to `testpaths = ["tests"]` and adds `src` to
`pythonpath` so `tests/` can `import fin_trade_alpaca...` / `import runners...` / `import
yfinance...` without an editable install.

**Convention:** every file directly inside `tests/` must contain real `def test_...` functions.
One-off manual debugging scripts (module-level `yf.download(...)` calls, hardcoded CSV paths, live
network/API access) belong in `scripts/` with a `debug_` prefix, never in `tests/` -- pytest
imports+executes module-level code for every file it collects, so a debug script named
`tests/test_*.py` can hang collection indefinitely. Verify with:
```powershell
Select-String -Path tests\*.py -Pattern "^def test_" -List
```
Every file in the list output should appear; anything missing has no real tests and doesn't belong
in `tests/`.

## Ruff Configuration

See `[tool.ruff]` section in `pyproject.toml` for:
- **Target:** Python 3.11+
- **Line length:** 120 characters
- **Enabled rules:** pycodestyle, pyflakes, isort, pep8-naming, bugbear, simplify, performance
- **Exclusions:** Lambda function copies in `aws/functions/*/src/`

## Pre-commit Checklist

Before committing code:
1. ✅ Run `python -m ruff check . --fix` to auto-fix issues
2. ✅ Run `python -m ruff format .` to format code
3. ✅ Review any remaining warnings
4. ✅ Run tests: `pytest tests/`

## Common Issues Ignored

- `E501` - Line too long (formatter handles this)
- `PLR0913` - Too many function arguments (common in trading functions)
- `PLR2004` - Magic values (acceptable for trading parameters)
- `ARG001/002` - Unused arguments (common in callback functions)
- `N806/N803` on `src/yfinance/screener/{feature_engineering,model_training,scoring}.py` -
  scikit-learn's capitalized-matrix convention (`X`, `X_train`, `X_test`) is idiomatic ML naming.

## CI Quality Gate

`.github/workflows/ci.yml` runs on every push/PR to `main`: `ruff check`, `ruff format --check`, and
`pytest`, all scoped to `src/` and `tests/`.

**Why not the whole repo?** As of this writing `tools/`, `scripts/`, `console/`, `database/`, and
`sandbox/` collectively carry ~1000 pre-existing ruff findings accumulated before linting was
adopted. Bulk-autofixing all of them in one pass would be risky (some findings, like
`B023`/loop-variable-closure warnings, can indicate real bugs and need case-by-case review, not a
mechanical fix). `src/` and `tests/` are fully clean and gated; expand the CI scope to additional
directories as they're cleaned up, rather than loosening the gate.

A handful of specific pre-existing files inside `src/`/`tests/` are exempted from specific rules via
`[tool.ruff.lint.per-file-ignores]` (with an inline comment marking them as tracked debt):
`src/runners/clone_live_to_paper.py`, `src/runners/predict_screener.py`,
`src/runners/yfinance_growth_screener.py`, `src/yfinance/__init__.py`,
`src/yfinance/screener/dedup_by_correlation.py`, `src/yfinance/screener/enhanced_features.py`,
`src/yfinance/screener/rank_candidates.py`, `src/yfinance/screener/yfinance_equity_screener.py`,
`tests/test_industry_filtering_fix.py`. New code in these files should still avoid introducing new
findings of other kinds.

## Module Structure

Two previously-monolithic scripts have been split into focused modules for testability and
extensibility. In both cases the **original file path still works** as a backward-compatible
re-export shim (`__all__`), since it's imported directly by name (`from runners.optimize_and_buy
import main`, `from yfinance.screener.predict_short_term import build_dataset`, etc.) by multiple
consumers including the AWS Lambda handlers.

- **`src/runners/optimize_and_buy.py`** (thin CLI orchestrator) delegates to
  `src/fin_trade_alpaca/`: `credentials.py`, `strategy_config.py`, `trading_calendar.py`,
  `dynamic_tilt.py`, `screener_selection.py`, `order_execution.py`, `env_loader.py`.
- **`src/yfinance/screener/predict_short_term.py`** (thin CLI + re-export shim) delegates to sibling
  modules in the same package: `data_loading.py` (screener CSV discovery, yfinance downloads),
  `feature_engineering.py` (technical features, training dataset construction, feature prep),
  `charts.py` (optional matplotlib diagnostic plot), `model_training.py` (model construction,
  tuning, train/evaluate loop), `scoring.py` (scoring the latest trading day).

When adding a new script that grows past a few hundred lines or accumulates unrelated
responsibilities, follow this same pattern: extract pure/testable logic into focused sibling
modules, keep the original path as a thin, directly-runnable orchestrator that re-exports the
public API.

## Installing Tools

Tools are listed in `pyproject.toml` dev dependencies:

```powershell
pip install -e ".[dev]"
```

Or manually:
```powershell
pip install ruff vulture
```
