import argparse
import importlib.util
import json
import sys
import types
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fin_trade_alpaca.order_execution import distribute_notionals
from fin_trade_alpaca.strategy_config import flatten_symbol_weights
from runners import optimize_and_buy


def test_flatten_symbol_weights():
    strategy = {
        "buckets": {
            "core": {"weight": 0.5, "assets": {"SPLG": 0.7, "VXUS": 0.3}},
            "growth": {"weight": 0.25, "assets": {"DTCR": 1.0}},
            "short_term": {"weight": 0.25, "assets": {}},
        }
    }

    weights = flatten_symbol_weights(strategy)
    assert weights["SPLG"] == Decimal("0.35")
    assert weights["VXUS"] == Decimal("0.15")
    assert weights["DTCR"] == Decimal("0.25")


def test_distribute_notionals():
    symbol_weights = {"SPLG": Decimal("0.35"), "VXUS": Decimal("0.15"), "DTCR": Decimal("0.25")}
    notionals = distribute_notionals(Decimal("100.00"), symbol_weights, Decimal("1.00"))
    assert notionals["SPLG"] == Decimal("35.00")
    assert notionals["VXUS"] == Decimal("15.00")
    assert notionals["DTCR"] == Decimal("25.00")


def test_main_scheduled_run_uses_configured_trading_frequency(monkeypatch):
    args = argparse.Namespace(
        mode="paper",
        run_type="scheduled",
        config="configs/strategy.json",
        env_file=None,
        target=None,
        max_notional=None,
        simulate_cash=None,
        min_order_notional=Decimal("1.00"),
        dry_run=False,
        confirm_live=False,
        poll_fills=False,
        poll_timeout=180,
        poll_interval=5,
    )

    captured = {}

    class DummyTradingClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(optimize_and_buy, "parse_args", lambda: args)
    monkeypatch.setattr(optimize_and_buy, "load_environment_for_mode", lambda *args: None)
    monkeypatch.setattr(
        optimize_and_buy,
        "resolve_credentials",
        lambda _mode: optimize_and_buy.ModeCredentials("key", "secret", None, True),
    )
    monkeypatch.setattr(optimize_and_buy, "TradingClient", DummyTradingClient)
    monkeypatch.setattr(
        optimize_and_buy,
        "load_strategy_config",
        lambda _path: {"trading_frequency": "daily", "buckets": {"core": {}, "growth": {}, "short_term": {}}},
    )

    def fake_should_run_today(_client, _today, trading_frequency="monthly"):
        captured["trading_frequency"] = trading_frequency
        return False, "skip"

    monkeypatch.setattr(optimize_and_buy, "should_run_today", fake_should_run_today)

    result = optimize_and_buy.main()

    assert result == 0
    assert captured["trading_frequency"] == "daily"


def test_recent_trade_cooldown_blocks_recent_symbols():
    now = datetime.now(timezone.utc)

    class Side:
        def __init__(self, value):
            self.value = value

    class Order:
        def __init__(self, symbol, side, days_ago):
            self.symbol = symbol
            self.side = Side(side)
            self.filled_at = now - timedelta(days=days_ago)
            self.submitted_at = None

    class Client:
        def get_orders(self, _request):
            return [
                Order("AAPL", "sell", 10),
                Order("MSFT", "buy", 3),
                Order("GOOGL", "sell", 40),
            ]

    blocked = optimize_and_buy.get_recent_trade_cooldown_symbols(
        Client(),
        {
            "enabled": True,
            "buy_cooldown_days": 7,
            "sell_cooldown_days": 30,
            "lookback_days": 45,
        },
    )

    assert "AAPL" in blocked
    assert "MSFT" in blocked
    assert "GOOGL" not in blocked


def test_load_statarb_excluded_symbols_uses_top_pairs_and_min_score(tmp_path):
    pairs_csv = Path(tmp_path) / "pairs.csv"
    pairs_csv.write_text(
        "pair_id,long_symbol,short_symbol,score\n"
        "AAA_BBB,AAA,BBB,2.0\n"
        "CCC_DDD,CCC,DDD,1.4\n"
        "EEE_FFF,EEE,FFF,0.8\n",
        encoding="utf-8",
    )

    strategy = {
        "statarb_exclusion": {
            "enabled": True,
            "pairs_csv": str(pairs_csv),
            "max_pairs": 2,
            "min_score": 1.0,
        }
    }

    excluded = optimize_and_buy.load_statarb_excluded_symbols(strategy, Path(tmp_path))

    assert excluded == {"AAA", "BBB", "CCC", "DDD"}


