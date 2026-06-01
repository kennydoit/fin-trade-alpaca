from decimal import Decimal

from optimize import flatten_symbol_weights, distribute_notionals


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
