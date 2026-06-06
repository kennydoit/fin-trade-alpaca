# fin-trade-alpaca

Automated Alpaca cash allocation with two trading modes:
- `paper` mode for simulation.
- `live` mode for production execution.

The project supports:
- Ad-hoc allocation runs.
- Scheduled allocation runs with a gatekeeper that only executes on the effective market-open dates for the `15th` and `last day of month` anchors.

## Strategy Model

`optimize_and_buy.py` reads `strategy.json` and allocates spendable cash across three configurable buckets:
- `core`
- `growth`
- `short_term`

Each bucket has:
- A bucket-level weight (all buckets must sum to `1.0`).
- An `assets` map (`SYMBOL -> weight`) that defines how bucket capital is distributed.

`short_term` can intentionally contain no assets so funds remain in cash.

## Local Setup

1. Install dependencies:

```bash
pip install .
```

2. Configure environment variables.

Paper mode variables:
- `ALPACA_PAPER_API_KEY`
- `ALPACA_PAPER_API_SECRET`

Live mode variables:
- `ALPACA_LIVE_API_KEY`
- `ALPACA_LIVE_API_SECRET`

Fallback behavior:
- In `paper` mode, the script can also use `ALPACA_API_KEY` and `ALPACA_API_SECRET` if present.

3. Customize allocations in `configs/strategy.json`.

## Run Commands

Ad-hoc paper run (real paper orders):

```bash
python src/fin_trade_alpaca/optimize_and_buy.py --mode paper --run-type adhoc --config configs/strategy.json
```

Ad-hoc preview only:

```bash
python src/fin_trade_alpaca/optimize_and_buy.py --mode paper --run-type adhoc --config configs/strategy.json --dry-run
```

Scheduled gatekeeper run:

```bash
python src/fin_trade_alpaca/optimize_and_buy.py --mode paper --run-type scheduled --config configs/strategy.json
```

Live run requires explicit confirmation:

```bash
python src/fin_trade_alpaca/optimize_and_buy.py --mode live --run-type adhoc --config configs/strategy.json --confirm-live
```

Optional spend cap:

```bash
python src/fin_trade_alpaca/optimize_and_buy.py --mode paper --run-type adhoc --max-notional 250.00
```

## Clone Live Portfolio To Paper

Use this workflow when you want paper holdings to mirror the dollar values of live holdings.

Step 1: generate paper clone strategy and wipe paper positions

```bash
python src/fin_trade_alpaca/clone_live_to_paper.py
```

Or run both steps in one command (recommended for minimal slippage):

```bash
python src/fin_trade_alpaca/clone_live_to_paper.py --auto-execute-paper
```

- Reads live positions and their market values.
	- Writes `configs/paper_clone_strategy.json` using the same schema as `strategy.json`.

- `--min-order-notional 0.01` (or your override)

Step 2: execute buys in paper using the generated strategy

```bash
python src/fin_trade_alpaca/optimize_and_buy.py --mode paper --run-type adhoc --config configs/paper_clone_strategy.json --min-order-notional 0.01
```

- If paper cash is lower than live total, `clone_live_to_paper.py` exits with a shortfall message.

## GitHub Actions

Workflow file: `.github/workflows/alpaca-allocation.yml`

- On scheduled runs, defaults to:
	- `mode=paper`
	- `run_type=scheduled`
- Manual `workflow_dispatch` allows selecting mode, run type, dry run, and max notional.

- `ALPACA_LIVE_API_SECRET`

- Restrict which branches can deploy to the environment.

Example using GitHub CLI:
```bash
gh secret set ALPACA_PAPER_API_KEY --env alpaca-trading
gh secret set ALPACA_PAPER_API_SECRET --env alpaca-trading
gh secret set ALPACA_LIVE_API_KEY --env alpaca-trading
gh secret set ALPACA_LIVE_API_SECRET --env alpaca-trading
```

- `ALLOW_LIVE_TRADING` -> set to `true` only when live account orders should be allowed

