from __future__ import annotations

COMMANDS = {
    "optimize-and-buy": {
        "entry": "orchestration.commands.optimize_and_buy:main",
        "description": "Run the main Alpaca allocation and paper/live trading workflow.",
    },
    "predict-screener": {
        "entry": "orchestration.commands.predict_screener:main",
        "description": "Run the prediction screener pipeline on the latest candidate CSV.",
    },
    "clone-live-to-paper": {
        "entry": "orchestration.commands.clone_live_to_paper:main",
        "description": "Clone live positions into a paper strategy snapshot.",
    },
    "liquidate-paper-account": {
        "entry": "orchestration.commands.liquidate_paper_account:main",
        "description": "Liquidate paper positions for cleanup or reset.",
    },
}


def main() -> int:
    print("Supported orchestration commands:")
    for name, info in sorted(COMMANDS.items()):
        print(f"  - {name}: {info['description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
