# Portfolio Management Console

Simple text-based menu interface for running common portfolio management operations.

## Quick Start

From repository root:
`powershell
python console/console.py
`

Or from console directory:
`powershell
cd console
python console.py
`

## Features

### 1. Sync Portfolio Database
- Syncs current positions from Alpaca into the SQLite database
- Options: paper, live, or both accounts
- Optional transaction backfill
- Automatically enriches assets with sector/industry/company data

### 2. Update Asset Classifications
- Scans all strategy*.json files in configs/
- Updates position strategies (core/growth/short_term) in database
- Based on strategy file definitions

### 3. Run Prediction Screener
- Executes prediction-based equity screening
- Can use default or custom config file
- Outputs ranked predictions to reports/screener_results/

### 4. Run Growth Screener  
- Executes growth-based equity screening
- Generates ranked candidate CSV files
- Outputs to reports/screener_results/

### 5. View Portfolio
- Shows database location and viewing instructions
- Supports SQLite Viewer extension in VS Code
- Compatible with DBeaver and other SQLite clients

### 6. Query Database
- Launches interactive SQL query tool
- Commands: .tables, .schema, .exit
- Direct SQL access to portfolio data

## Notes

- All operations run existing tools via subprocess
- Scripts work identically whether run from console or command line
- Console just provides a convenient menu interface
- No modifications to underlying tools required

## Requirements

- Python 3.11+
- All dependencies from main project (see pyproject.toml)
- Virtual environment activated (.venv)

## Architecture

The console is a simple orchestrator that:
1. Displays text menus with numbered options
2. Prompts for configuration choices (account, backfill, etc.)
3. Constructs command-line arguments
4. Runs tools using subprocess.run()
5. Reports success/failure status

Zero coupling - all tools remain independent and unchanged.
