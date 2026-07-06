# Inspection Commands

Run all commands from repository root: `/Users/juhwan/hermes_v2`.

## Git & Source Inventory

```bash
git status --short
rg --files src/tradecraft tests docs | sort
rg --files src/tradecraft | sort > /tmp/hermes_source_files.txt
rg --files tests | sort > /tmp/hermes_test_files.txt
rg --files docs | sort > /tmp/hermes_doc_files.txt
rg -n "def .*\\(|class .*\\(|@app\\.|APIRouter|add_api_route" src/tradecraft
```

## Runtime Files

```bash
find .runtime -maxdepth 1 -type f | sort > /tmp/hermes_runtime_files.txt
find .runtime/pids -maxdepth 1 -type f -print -exec cat {} \; > /tmp/hermes_pid_files.txt
find .runtime -maxdepth 2 -type f | sort
find .runtime/pids -maxdepth 1 -type f -print -exec cat {} \;
```

## SQLite Tables

```bash
for db in .runtime/*.db; do echo "### $db"; sqlite3 "$db" ".tables"; done
```

## Focused DB Schemas

```bash
sqlite3 .runtime/kis_blocks.db ".schema"
sqlite3 .runtime/binance_blocks.db ".schema"
sqlite3 .runtime/investment_memory.db ".schema"
sqlite3 .runtime/market_judgment.db ".schema"
sqlite3 .runtime/naver_reports.db ".schema"
sqlite3 .runtime/llm_usage.db ".schema"
```

## Focused Tests

```bash
pytest tests/test_api_smoke.py
pytest tests/test_kis_block_trader.py
pytest tests/test_binance_block_trader.py
pytest tests/test_investment_memory.py
pytest tests/test_market_judgment.py
node --check src/tradecraft/web/static/app.js
```