```bash
gh secret set ALLOW_BROKER_FUNDING --env alpaca-trading
gh secret set ALLOW_REAL_ORDERS --env alpaca-trading
gh secret set ALLOW_LIVE_TRADING --env alpaca-trading
```

- Automatic secret scanning on push/PR via `.github/workflows/secret-scan.yml`

## Files Added
- `.github/workflows/alpaca-allocation.yml`: scheduled and manual automation.

## YAML Automation (Funding + Allocation)

This repository now supports a YAML-driven orchestrator in `scripts/run_automation.py`.

- `.github/workflows/alpaca-automation.yml`: scheduled and manual workflow.

### YAML fields

- `strategy_file`: allocation strategy JSON file

- `require_market_open`: if true, skips on market-closed days

- `dry_run`: if true, does not place allocation orders

### ACH transfer prerequisites

ACH transfer submission in this repo targets Alpaca Broker transfer endpoints.

- `ALPACA_BANK_RELATIONSHIP_ID`

For public repositories, store these as Environment Secrets under `alpaca-trading`:
```bash
gh secret set ALPACA_BROKER_API_KEY --env alpaca-trading
gh secret set ALPACA_BROKER_API_SECRET --env alpaca-trading
gh secret set ALPACA_BROKER_ACCOUNT_ID --env alpaca-trading
gh secret set ALPACA_BANK_RELATIONSHIP_ID --env alpaca-trading
```

If these are missing, funding step is skipped with a clear message.

### Local YAML run examples

Run both funding and allocation now:

```bash
python scripts/run_automation.py --config configs/automation.yaml --mode paper --action both --force-immediate
```

Run only funding now:

```bash
python scripts/run_automation.py --config configs/automation.yaml --mode paper --action funding --force-immediate
```

Run only allocation with schedule rules:

```bash
python scripts/run_automation.py --config configs/automation.yaml --mode paper --action allocation
```

Override investment amount from CLI for a one-off run:

```bash
python scripts/run_automation.py --config configs/automation.yaml --mode paper --action allocation --force-immediate --investment-amount 100.00
```

Live mode requires explicit confirmation before execution:

```bash
python scripts/run_automation.py --config configs/automation.yaml --mode live --action allocation --force-immediate --investment-amount 100.00 --execute-live-now
```

For non-interactive environments, pass:

```bash
python scripts/run_automation.py --config configs/automation.yaml --mode live --action allocation --force-immediate --investment-amount 100.00 --execute-live-now --confirm-live
```
- `--execute-live-now` must be present.
- Interactive confirmation (`YES`) or `--confirm-live` must be provided.

## ACH Transfer CLI

Use `transfer_ach.py` for ACH transfer requests with command-line amount and confirmation controls.

Dry-run (no transfer):

```bash
python transfer_ach.py --amount 10.00
```

Real transfer (interactive confirmation):

```bash
python transfer_ach.py --amount 10.00 --execute-ach-now
```

Real transfer (non-interactive):

```bash
python transfer_ach.py --amount 10.00 --execute-ach-now --confirm-transfer
```

Required environment variables for ACH:
- `ALPACA_BROKER_API_KEY`
- `ALPACA_BROKER_API_SECRET`
- `ALPACA_BROKER_ACCOUNT_ID`
- `ALPACA_BANK_RELATIONSHIP_ID`

Safety behavior:
- Without `--execute-ach-now`, command is always dry-run.
- With `--execute-ach-now`, command requires interactive `YES` or `--confirm-transfer`.

Priority for allocation amount:
- `--investment-amount` argument (if provided)
- `allocation.max_notional_per_run` in `automation.yaml`

### GitHub Action usage

Workflow: `.github/workflows/alpaca-automation.yml`

Behavior:
- Scheduled cron run: daily at `30 14 * * *`, runs `action=both` without force.
- Manual run: choose `funding`, `allocation`, or `both`, and optionally force immediate run.

Recommended first step for real money safety:
- Keep `automation.yaml -> funding.dry_run: true`
- Keep `automation.yaml -> allocation.dry_run: true`
- Use a very small `funding.amount` (for example `5.00`)
