# Architecture

## Scope (v1)
- Desktop app using PyQt6 + pyqtgraph.
- Charting and indicators only (no backtesting yet).
- Two chart types: candlestick and Renko.
- Indicators are hot-reloadable from a folder inside the project.
- Errors from indicators are shown in a dedicated error window/dock.
- TradingView-like dark theme.
- External data providers supported: Binance (initial), with extensible adapters for Hyperliquid and MEXC.
- Local caching for OHLCV using SQLite with incremental fetch on startup (resume from last cached bar).
- Lazy data loading: fetch a bounded recent window first (e.g., 2k-5k bars) so the chart is usable immediately, then extend on demand.

## High-level design
The app is a single desktop process with a clean split:
- UI layer: PyQt6 widgets, chart rendering, user controls.
- Core layer: data models, chart transformations (Renko), indicator registry, and hot-reload.

Indicators are loaded as Python modules from `app/indicators/` and called to compute series. The UI requests indicator updates; the core computes them and returns series to render. When a module changes, a file watcher triggers a reload and re-renders indicators if the indicator is active. Errors are captured and surfaced in the error dock without crashing the app or clearing valid chart data.
Data is fetched on demand based on the chart's visible time range, using a lookback buffer to support indicators that need history. Additional ranges are fetched as the user pans/zooms or changes timeframe/symbol.
Initial load is a bounded recent window so the chart renders fast; background backfill can extend older history in chunks while the chart remains usable.
The symbol list is fetched from the exchange (Binance first) at startup and cached locally; the UI uses this cache for search/selection.
The data store supports explicit historical backfill: requesting older ranges than currently cached (e.g., add 20,000 earlier bars) triggers a bounded fetch for that prior window and merges it into the cache. This is exposed as a UI action (e.g., "Load more history") and a core API that can fetch by bar count or time range.

## Future extensions (post-v1)
- Strategy execution and backtesting.
- Trade execution from the dashboard (broker/exchange adapters, order management, risk controls).
- Market scanner module.
- Deep backtester (Backtrader or custom engine integration).
- Strategy management (hot-reload Python strategies, parameter UI, execution logs).
- Simple drawing tools, trendlines etc

## Folder structure
- `app/main.py`: app entrypoint
- `app/ui/`: PyQt6 widgets and views
  - `main_window.py`: top-level window, layout, docks
  - `chart_view.py`: chart renderer (candles or Renko)
  - `indicator_panel.py`: indicator selection and parameters
  - `error_dock.py`: error display and history
- `app/ui/charts/`: chart renderers and helpers
  - `candlestick_chart.py`: candlestick renderer
  - `renko_chart.py`: renko renderer
  - `line_chart.py`: line renderer
  - `volume_histogram.py`: volume overlay
  - `performance.py`: visible-range + LOD helpers
- `app/ui/theme/`: UI styling and theme tokens
  - `theme.py`: TradingView-like dark palette and style constants
  - `app.qss`: Qt stylesheet for widgets, docks, and panels
- `app/core/`: non-UI logic
  - `data_store.py`: OHLCV source, SQLite cache, incremental fetch
  - `data_providers/`: exchange adapters (Binance first, Hyperliquid/MEXC ready)
  - `renko_builder.py`: Renko transform
  - `indicator_registry.py`: discovery + schema + hot-reload
  - `hot_reload.py`: file watcher + debounce
  - `schema.py`: indicator schema types and validation
- `app/indicators/`: user indicators (folder per indicator)
  - `indicator.py`: indicator module

## Chart types
### Candlestick
- Renders raw OHLCV bars.
- Time axis is UTC seconds.

### Renko
- Built from raw OHLCV (server-side transform, not a view-only trick).
- Uses `renko_builder.py` to generate synthetic bricks for both charting and indicators.
- Renko bricks are price-action based (not time-based); time fields are derived from source data for plotting only.
#### Renko parameters (initial)
- `brick_pct`: fixed brick size as percent of price.
- `use_dynamic_renko`: enable ATR-based dynamic brick sizing.
- `atr_lookback`: ATR period for dynamic mode.
- `atr_multiplier`: scale ATR to compute brick size.
- `min_brick_pct`: floor for dynamic brick size.

## Chart interactions
- Mouse wheel zooms the time scale (TradingView-like behavior).
- Dragging on the price scale (left axis) adjusts price scaling.

## Volume histogram
- Rendered as an overlay in the main chart area (not a separate pane).
- Scales to a fraction of the visible price range (e.g., 10-20% height).
- Uses LOD sampling (based on visible bars) to keep rendering fast.

## LOD (level of detail)
- Use visible range sampling to limit rendering cost when many bars are in view.
- If visible bars exceed a dense threshold (e.g., 2000), render every Nth bar.
- Optionally draw a simplified line path for dense views to maintain continuity.

## Line chart rendering (for non-candlestick views)
- Line series uses downsampling/LOD for large datasets.
- Optional pulsing dot on the latest price for live updates.
- Bid/ask lines are drawn as dashed horizontal lines when live data is available.

## Candlestick rendering
- Custom candlestick item with LOD sampling based on visible range.
- Uses per-bar colors (up/down or indicator highlights).
- Draws a simplified line path in dense views for continuity.
- Optionally overlays a "current" candle and bid/ask lines during live updates.

## Indicator contract
Each indicator module should expose:

- `def schema() -> dict`
  - Provides id, name, and input definitions (type, default, min, max, step).
- `def compute(bars, params) -> dict`
  - `bars`: list of dicts with `time, open, high, low, close, volume`.
  - Returns `{series_name: [(time, value), ...]}`.

Example:
```
{
  "id": "sma",
  "name": "SMA",
  "inputs": {
    "length": {"type": "int", "default": 20, "min": 2, "max": 200}
  }
}
```

## Hot-reload behavior
- Watch `app/indicators/**/indicator.py`.
- On change, compute file hash.
- Debounce 300-800ms.
- If active indicator changed, recompute and re-render.
- On error, show error in error dock and keep last good render.

## Error handling
- Errors are collected with: indicator id, error type, traceback, timestamp.
- Error dock shows latest error and a short history list.
- The chart should remain interactive even with indicator errors.

## Theme
- Dark theme similar to TradingView.
- Define a single theme module or constants for:
  - background, grid, text, axis, candle colors, volume colors.

## Invariants
- All times are UTC seconds.
- Indicators never mutate input data.
- Chart rendering is deterministic for a given dataset + indicator params.
- Cached data is authoritative unless a gap is detected; startup fetch fills from last cached timestamp forward.
- No full-history fetch by default; initial load is bounded to a time window + lookback.
