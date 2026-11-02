from __future__ import annotations

import pandas as pd

from yfinance.screener import data_loading


def test_download_price_history_batched_sleeps_between_batches(monkeypatch):
    calls: list[list[str]] = []
    sleeps: list[float] = []

    def fake_download(symbols, period, interval, auto_adjust):
        symbols_list = [symbols] if isinstance(symbols, str) else list(symbols)
        calls.append(symbols_list)
        return pd.DataFrame({symbol: [100.0 + idx] for idx, symbol in enumerate(symbols_list)})

    monkeypatch.setattr(data_loading, "_download_batch", fake_download)
    monkeypatch.setattr(data_loading, "_sleep", sleeps.append)

    frame = data_loading.download_price_history_batched(
        ["AAPL", "MSFT", "GOOG"],
        period="3mo",
        batch_size=2,
        batch_pause=1.5,
        max_retries=0,
    )

    assert calls == [["AAPL", "MSFT"], ["GOOG"]]
    assert sleeps == [1.5]
    assert list(frame.columns) == ["AAPL", "MSFT", "GOOG"]


def test_download_price_history_batched_retries_before_fallback(monkeypatch):
    attempts: list[list[str]] = []
    sleeps: list[float] = []

    def fake_download(symbols, period, interval, auto_adjust):
        symbols_list = [symbols] if isinstance(symbols, str) else list(symbols)
        attempts.append(symbols_list)
        raise RuntimeError("rate limited")

    def fake_individual(symbols, period, interval="1d", auto_adjust=False):
        return pd.DataFrame({symbols[0]: [123.0]})

    monkeypatch.setattr(data_loading, "_download_batch", fake_download)
    monkeypatch.setattr(data_loading, "_download_individually", fake_individual)
    monkeypatch.setattr(data_loading, "_sleep", sleeps.append)

    frame = data_loading.download_price_history_batched(
        ["AAPL"],
        period="3mo",
        batch_size=1,
        batch_pause=0.0,
        max_retries=2,
        retry_pause=4.0,
    )

    assert attempts == [["AAPL"], ["AAPL"], ["AAPL"]]
    assert sleeps == [4.0, 4.0]
    assert list(frame.columns) == ["AAPL"]
