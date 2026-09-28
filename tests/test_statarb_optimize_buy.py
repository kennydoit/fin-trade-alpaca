from decimal import Decimal
from datetime import datetime, timedelta, timezone
import importlib.util
import sys
import types
from pathlib import Path

from runners import statarb_optimize_buy


def test_select_pair_candidates_limits_overlap_and_count():
    candidates = [
        statarb_optimize_buy.PairCandidate("pair-1", "AAA", "BBB", 2.0, None, 1.0, None, None, None, None, None, None),
        statarb_optimize_buy.PairCandidate("pair-2", "AAA", "CCC", 1.5, None, 1.0, None, None, None, None, None, None),
        statarb_optimize_buy.PairCandidate("pair-3", "DDD", "EEE", 1.0, None, 1.0, None, None, None, None, None, None),
    ]

    selected = statarb_optimize_buy.select_pair_candidates(candidates, max_pairs=2)

    assert [item.pair_id for item in selected] == ["pair-1", "pair-3"]


def test_build_pair_plan_uses_hedge_ratio_for_default_weights():
    candidate = statarb_optimize_buy.PairCandidate("pair-1", "AAA", "BBB", 2.0, None, 2.0, None, None, None, None, None, None)

    plan = statarb_optimize_buy.build_pair_plan([candidate], Decimal("1000.00"))

    assert plan[0]["pair_notional"] == Decimal("1000.00")
    assert plan[0]["long_notional"] == Decimal("333.33")
    assert plan[0]["short_notional"] == Decimal("666.67")


def test_handler_summary_counts_pairs_and_legs():
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.client = lambda *args, **kwargs: None
    sys.modules["boto3"] = boto3_stub

    botocore_stub = types.ModuleType("botocore")
    exceptions_stub = types.ModuleType("botocore.exceptions")

    class ClientError(Exception):
        pass

    exceptions_stub.ClientError = ClientError
    sys.modules["botocore"] = botocore_stub
    sys.modules["botocore.exceptions"] = exceptions_stub

    handler_path = Path(__file__).resolve().parents[1] / "aws" / "functions" / "statarb_optimize_buy" / "handler.py"
    spec = importlib.util.spec_from_file_location("statarb_handler", handler_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    summary = module.summarize_runner_output(
        "\n".join(
            [
                "LIQUIDATE PLAN: pair-old long=OLDL qty=1 short=OLDS qty=1 reasons=max_holding_days reached",
                "PAIR PLAN: pair-1 score=1.2000 long=AAA short=BBB pair_notional=$1000.00 long_leg=$333.33 short_leg=$666.67",
                "  BUY AAA: $333.33",
                "  SELL BBB: $666.67",
                "  Submitted BUY AAA order id=abc qty=1.000000",
                "  Submitted SELL BBB order id=def qty=2.000000",
            ]
        )
    )

    assert summary["planned_pair_count"] == 1
    assert summary["liquidation_pair_count"] == 1
    assert summary["planned_leg_count"] == 2
    assert summary["submitted_leg_count"] == 2


def test_handler_summary_counts_pair_legs_without_buy_sell_lines():
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.client = lambda *args, **kwargs: None
    sys.modules["boto3"] = boto3_stub

    botocore_stub = types.ModuleType("botocore")
    exceptions_stub = types.ModuleType("botocore.exceptions")

    class ClientError(Exception):
        pass

    exceptions_stub.ClientError = ClientError
    sys.modules["botocore"] = botocore_stub
    sys.modules["botocore.exceptions"] = exceptions_stub

    handler_path = Path(__file__).resolve().parents[1] / "aws" / "functions" / "statarb_optimize_buy" / "handler.py"
    spec = importlib.util.spec_from_file_location("statarb_handler_pair_only", handler_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    summary = module.summarize_runner_output(
        "PAIR PLAN: A_B score=1.0000 long=A short=B pair_notional=$1000.00 long_leg=$400.00 short_leg=$600.00"
    )

    assert summary["planned_pair_count"] == 1
    assert summary["planned_leg_count"] == 2
    assert summary["planned_total_notional"] == Decimal("1000.00")


def test_evaluate_liquidation_decisions_marks_max_holding_exit():
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    open_pair = statarb_optimize_buy.OpenStatarbPair(
        pair_id="AAA_BBB",
        long_symbol="AAA",
        short_symbol="BBB",
        long_qty=Decimal("2"),
        short_qty=Decimal("2"),
        entry_time_utc=now - timedelta(days=7),
    )
    candidate = statarb_optimize_buy.PairCandidate(
        pair_id="AAA_BBB",
        long_symbol="AAA",
        short_symbol="BBB",
        score=1.2,
        pair_notional=Decimal("1000.00"),
        hedge_ratio=1.0,
        long_weight=None,
        short_weight=None,
        long_price=100.0,
        short_price=100.0,
        correlation=0.8,
        zscore=0.2,
    )

    decisions = statarb_optimize_buy.evaluate_liquidation_decisions(
        [open_pair],
        [candidate],
        {"max_holding_days": 5, "exit_abs_zscore": 0.5},
        now_utc=now,
    )

    assert len(decisions) == 1
    assert decisions[0].should_liquidate is True
    assert any("max_holding_days" in reason for reason in decisions[0].reasons)
    assert any("mean-reverted" in reason for reason in decisions[0].reasons)