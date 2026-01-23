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

## 0.2.2
- Throttled trade stream updates to reduce drag lag.
- Switched timeframe selector to button row.
- Added searchable symbol dropdown.

## 0.3.0
- Made timeframe buttons exclusive to prevent unchecking.
- Auto-load on symbol change.
- Default symbol selection now prefers BTCUSDT on launch.
- Renamed Load to Reset Cache for explicit refetch if there is a need.
- Auto backfill triggers on left-edge pan and keeps the current view.
- Backfill now loads a fixed-size chunk instead of the full cache.
- Capped zoom-out to 1k visible bars with 2k.
- Tracked end-of-history in cache and surfaced a chart label.
- Switched to timestamp-based window loading so only the visible range is rendered.
- Added a debug dock with live render, cache and performance metrics.
- Added weekly and monthly timeframes.
- Stop window loading once the earliest available history is reached.
- Removed the manual Load More button in favor of windowed panning.

## 0.4.0
- Added symbol tabs with a "+" tab and persistence.
- Added per-tab timeframe persistence.
- Periodic time offset resync for countdown accuracy.
- Added chart crosshair cursor with dashed guides.

## 0.4.1
- Added a Window menu to re-open hidden docks.
- Added a Settings menu with a placeholder dialog.
- Added a Themes submenu with a placeholder dialog.

## 0.5.0
- Renamed app to PySuperChart.
- Added a custom title bar and app icon.
- Mouse wheel zoom now only affects the time axis.
- Increased mouse wheel zoom sensitivity.
- Left-drag on the price scale adjusts the y-axis.
- Enabled pyqtgraph grid by default with denser major price ticks.