def test_get_ai_decision_config_requires_api_key(monkeypatch):
    strategy = {
        "ai_decision": {
            "enabled": True,
            "api_key_env": "PERPLEXITY_API_KEY",
        }
    }
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    cfg = optimize_and_buy.get_ai_decision_config(strategy, mode="paper")
    assert cfg is None


def test_get_ai_decision_config_respects_live_policy(monkeypatch):
    strategy = {
        "ai_decision": {
            "enabled": True,
            "live_mode_policy": "paper_only",
        }
    }
    monkeypatch.setenv("PERPLEXITY_API_KEY", "dummy")
    cfg = optimize_and_buy.get_ai_decision_config(strategy, mode="live")
    assert cfg is None


def test_run_ai_advisory_writes_report(tmp_path, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "overall_decision": "mixed",
                                    "confidence": 0.88,
                                    "symbol_feedback": [{"symbol": "AAPL", "action": "approve", "reason": "ok"}],
                                    "notes": ["advisory only"],
                                }
                            )
                        }
                    }
                ]
            }
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setenv("PERPLEXITY_API_KEY", "dummy")
    monkeypatch.setattr(optimize_and_buy.urlrequest, "urlopen", lambda *args, **kwargs: FakeResponse())

    ai_cfg = {
        "mode": "advisory",
        "api_key_env": "PERPLEXITY_API_KEY",
        "endpoint": "https://api.perplexity.ai/chat/completions",
        "model": "sonar-pro",
        "timeout_seconds": 3,
        "max_candidates": 25,
        "max_tokens": 300,
        "temperature": 0.0,
        "min_confidence": None,
    }

    advisory, report_path = optimize_and_buy.run_ai_advisory(
        ai_cfg=ai_cfg,
        section="short_term",
        candidates=[{"symbol": "AAPL", "notional": "100.00"}],
        strategy_config={"trading_frequency": "daily"},
        mode="paper",
        dry_run=True,
        report_root=tmp_path,
    )

    assert advisory is not None
    assert advisory["overall_decision"] == "mixed"
    assert report_path is not None
    assert report_path.exists()


def test_pick_top_n_candidates_from_screener_includes_trigger_metadata(tmp_path):
    csv_path = Path(tmp_path) / "predictions.csv"
    csv_path.write_text(
        "symbol,regularMarketPrice,pred_ret,avg_ret,prediction_rank\n"
        "AAPL,100,0.9,0.2,1\n"
        "MSFT,50,0.7,0.1,2\n",
        encoding="utf-8",
    )

    picks = optimize_and_buy.pick_top_n_candidates_from_screener(csv_path, 2)

    assert picks[0]["symbol"] == "AAPL"
    assert picks[0]["score_field"] == "pred_ret"
    assert picks[0]["score"] == 0.9
    assert picks[0]["prediction_rank"] == 1


def test_submit_orders_emits_structured_summaries(monkeypatch, capsys):
    class DummyOrder:
        id = "abc123"

    class DummyClient:
        def submit_order(self, _req):
            return DummyOrder()

    optimize_and_buy.submit_orders(
        DummyClient(),
        {"AAPL": Decimal("25.00")},
        dry_run=False,
        order_context={
            "AAPL": {
                "section": "core_growth",
                "trigger_metric_name": "effective_weight",
                "trigger_metric_value": 0.5,
                "trigger_description": "effective_weight=0.5",
                "source_buckets": ["core"],
            }
        },
    )

    output = capsys.readouterr().out
    assert "ORDER_SUMMARY|" in output
    assert '"symbol": "AAPL"' in output
    assert '"status": "submitted"' in output


