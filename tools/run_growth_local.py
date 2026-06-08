"""Run the local `yfinance.screener.yfinance_growth_screener.py` module directly.

This bypasses package import resolution and loads the file by path so the
repo's screener implementation is executed regardless of installed
`yfinance` package behavior.
"""
from __future__ import annotations

import importlib.util
import sys
from types import ModuleType
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "src" / "yfinance" / "screener" / "yfinance_growth_screener.py"
    if not target.exists():
        print(f"Screener file not found: {target}")
        raise SystemExit(1)

    # Ensure package-style relative imports in the screener file work by
    # creating package modules for `yfinance` and `yfinance.screener` that
    # point to the repo's `src/yfinance` tree.
    repo_src = repo_root / "src"
    yfinance_pkg = ModuleType("yfinance")
    yfinance_pkg.__path__ = [str(repo_src / "yfinance")]
    screener_pkg = ModuleType("yfinance.screener")
    screener_pkg.__path__ = [str(repo_src / "yfinance" / "screener")]
    sys.modules.setdefault("yfinance", yfinance_pkg)
    sys.modules.setdefault("yfinance.screener", screener_pkg)

    spec = importlib.util.spec_from_file_location("yfinance.screener.yfinance_growth_screener", str(target))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore
        if hasattr(mod, "main"):
            mod.main()
        else:
            print("Loaded module has no main() entry")
    except Exception as e:
        print("Error executing screener:", e)
        raise


if __name__ == "__main__":
    main()
