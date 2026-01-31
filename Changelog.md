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
- Polished chart visuals (grid contrast, crosshair, cursor price label, volume styling).
- Fixed volume histogram rendering when using per-bar colors.
- Added OHLC hover band and PNG export.
- Tweaked UI polish (toolbar alignment, tab underline, softer session lines, volume baseline).
- Refined OHLC row positioning, crosshair softness, and price label notch.
- Added cursor time label on the time axis.
- Cursor snapping only within candle range; time pill centered in bottom axis.
- Hid axis border lines on the right and bottom scales.
- Fixed axis style crash on older pyqtgraph versions.
- Removed PlotWidget top border styling.
- Added a subtle tab bar divider.
- Applied global scrollbar styling.
- Added tab hover tint styling.
- Added comma + precision formatting for current and cursor price labels.
- Raised cursor price label z-order above live price label.
- Volume bars pinned to the chart bottom again.
- Toolbar labels removed (self-explanatory controls).
- Added adaptive candle widths with thinner wicks at dense zoom.
- Added OHLC hover pill, candle outline, and cursor intersection dot.
- Added cursor time tick on the time axis and smaller axis tick fonts.
- Added dock tab icons and tightened toolbar spacing.
- Fixed volume bars disappearing when zooming out.

## 0.6.0
- Skip rendering stale cache when too many bars are missing; fetch missing range first.
- Avoid marking end-of-history during windowed fetches.
- Added a Settings action to reset history-end for the current symbol/timeframe.
- Keep live price label and dotted line updating even when the latest candle is outside the window.
- Mark history end using binary search when windowed fetches return no older data.
- Background probe logs earliest-available candle per symbol/timeframe.
- Probe earliest-available candles for all timeframes when opening a symbol.
- History-end flag now updates when earliest floor is known and cache reaches it.
- History-end flag self-heals when cached oldest meets the stored floor.
- Collapse candles into single line when zoomed out beyond 750 visible bars.
- Increased max visible bars to 5k.
- Throttled chart redraws on pan/zoom and added chunked QPicture rendering for candlesticks.
- Added fast-drag mode to skip hover overlays while panning.
- Optimized candlestick rendering with chunk invalidation and cached pens/brushes.
- Windowed backfill now scales with zoom level.
- Debounced windowed backfill to wait until pan/zoom settles.
- Windowed fetch now reuses cached edges and only fetches missing ranges when possible.
- Backfill now re-evaluates the live view range and retriggers when returning to current time.
- Suppressed live updates during fresh symbol/timeframe loads without cache to avoid partial renders.

## 0.7.0
- Added NumPy-backed indicator runtime helpers (ctx) as the foundation for indicators.
- Added a renderer bridge for indicator outputs (lines/bands/hist/markers/regions).
- Implemented indicator discovery and a polling hot-reload watcher.
- Added a full set of built-in indicator modules (MA family, RSI/Stoch, MACD, bands, volatility, trend).
- Wired the indicator UI with persistence, parameter controls, and multi-pane rendering.

## 0.7.1
- Debounced indicator recompute on view changes and render indicators on the visible window + lookback.
- Cached line-mode candle chunks and precomputed OHLC arrays for faster CPU rendering.
- Reworked volume histogram to a QPicture renderer with chunk caching.
- Moved indicator compute, candle normalization, and volume prep into worker threads.
- Added background backfill decision worker and precomputed volume view hints.
- Added compute/normalize/backfill/volume prep timings to the debug dock and throttled live indicator recompute.
- Coalesced live candle redraws and skipped redraws when OHLCV is unchanged.
- Deferred applying fetched bars until view settles and batched candle updates to reduce pan stutter.
- Fixed volume histogram culling by updating its view-aligned bounds on pan/zoom.
- Skipped indicator recompute when the view window is unchanged and capped compute window size.
- Added incremental candle normalization for window slices that are subsets/supersets of current data.
- Debounced crosshair updates and volume view updates to reduce per-mouse-move and pan load.
- Throttled live volume updates to avoid rebuilding the histogram on every trade tick.
- Restored full-view indicator computation on zoomed-out views to avoid truncating left-side plots.
- Live indicator recompute now treats view changes as full-view recomputes to avoid partial plots after zoom.
- Added tail-only volume updates to avoid rebuilding volume chunks on every live tick.
- Cached indicator series buffers and applied tail-only updates for live recomputes.
- Reduced indicator compute allocations by reusing normalized bars and returning NumPy views.
- Skipped indicator recompute when view index range is unchanged.
- Downsampled indicator series/bands/hist when zoomed out to reduce render load.
- Frozen indicator recompute while zoomed out and re-run on idle to smooth panning.
- Throttled backfill decision frequency when zoomed out to avoid extra work during pans.
- Suppressed live indicator and volume histogram recomputes while zoomed out (tail updates still apply).
- Cached indicator outputs per window and reuse cached values on pan/zoom instead of recomputing.