def test_apply_ai_actions_to_short_orders_respects_bounds():
    orders = [
        {"symbol": "AAPL", "notional": Decimal("100.00"), "price": 10.0, "stop_pct": -2.0, "take_pct": 5.0},
        {"symbol": "MSFT", "notional": Decimal("100.00"), "price": 10.0, "stop_pct": -2.0, "take_pct": 5.0},
        {"symbol": "NVDA", "notional": Decimal("100.00"), "price": 10.0, "stop_pct": -2.0, "take_pct": 5.0},
    ]
    advisory = {
        "symbol_feedback": [
            {"symbol": "AAPL", "action": "veto"},
            {"symbol": "MSFT", "action": "veto"},
            {"symbol": "NVDA", "action": "reduce", "reduce_pct": 40},
        ]
    }
    ai_cfg = {
        "apply_in_paper": True,
        "max_veto_fraction": 0.34,
        "min_remaining_orders": 1,
        "default_reduce_fraction": 0.25,
        "max_reduce_fraction": 0.5,
    }

    adjusted = optimize_and_buy.apply_ai_actions_to_short_orders(
        orders,
        advisory_json=advisory,
        ai_cfg=ai_cfg,
        mode="paper",
        min_order_notional=Decimal("1.00"),
    )

    symbols = [str(order["symbol"]) for order in adjusted]
    assert "AAPL" not in symbols
    assert "MSFT" in symbols
    nvda_order = next(o for o in adjusted if o["symbol"] == "NVDA")
    assert nvda_order["notional"] == Decimal("60.00")


def test_apply_ai_actions_to_notionals_live_mode_noop():
    notionals = {
        "AAPL": Decimal("100.00"),
        "MSFT": Decimal("100.00"),
    }
    advisory = {"symbol_feedback": [{"symbol": "AAPL", "action": "veto"}]}
    ai_cfg = {
        "apply_in_paper": True,
        "max_veto_fraction": 0.5,
        "min_remaining_orders": 1,
        "default_reduce_fraction": 0.25,
        "max_reduce_fraction": 0.5,
    }

    adjusted = optimize_and_buy.apply_ai_actions_to_notionals(
        notionals,
        advisory_json=advisory,
        ai_cfg=ai_cfg,
        mode="live",
        min_order_notional=Decimal("1.00"),
    )

    assert adjusted == notionals


def test_recent_trade_cooldown_sell_override_env(monkeypatch):
    now = datetime.now(timezone.utc)

    class Side:
        def __init__(self, value):
            self.value = value

    class Order:
        def __init__(self, symbol, side, days_ago):
            self.symbol = symbol
            self.side = Side(side)
            self.filled_at = now - timedelta(days=days_ago)
            self.submitted_at = None

    class Client:
        def get_orders(self, _request):
            return [Order("AAPL", "sell", 2)]

    monkeypatch.setenv("FINTRADE_SELL_COOLDOWN_DAYS_OVERRIDE", "0")
    blocked = optimize_and_buy.get_recent_trade_cooldown_symbols(
        Client(),
        {
            "enabled": True,
            "buy_cooldown_days": 7,
            "sell_cooldown_days": 30,
            "lookback_days": 45,
        },
    )
    assert "AAPL" not in blocked


def test_optimize_buy_handler_summary_includes_detailed_orders():
    handler_path = Path(__file__).resolve().parents[1] / "aws" / "functions" / "optimize_and_buy" / "handler.py"
    spec = importlib.util.spec_from_file_location("optimize_buy_handler", handler_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.client = lambda *args, **kwargs: None
    botocore_stub = types.ModuleType("botocore")
    botocore_exceptions_stub = types.ModuleType("botocore.exceptions")
    botocore_exceptions_stub.ClientError = Exception
    monkey_modules = {
        "boto3": boto3_stub,
        "botocore": botocore_stub,
        "botocore.exceptions": botocore_exceptions_stub,
    }
    original_modules = {name: sys.modules.get(name) for name in monkey_modules}
    sys.modules.update(monkey_modules)
    try:
        spec.loader.exec_module(module)
    finally:
        for name, original in original_modules.items():
            if original is None:
                del sys.modules[name]
            else:
                sys.modules[name] = original

    output_text = "\n".join(
        [
            'ORDER_SUMMARY|{"status":"planned","section":"short_term","symbol":"AAPL","notional":"100.00","trigger_description":"prediction_rank=1, pred_ret=0.9"}',
            'ORDER_SUMMARY|{"status":"submitted","section":"short_term","symbol":"AAPL","notional":"100.00","trigger_description":"prediction_rank=1, pred_ret=0.9"}',
            'Submitted AAPL order id=xyz notional=$100.00',
        ]
    )

    summary = module.summarize_runner_output(output_text)

    assert summary["planned_order_count"] == 1
    assert summary["submitted_order_count"] == 1
    assert summary["submitted_orders"][0]["symbol"] == "AAPL"
