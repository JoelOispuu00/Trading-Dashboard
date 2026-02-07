# Architecture

## Scope (V1)
- Desktop app using PyQt6 + pyqtgraph.
- Charting + indicators, plus a V2-simple strategy runtime + deterministic backtesting (single-symbol, single-timeframe).
- Primary chart type: candlestick. Renko to-do.
- Indicators are hot-reloadable from a local folder.
- Indicator errors surface in a dedicated error dock.
- Dark, modern UI theme.
- Modular data providers (crypto exchanges now, brokers later).
- Local OHLCV cache in SQLite with incremental fetch.
- Windowed loading: render a bounded visible range + buffer and extend on demand.

## High-level design
Single-process desktop app with a clean split:
- UI layer: widgets, chart rendering, user controls.
- Core layer: data fetch + cache, transforms, indicator runtime, hot reload.

Indicators are Python modules in `app/indicators/` and are loaded by the registry.
The UI requests indicator updates; the core computes series and returns render
instructions. When a module changes, the file watcher reloads and re-renders
active indicators. Errors are captured and shown in the error dock without
clearing the last good render.

Data is fetched based on the visible chart window with a lookback buffer to
support indicators. Additional ranges are fetched as the user pans/zooms or
changes symbol/timeframe. Initial load is a bounded recent window so the chart
renders fast; background backfill extends older history in chunks while the
chart remains usable.

## Future extensions (post-V1)
- Deep backtester (custom engine) and expanded order lifecycle realism.
- Strategy management expansion (more strategies, richer config, diagnostics, run comparisons).
- Broker/exchange live execution adapters.
- Market scanner module.
- Drawing tools (trendlines, annotations).

## Folder structure
- `app/main.py`: app entrypoint
- `app/ui/`: PyQt6 widgets and views
  - `main_window.py`: top-level window, layout, docks
  - `chart_view.py`: chart controller + data orchestration
  - `indicator_panel.py`: indicator selection + params
  - `error_dock.py`: error display + history
  - `debug_dock.py`: live metrics
  - `strategy_panel.py`: strategy selection + params + run controls
  - `strategy_report.py`: stats + trades table + run history
  - `strategy_equity.py`: equity curve widget
- `app/ui/charts/`: chart renderers + helpers
  - `candlestick_chart.py`: candlestick renderer
  - `renko_chart.py`: renko renderer (planned)
  - `line_chart.py`: line renderer (planned)
  - `volume_histogram.py`: volume overlay
  - `strategy_overlay.py`: entry/exit markers (env-gated while stabilizing)
  - `performance.py`: visible-range + LOD helpers
- `app/ui/theme/`: UI styling and theme tokens
  - `theme.py`: palette + style constants
  - `app.qss`: Qt stylesheet
- `app/core/`: non-UI logic
  - `data_store.py`: SQLite cache + queries
  - `data_fetch.py`: cache-first fetch + window loading
  - `data_providers/`: exchange adapters (Binance first)
  - `indicator_registry.py`: indicator discovery + schema
  - `hot_reload.py`: file watcher + debounce
  - `schema.py`: indicator schema types/validation
  - `strategies/`: V2-simple strategy/backtest engine + persistence (`app/data/strategy.sqlite`)
  - `renko_builder.py`: Renko transform (planned)
- `app/indicators/`: indicator modules
  - `builtins/`: shipped indicators
  - `custom/`: user indicators

## Chart types
### Candlestick
- Renders raw OHLCV bars.
- Time axis uses UTC epoch milliseconds.

### Renko (to-do)
- Built from OHLCV using a transform (not view-only).
- Bricks are price-action based; time fields are derived for plotting.
- Dynamic sizing uses ATR with optional floor.

## Chart interactions
- Mouse wheel zooms time scale.
- Dragging on the price scale adjusts Y-axis.
- Crosshair + hover labels show O/H/L/C + change.

## Volume histogram
- Overlay in the main chart area (not a separate pane).
- Scales to a fraction of the visible price range.
- Uses LOD sampling based on visible bars.

## LOD (level of detail)
- Visible-range sampling limits rendering cost at high bar counts.
- Windowed loading prevents full-history render by default.

## Indicator contract (current)
Each indicator module exposes:
- `schema() -> dict` (id, name, inputs, defaults)
- `compute(bars, params) -> dict`
  - `bars`: NumPy OHLCV arrays
  - returns render payloads (series / bands / markers)

## Hot-reload behavior
- Watch `app/indicators/*.py` (builtins + custom).
- Debounce 300-800ms.
- If an active indicator changes, recompute + re-render.
- On error, show in error dock and keep last good render.

## Error handling
- Errors capture indicator id, error type, traceback, timestamp.
- Error dock shows latest error and a short history.
- Chart remains interactive even with indicator errors.

## Theme
- Dark theme with consistent tokens for background, grid, text, axes,
  candle colors, and volume colors.

## Invariants
- All times are UTC epoch milliseconds.
- Indicators never mutate input data.
- Rendering is deterministic for a given dataset + params.
- Cache is authoritative unless a gap is detected.
- Initial load is a bounded window + lookback; no full-history fetch by default.
