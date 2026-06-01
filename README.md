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

3. Customize allocations in `strategy.json`.

## Run Commands

Ad-hoc paper run (real paper orders):

```bash
python optimize_and_buy.py --mode paper --run-type adhoc --config strategy.json
```

Ad-hoc preview only:

```bash
python optimize_and_buy.py --mode paper --run-type adhoc --config strategy.json --dry-run
```

Scheduled gatekeeper run:

```bash
python optimize_and_buy.py --mode paper --run-type scheduled --config strategy.json
```

Live run requires explicit confirmation:

```bash
python optimize_and_buy.py --mode live --run-type adhoc --config strategy.json --confirm-live
```

Optional spend cap:

```bash
python optimize_and_buy.py --mode paper --run-type adhoc --max-notional 250.00
```

## Clone Live Portfolio To Paper

Use this workflow when you want paper holdings to mirror the dollar values of live holdings.

Step 1: generate paper clone strategy and wipe paper positions

```bash
python clone_live_to_paper.py
```

Or run both steps in one command (recommended for minimal slippage):

```bash
python clone_live_to_paper.py --auto-execute-paper
```

This command:
- Sells all current paper positions.
- Reads live positions and their market values.
- Writes `paper_clone_strategy.json` using the same schema as `strategy.json`.

When `--auto-execute-paper` is set, it immediately calls `optimize_and_buy.py` in paper mode using:
- `--config paper_clone_strategy.json`
- `--max-notional <live total market value>`
- `--min-order-notional 0.01` (or your override)

Step 2: execute buys in paper using the generated strategy

```bash
python optimize_and_buy.py --mode paper --run-type adhoc --config paper_clone_strategy.json --min-order-notional 0.01
```

Notes:
- This flow mirrors dollar values, not share counts.
- Exact mirroring requires paper cash (after liquidation) to be at least live long-position market value.
- If paper cash is lower than live total, `clone_live_to_paper.py` exits with a shortfall message.

## GitHub Actions

Workflow file: `.github/workflows/alpaca-allocation.yml`

Behavior:
- Daily schedule at `30 14 * * *` (10:30 AM Eastern during standard offset windows).
- On scheduled runs, defaults to:
	- `mode=paper`
	- `run_type=scheduled`
	- `dry_run=false`
- Manual `workflow_dispatch` allows selecting mode, run type, dry run, and max notional.

Required GitHub Secrets:
- `ALPACA_PAPER_API_KEY`
- `ALPACA_PAPER_API_SECRET`
- `ALPACA_LIVE_API_KEY`
- `ALPACA_LIVE_API_SECRET`

Public-repo hardening (recommended):
- Create a GitHub Environment named `alpaca-trading`.
- Store these values as Environment Secrets (instead of plain repo secrets).
- Add required reviewers for the environment before any workflow can access secrets.
- Restrict which branches can deploy to the environment.

Example using GitHub CLI:
```bash
gh secret set ALPACA_PAPER_API_KEY --env alpaca-trading
gh secret set ALPACA_PAPER_API_SECRET --env alpaca-trading
gh secret set ALPACA_LIVE_API_KEY --env alpaca-trading
gh secret set ALPACA_LIVE_API_SECRET --env alpaca-trading
```

Additional CI approval secrets (default deny for real money actions):
- `ALLOW_BROKER_FUNDING` -> set to `true` only when ACH funding should be allowed
- `ALLOW_REAL_ORDERS` -> set to `true` only when non-dry-run orders should be allowed
- `ALLOW_LIVE_TRADING` -> set to `true` only when live account orders should be allowed

```bash
gh secret set ALLOW_BROKER_FUNDING --env alpaca-trading
gh secret set ALLOW_REAL_ORDERS --env alpaca-trading
gh secret set ALLOW_LIVE_TRADING --env alpaca-trading
```

Security checks enabled in this repo:
- Workflows run with minimum token permission: `contents: read`
- Sensitive environment variables are scoped to execution steps only
- Non-dry-run funding/orders in CI are blocked unless approval secrets are set to `true`
- Live orders in CI require both `ALLOW_REAL_ORDERS=true` and `ALLOW_LIVE_TRADING=true`
- Automatic secret scanning on push/PR via `.github/workflows/secret-scan.yml`

## Files Added

- `optimize_and_buy.py`: main allocation engine.
- `strategy.example.json`: starter strategy template.
- `strategy.json`: active strategy config.
- `.github/workflows/alpaca-allocation.yml`: scheduled and manual automation.

## YAML Automation (Funding + Allocation)

This repository now supports a YAML-driven orchestrator in `run_automation.py`.

Primary files:
- `automation.yaml`: active settings.
- `automation.example.yaml`: template.
- `.github/workflows/alpaca-automation.yml`: scheduled and manual workflow.

### YAML fields

`trading`
- `mode`: `paper` or `live`
- `strategy_file`: allocation strategy JSON file

`funding`
- `enabled`: true/false
- `provider`: currently `alpaca_broker`
- `schedule`: list such as `[15, last_day]` or `[immediate]`
- `amount`: ACH transfer amount
- `dry_run`: if true, prints transfer payload and does not submit
- `require_market_open`: if true, skips on market-closed days

`allocation`
- `enabled`: true/false
- `schedule`: list such as `[15, last_day]` or `[immediate]`
- `require_market_open`: if true, skips on market-closed days
- `min_new_cash`: minimum spendable cash required to place orders
- `reserve_cash`: cash buffer kept in account
- `max_notional_per_run`: cap on dollars invested each run
- `min_order_notional`: minimum per-order dollar size
- `dry_run`: if true, does not place allocation orders

### ACH transfer prerequisites

ACH transfer submission in this repo targets Alpaca Broker transfer endpoints.

Set these environment variables (or GitHub secrets):
- `ALPACA_BROKER_API_KEY`
- `ALPACA_BROKER_API_SECRET`
- `ALPACA_BROKER_ACCOUNT_ID`
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
python run_automation.py --config automation.yaml --mode paper --action both --force-immediate
```

Run only funding now:

```bash
python run_automation.py --config automation.yaml --mode paper --action funding --force-immediate
```

Run only allocation with schedule rules:

```bash
python run_automation.py --config automation.yaml --mode paper --action allocation
```

Override investment amount from CLI for a one-off run:

```bash
python run_automation.py --config automation.yaml --mode paper --action allocation --force-immediate --investment-amount 100.00
```

Live mode requires explicit confirmation before execution:

```bash
python run_automation.py --config automation.yaml --mode live --action allocation --force-immediate --investment-amount 100.00 --execute-live-now
```

For non-interactive environments, pass:

```bash
python run_automation.py --config automation.yaml --mode live --action allocation --force-immediate --investment-amount 100.00 --execute-live-now --confirm-live
```

Live safety gates for non-dry-run allocation:
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
