"""yfinance.screener package for repository-local screener utilities.

This package provides the moved screener implementation originally in
`sandbox/yfinance_equity_screener.py` so other modules can import
`yfinance.screener.yfinance_equity_screener`.
"""

from pathlib import Path
import importlib
import sys

from .yfinance_equity_screener import (
    screen_equities,
    print_results,
    enrich_results_with_info,
    save_results_csv,
)


def _load_installed_screener():
    """Load the site-packages yfinance screener module without overwriting the repo-local package mapping."""
    repo_src = str(Path(__file__).resolve().parents[2])
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in ("yfinance", "yfinance.screener")}
    try:
        for name in list(sys.modules):
            if name == "yfinance" or name.startswith("yfinance.screener"):
                sys.modules.pop(name, None)
        if repo_src in sys.path:
            sys.path.remove(repo_src)

        return importlib.import_module("yfinance.screener")
    except Exception:
        return None
    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


try:
    _installed_screener = _load_installed_screener()
    EquityQuery = getattr(_installed_screener, "EquityQuery", None)
    screen = getattr(_installed_screener, "screen", None)
except Exception:
    EquityQuery = None
    screen = None

__all__ = [
    "screen_equities",
    "print_results",
    "enrich_results_with_info",
    "save_results_csv",
    "EquityQuery",
    "screen",
]
