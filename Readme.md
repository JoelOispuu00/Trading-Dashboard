# Trading Dashboard (PyQt6 + pyqtgraph)

Desktop charting app with TradingView-like dark theme, candlestick/Renko charting, hot-reloadable indicators, and cached exchange data (SQLite).

## Current Status
- PyQt6 app scaffolded with dark theme and chart view.
- Candlestick renderer wired and working (LOD + volume overlay + gridlines).
- Binance OHLCV fetch + SQLite caching implemented.
- Symbol/timeframe selector UI wired with background fetching.
- Incremental backfill (Load More) button implemented.
- Fetch, symbol list, and render errors surface in the error dock.
- Symbol list loading is non-blocking.
- Binance requests retry once on network failure.
- Volume bars use a green tone.

## Quick Start
1) Create/activate your Python environment
2) Install dependencies:
```
pip install PyQt6 pyqtgraph requests numpy
```
3) Run:
```
python app/main.py
```

## Core Architecture
- `Architecture.md` contains the full design and requirements.
- `app/main.py` loads the Qt stylesheet and starts the app.
- UI lives under `app/ui/`.
- Charts and rendering helpers are in `app/ui/charts/`.
- Core logic (data store, fetch, providers) is in `app/core/`.

## Data Flow (Current)
- Symbol list is pulled from Binance and cached in SQLite (`app/data/ohlcv.sqlite`).
- Load fetches recent bars for the selected symbol/timeframe, caches them, and renders.
- Load More fetches older bars (backfill) and merges with cache.

## Next Steps
- Add indicator registry + hot-reload.
- Chart interactions: mouse wheel zooms time scale; dragging on price axis adjusts price scale.

## SQLite Cache
- Stored at `app/data/ohlcv.sqlite`.
- Tables:
  - `ohlcv` (exchange, symbol, timeframe, ts_ms, open, high, low, close, volume)
  - `symbols` (exchange, symbol, fetched_at)
