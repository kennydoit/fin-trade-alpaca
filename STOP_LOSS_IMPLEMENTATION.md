# Stop Loss & Take Profit Implementation

## Summary

Stop loss and take profit protections have been **implemented with a workaround** for Alpaca's API limitation.

## Configuration

Updated [configs/strategy.json](configs/strategy.json) with **restrictive protections** for faster triggering:
- **Stop loss: -1%** (triggers if price drops 1%)
- **Take profit: +2%** (triggers if price rises 2%)

## Implementation Details

### The Challenge

**Alpaca API Limitation**: Bracket orders (orders with stop loss/take profit attached) **cannot be used with fractional shares**.

Error: `"fractional orders must be simple orders"`

Since your strategy uses notional-based orders (e.g., $333.33 per asset), this results in fractional shares, which prevents direct bracket order attachment.

### The Solution: Two-Step Process

**Step 1**: Submit simple fractional market orders
- Modified [src/runners/optimize_and_buy.py](src/runners/optimize_and_buy.py)
- Uses live price fetching from Alpaca Data API
- Submits fractional qty-based market orders
- Prices properly rounded to penny increments

**Step 2**: Add protective orders after positions fill
- Created [tools/add_position_protections.py](tools/add_position_protections.py)
- Scans existing positions
- Submits separate stop loss and take profit orders for each position
- Uses GTC (Good Till Cancelled) orders

## Usage

### Running Your Strategy (with simple orders)

```bash
python src/runners/optimize_and_buy.py --mode paper --run-type adhoc --config configs/strategy.json
```

This will:
- Submit fractional buy orders for top predictions
- Print calculated stop loss and take profit targets
- **Note**: Protections must be added separately after fills

### Adding Protections to Positions

After your buy orders fill, run:

```bash
python tools/add_position_protections.py --mode paper
```

Options:
- `--dry-run`: Preview without submitting
- `--symbols AAPL MSFT`: Protect specific symbols only
- `--stop-pct -2.0`: Override stop loss percentage
- `--take-pct 5.0`: Override take profit percentage

Example:
```bash
# Preview what would be done
python tools/add_position_protections.py --mode paper --dry-run

# Add protections to all positions
python tools/add_position_protections.py --mode paper

# Add tighter protections to specific symbols
python tools/add_position_protections.py --mode paper --symbols PDFS ICHR --stop-pct -0.5 --take-pct 1.0
```

## How It Works

1. **Fetch positions**: Gets all current holdings from your account
2. **Get current prices**: Fetches live bid prices from Alpaca
3. **Calculate targets**: 
   - Stop loss = current_price × (1 + stop_pct/100)
   - Take profit = current_price × (1 + take_pct/100)
4. **Submit orders**:
   - Stop order (SELL at stop price)
   - Limit order (SELL at take profit price)
5. **Both orders are GTC**: They remain active until triggered or manually cancelled

## Testing

### Test Tools Created

1. **[tools/test_bracket_order.py](tools/test_bracket_order.py)**: Tests bracket orders (reveals fractional limitation)
2. **[tools/diagnose_stop_loss_take_profit.py](tools/diagnose_stop_loss_take_profit.py)**: Analyzes existing orders for protections
3. **[tools/add_position_protections.py](tools/add_position_protections.py)**: Adds protections to positions

### Run Diagnostic

Check if any of your current orders have protections:
```bash
python tools/diagnose_stop_loss_take_profit.py
```

## Key Improvements Made

1. ✅ **Live price fetching**: Uses Alpaca Data API instead of stale screener prices
2. ✅ **Proper price rounding**: Prices rounded to penny increments (no sub-penny)
3. ✅ **Tighter protections**: -1% stop / +2% take profit for faster triggers
4. ✅ **Debug logging**: Clear visibility into order submission process
5. ✅ **Protection tool**: Easy way to add stop loss/take profit to existing positions

## Limitations & Future Enhancements

**Current Limitations**:
- Protections are NOT automatically added after buy orders fill
- Requires manual execution of `add_position_protections.py`
- Stop and take profit are independent orders (not OCO - one-cancels-other)

**Future Enhancements** (TODO):
- Auto-monitoring: Watch for fills and auto-add protections
- OCO orders: Make stop and take profit mutually exclusive
- Automated workflow: Integrate protection addition into main strategy runner
- Position monitoring: Alert when protections trigger

## Next Steps

1. **Test the protection tool** on your current positions:
   ```bash
   python tools/add_position_protections.py --mode paper --dry-run
   ```

2. **Add protections** to your 20 current positions:
   ```bash
   python tools/add_position_protections.py --mode paper
   ```

3. **Run new allocations** and then immediately add protections:
   ```bash
   # Step 1: Run strategy (when you want to buy new positions)
   python src/runners/optimize_and_buy.py --mode paper --run-type adhoc --config configs/strategy.json
   
   # Step 2: Wait for fills (check with tools/inspect_paper_state.py)
   
   # Step 3: Add protections
   python tools/add_position_protections.py --mode paper
   ```

4. **Monitor** protection orders:
   ```bash
   python tools/inspect_paper_state.py
   ```

