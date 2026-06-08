from __future__ import annotations

from fin_trade_alpaca.cli.predict_screener_cli import main as _main


def main() -> int:
    return _main()


if __name__ == "__main__":
    raise SystemExit(main())
