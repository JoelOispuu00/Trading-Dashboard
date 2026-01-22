# Changelog

## 0.1.0 - Initial
- Added `Architecture.md` with full plan and constraints.
- Scaffolded PyQt6 app structure + theme (QSS + palette).
- Implemented Candlestick renderer with LOD + volume overlay.
- Implemented SQLite cache schema for OHLCV and symbol list.
- Added Binance data provider (klines + symbol list).
- Wired ChartView with symbol/timeframe UI, background fetch, and Load More backfill.
- Surfaced fetch, symbol list, and render errors in the error dock.
- Made symbol list loading non-blocking.
- Added Binance retry-once for network failures.
- Added `Readme.md` and `Changelog.md`.

## 0.2.0
- Added startup forward-fill for cached data.
- Added live candle updates via Binance WebSocket.
- Added sparse-cache window repair on startup fetch.
- Added dotted live price line colored by candle direction.
- Rendered candles without LOD downsampling to avoid gaps.
- Persisted closed live candles to SQLite.
- Added live price label with time-to-close.
- Moved price scale to the right and widened it.
- Synced time-to-close to Binance event time.
- Candle hover OHLC stats.

## 0.2.1
- Right-docked tab strip with vertical labels.
- Refreshed latest bars on startup to avoid stale closes near rollover.
- Faster live ticks via trade stream updates.
- Countdown label now refreshes every second.
- UI layout persists across restarts.
