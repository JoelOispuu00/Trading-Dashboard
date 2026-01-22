# Trading Dashboard (PyQt6 + pyqtgraph)

Desktop charting app with TradingView-like dark theme, candlestick/Renko(TODO) charting, hot-reloadable indicators(TODO), and cached exchange data (SQLite).

## Current Status
- PyQt6 app scaffolded with dark theme and chart view.
- Candlestick chart rendering wired with volume overlay and gridlines.
- Binance OHLCV fetch + SQLite caching implemented.
- Symbol/timeframe selector UI with background fetching.
- Live chart updates (WebSocket) and price marker on the right axis.
- Startup cache forward-fill and backfill are in place.
- Hover shows OHLC + change stats for any candle.
- Live updates now use both kline and trade streams for faster ticks.

## Quick Start
1) Create/activate your Python environment
2) Install dependencies:
```
pip install PyQt6 pyqtgraph requests numpy websocket-client
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
- On startup, cached data is extended forward to the present when needed.
- Live kline updates stream in from Binance WebSocket.
- Closed live candles are persisted to SQLite.

## Next Steps
- Add indicator registry + hot-reload.

## SQLite Cache
- Stored at `app/data/ohlcv.sqlite`.
- Tables:
  - `ohlcv` (exchange, symbol, timeframe, ts_ms, open, high, low, close, volume)
  - `symbols` (exchange, symbol, fetched_at)
