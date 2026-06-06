"""Run the local `yfinance.screener.yfinance_growth_screener.py` module directly.

This bypasses package import resolution and loads the file by path so the
repo's screener implementation is executed regardless of installed
`yfinance` package behavior.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "src" / "yfinance" / "screener" / "yfinance_growth_screener.py"
    if not target.exists():
        print(f"Screener file not found: {target}")
        raise SystemExit(1)

    spec = importlib.util.spec_from_file_location("local_growth_screener", str(target))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["local_growth_screener"] = mod
    # forward CLI args
    # keep sys.argv[0] as the script name
    try:
        spec.loader.exec_module(mod)  # type: ignore
        # call the module's main() to run the CLI
        if hasattr(mod, "main"):
            mod.main()
        else:
            print("Loaded module has no main() entry")
    except Exception as e:
        print("Error executing screener:", e)
        raise


if __name__ == "__main__":
    main()
