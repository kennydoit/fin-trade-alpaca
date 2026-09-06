import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import console.console as console_module
from console.console import get_config_list, print_menu


def test_get_config_list_reads_json_arrays(tmp_path):
    config_path = tmp_path / "equity_screener.json"
    config_path.write_text(
        json.dumps(
            {
                "sector_choices": ["Technology", "Healthcare", "Financial Services"],
                "industry_choices": ["Software - Infrastructure"],
            }
        ),
        encoding="utf-8",
    )

    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert get_config_list(config, "sector_choices", ["Technology"]) == [
        "Technology",
        "Healthcare",
        "Financial Services",
    ]
    assert get_config_list(config, "industry_choices", []) == ["Software - Infrastructure"]


def test_print_menu_shows_trade_options(capsys):
    print_menu()
    output = capsys.readouterr().out

    assert "5. Paper Trade" in output
    assert "6. Live Trade" in output
    assert "7. View Portfolio" in output
    assert "8. Query Database" in output
    assert "9. Daily Portfolio Review" in output


def test_run_trade_uses_strategy_config(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports" / "screener_results"
    reports_dir.mkdir(parents=True)
    (reports_dir / "predictions_20260615.csv").write_text("symbol\nAAPL\n", encoding="utf-8")
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)
    (config_dir / "strategy.json").write_text('{"buckets": {}}', encoding="utf-8")

    monkeypatch.setattr(console_module, "get_repo_root", lambda: tmp_path)
    captured = {}

    def fake_run_command(cmd, description, capture_output=False):
        captured["cmd"] = cmd
        captured["description"] = description
        return True

    monkeypatch.setattr(console_module, "run_command", fake_run_command)
    monkeypatch.setattr("builtins.input", lambda *_args, **_kwargs: "y")

    console_module.run_trade("paper")

    assert captured["cmd"][5].replace("\\", "/") == "configs/strategy.json"
    assert "--dry-run" in captured["cmd"]
