# Growth Screener - Industry and Sector Filtering

## Overview

The growth screener now has intelligent sector filtering based on industry selections, making it easier to get relevant results.

## New Features

### 1. Industry-Based Sector Filtering

When you select industries, the console automatically:
- Maps industries to their valid sectors
- Offers to auto-select all matching sectors
- Shows only relevant sectors if you prefer manual selection

### 2. Industry-to-Sector Mapping

The mapping is configured in [configs/equity_screener.json](configs/equity_screener.json):

```json
{
  "industry_to_sector_map": {
    "Software - Infrastructure": "Technology",
    "Software - Application": "Technology",
    "Semiconductors": "Technology",
    "Computer Hardware": "Technology",
    "Internet Content & Information": "Communication Services",
    "Biotechnology": "Healthcare",
    "Drug Manufacturers - General": "Healthcare",
    "Medical Devices": "Healthcare",
    "Medical Instruments & Supplies": "Healthcare"
  }
}
```

### 3. Bug Fix: Multiple Industry Filtering

Fixed the bug where multiple industries couldn't be selected together. The screener now properly handles comma-separated industry lists.

## Workflow Examples

### Example 1: Auto-Select Matching Sectors

```
Growth Screener - generates ranked candidates CSV
Output: reports/screener_results/
You can filter by sectors and/or industries.

Proceed? (y/n): y

Filter by industries? (y/n): y

Industry selection (y/n for each):
  Software - Infrastructure? y
  Software - Application? y
  Biotechnology? y

Industries selected map to sectors: Communication Services, Healthcare, Technology
Use all matching sectors automatically? (y/n): y
✓ Auto-selected sectors: Communication Services, Healthcare, Technology
```

### Example 2: Manual Sector Selection

```
Filter by industries? (y/n): y

Industry selection (y/n for each):
  Software - Infrastructure? y
  Semiconductors? y

Industries selected map to sectors: Technology
Use all matching sectors automatically? (y/n): n

Showing only sectors matching your industry selections:
  Technology? y
```

### Example 3: No Industry Filter

```
Filter by industries? (y/n): n

Sector selection (y/n for each):
  Technology? y
  Healthcare? n
  Financial Services? y
  ...
```

## Command Line Usage

The command line also works correctly with multiple industries:

```bash
# Multiple sectors and industries
python src/runners/yfinance_growth_screener.py \
  --sectors "Technology,Healthcare" \
  --industry "Software - Infrastructure,Biotechnology,Medical Devices"

# Industries only (sectors fetched from all matching)
python src/runners/yfinance_growth_screener.py \
  --industry "Semiconductors,Computer Hardware"
```

## Technical Details

### How Industry Filtering Works

1. **Initial fetch**: Screener fetches candidates from yfinance by sector
2. **Post-filtering**: Results are then filtered by industry using substring matching
3. **No API limitation**: Since industry filtering happens after fetching, there's no yfinance API constraint

### Why This Approach?

The yfinance screener API has limitations on complex queries. By:
- Fetching by sector (which works reliably)
- Filtering by industry afterward (in Python)

We get:
- ✓ Reliable results
- ✓ Support for multiple industries
- ✓ No API query limit issues

## Adding New Mappings

To add more industry-to-sector mappings:

1. Edit [configs/equity_screener.json](configs/equity_screener.json)
2. Add to `industry_to_sector_map`:
   ```json
   "Your New Industry": "Matching Sector"
   ```
3. Add to `industry_choices` if needed:
   ```json
   "industry_choices": [
     "Existing Industries...",
     "Your New Industry"
   ]
   ```

## Troubleshooting

**"Invalid EQ value" error**
- ✓ Fixed in this update
- If you see this error, you're using an old version

**No results returned**
- Try broadening sector selection
- Some industry filters may be very specific
- Check that the industry name matches exactly (case-sensitive)

**Sector mapping not working**
- Verify the industry name in `industry_to_sector_map`
- Check that the sector exists in `sector_choices`
