import importlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def test_repo_yfinance_shim_exposes_ticker_and_download(monkeypatch):
    fake_yfinance = SimpleNamespace(
        Ticker=lambda symbol: f"ticker:{symbol}",
        download=lambda *args, **kwargs: None,
        EquityQuery=object,
        __version__="test-version",
        screener=SimpleNamespace(),
    )

    real_import_module = importlib.import_module

    def fake_import_module(name):
        if name == "yfinance":
            return fake_yfinance
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    for name in list(sys.modules):
        if name == "yfinance" or name.startswith("yfinance."):
            sys.modules.pop(name, None)

    spec = importlib.util.spec_from_file_location("test_yfinance_shim", Path("src/yfinance/__init__.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert hasattr(module, "Ticker"), "expected the repo yfinance shim to expose Ticker"
    assert callable(module.Ticker), "expected yfinance.Ticker to be callable"
    assert hasattr(module, "download"), "expected the repo yfinance shim to expose download"
    assert callable(module.download), "expected yfinance.download to be callable"
