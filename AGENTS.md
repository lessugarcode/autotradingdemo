# Trading — Agent Guide

## Project

Two independent Python scripts for Binance:

- **`lessugar_futures_paperv2.py`** — Multi-pair paper futures bot (BTC/ETH/SOL/XAU). Uses Binance fAPI (`fapi.binance.com`). No API key required. Saves trades to `lessugar_logs/trades_v4.json`.
- **`sugar_tracker_v2.py`** — Multi-asset technical tracker with CoinGecko whale/F&G signals. Uses `ccxt` (spot Binance). Requires `.env` with `COINGECKO_API_KEY` (optional — tracker works without it).

## Run

```
RUN_FUTURES_BOT.bat         # launches lessugar_futures_paperv2.py
LIVE_PRICE_TRACKER.bat      # launches sugar_tracker_v2.py
```

Or directly:
```
python lessugar_futures_paperv2.py
python sugar_tracker_v2.py
```

## Dependencies

- `pandas`, `pandas_ta`, `colorama`, `requests` (both)
- `ccxt`, `python-dotenv` (tracker only)

No `requirements.txt` exists — install manually.

## Important

- Both scripts run indefinitely; exit with Ctrl+C.
- No tests, no linting, no typechecking config.
- Paper bot config is inline in `CFG` dict (line 22). Adjust risk, pairs, indicator params there.
- Sugar tracker config is inline in `CONFIG` dict (line 20).
- UTF-8 box-drawing characters used — terminal must support them (Windows: `chcp 65001` if garbled).
- Log files (`lessugar_logs/`, `sugar_alerts.log`) are auto-created; gitignored by absence.
