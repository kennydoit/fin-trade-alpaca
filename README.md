# fin-trade-alpaca

An automated paper-trading system that connects to **Alpaca** to execute stock trades driven by
technical signals (RSI + moving averages) and, optionally, enriched by **Snowflake Cortex AI**
analysis of SEC fundamental filings.

---

## How it works

```
┌──────────────────────────────────────────────────────────────┐
│                       Trading Cycle                          │
│                                                              │
│  1. Alpaca ──▶ fetch all tradable US-equity symbols          │
│  2. Alpaca ──▶ download 90 days of daily OHLCV bars          │
│  3. Signals ──▶ compute RSI, SMA20/50, EMA9/21, MACD        │
│  4. Screener ──▶ score & rank; select top N candidates       │
│                                                              │
│  ┌── if Snowflake is configured ────────────────────────┐   │
│  │  5. Snowflake ──▶ query SEC fundamentals for top N   │   │
│  │  6. Cortex LLM ──▶ BUY / HOLD / SELL per symbol     │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  7. Alpaca ──▶ submit market-buy orders for approvals        │
└──────────────────────────────────────────────────────────────┘
```

### Technical signals used

| Indicator | Parameter | Signal |
|-----------|-----------|--------|
| RSI | 14-period | < 35 oversold → buy candidate |
| SMA | 20 & 50 day | price above both + SMA20 > SMA50 = uptrend |
| EMA | 9 & 21 day | momentum confirmation |
| MACD | 12-26-9 | MACD crossing above signal line |
| Volume | 20-day avg ratio | > 1× = conviction |

### Composite score

Each symbol receives a 0–100 composite score:
- **RSI component (40 pts)** — higher score for lower/oversold RSI
- **MA alignment (30 pts)** — price vs SMA20/50 & SMA20 vs SMA50
- **MACD (15 pts)** — bullish cross
- **Volume (15 pts)** — current bar volume vs 20-day average

### Snowflake Cortex AI (optional)

When Snowflake credentials are provided the system:
1. Queries SEC fundamental data from a configurable table
2. Calls `SNOWFLAKE.CORTEX.COMPLETE` with a structured prompt containing both technical and fundamental context
3. Parses the LLM response for a **BUY / HOLD / SELL** recommendation
4. Only executes trades for AI-approved BUY signals

If Snowflake is not configured, the top-N technically-ranked symbols proceed directly to order execution.

---

## Quick start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your Alpaca paper-trading API keys
# Optionally add Snowflake credentials for AI analysis
```

### 3. Run

```bash
python main.py
```

---

## Configuration

All settings are loaded from environment variables (or a `.env` file).

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ALPACA_API_KEY` | ✅ | — | Alpaca API key |
| `ALPACA_SECRET_KEY` | ✅ | — | Alpaca secret key |
| `ALPACA_PAPER` | | `true` | `true` for paper trading |
| `TOP_N_SYMBOLS` | | `20` | Max candidates after screening |
| `MAX_POSITION_SIZE` | | `1000.0` | Max $ per position |
| `RSI_PERIOD` | | `14` | RSI lookback window |
| `RSI_OVERSOLD` | | `35.0` | RSI buy threshold |
| `LOOKBACK_DAYS` | | `90` | Days of history to fetch |
| `SNOWFLAKE_ACCOUNT` | optional | — | Snowflake account identifier |
| `SNOWFLAKE_USER` | optional | — | Snowflake username |
| `SNOWFLAKE_PASSWORD` | optional | — | Snowflake password |
| `SNOWFLAKE_DATABASE` | optional | — | Snowflake database |
| `SNOWFLAKE_WAREHOUSE` | optional | — | Snowflake warehouse |
| `SEC_FILINGS_TABLE` | | `SEC_FILINGS.PUBLIC.FUNDAMENTALS` | Fully-qualified SEC table |
| `CORTEX_MODEL` | | `llama3-70b` | Snowflake Cortex model |

### Expected SEC fundamentals table schema

```sql
CREATE TABLE SEC_FILINGS.PUBLIC.FUNDAMENTALS (
    TICKER              VARCHAR,
    PERIOD_OF_REPORT    DATE,
    REVENUE             FLOAT,
    NET_INCOME          FLOAT,
    EPS                 FLOAT,
    TOTAL_ASSETS        FLOAT,
    TOTAL_LIABILITIES   FLOAT,
    OPERATING_CASH_FLOW FLOAT
);
```

---

## Project structure

```
fin-trade-alpaca/
├── main.py                   # Orchestration entry point
├── config.py                 # Environment-variable configuration
├── requirements.txt
├── .env.example
├── trading/
│   ├── client.py             # Alpaca data + trading client
│   └── trader.py             # Order submission & position sizing
├── signals/
│   ├── technical.py          # RSI, SMA, EMA, MACD calculation
│   └── screener.py           # Symbol scoring & ranking
├── fundamentals/
│   ├── client.py             # Snowflake connection manager
│   ├── sec_filings.py        # SEC fundamental data queries
│   └── cortex.py             # Snowflake Cortex AI integration
└── tests/
    ├── test_technical.py
    ├── test_screener.py
    └── test_trader.py
```

---

## Running tests

```bash
pip install pytest
python -m pytest tests/ -v
```

---

## License

MIT — see [LICENSE](LICENSE).
