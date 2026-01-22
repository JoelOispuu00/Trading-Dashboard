# PySuperChart (PyQt6 + pyqtgraph)

Desktop charting app with TradingView-like dark theme, candlestick/Renko(TODO) charting, hot-reloadable indicators(TODO), and cached exchange data (SQLite).

## Current Status - V1 WIP
- PyQt6 app scaffolded with dark theme and chart view.
- Candlestick chart rendering wired with volume overlay and gridlines.
- Binance OHLCV fetch + SQLite caching implemented.
- Symbol/timeframe changes auto-load and reuse cached data when available.
- Live chart updates (WebSocket) and price marker on the right axis.
- Startup cache forward-fill and backfill are in place.
- Auto backfill triggers when panning to the left edge (cache-first, API fallback).
- Backfill loads fixed-size chunks so older history is incremental.
- Hover shows OHLC + change stats for any candle.
- Live updates now use both kline and trade streams for faster ticks.
- Zoom-out is capped at 1k visible bars to keep rendering stable with 2k buffer.
- Cache tracks end-of-history and shows a chart label when reached.
- Candles render on a timestamp axis with windowed loading around the visible range.
- Window loading stops at the earliest available history once reached.
- Debug dock shows live performance and window metrics.
- Symbol tabs above the chart support quick switching, adding via "+", and persistence.
- Each symbol tab stores its own timeframe.
- Crosshair cursor with dashed X/Y guides follows the mouse.
- Window menu lets you re-open hidden docks.
- Settings dialog stub added under the Settings menu.
- Theme editor stub added under Settings → Themes.

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
- Reset Cache fetches recent bars for the selected symbol/timeframe, caches them, and renders.
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
