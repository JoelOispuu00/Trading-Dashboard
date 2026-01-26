# Indicators

This file documents the full indicator architecture, module contract, runtime API (ctx),
render instruction API, and the planned implementation flow.

## Architecture overview
- Indicators are math-only modules that return render instructions.
- A runtime layer validates inputs, applies lookback, and normalizes outputs.
- A renderer layer owns pyqtgraph items (lines/bands/hist/markers/regions).
- Indicators are global across tabs (same set + params for all symbols).
- Indicators can live in the price pane or their own pane.
- Indicators can be hidden/unhidden, moved between panes, or removed.
- Copy/paste indicator instances for fast reuse.
- Recompute triggers: candle close, window change, param change, file change.
- Compute scope: window + lookback buffer (lookback = max_length * 2).
- If lookback exceeds window, window loader fetches more data before compute.
- Indicator math uses NumPy arrays; list-of-lists are only for rendering.
- Cache computed arrays per indicator instance; recompute only on close.
- Fill/shading defaults to lightweight regions; full fills are optional due to OpenGL cost.

## Planned files
```
app/indicators/
  helpers.py             # indicator math helpers (ctx)
  runtime.py             # compute + normalize outputs + lookback
  renderer.py            # render instruction bridge -> pyqtgraph items
  builtins/              # built-in indicators
  custom/                # user-defined indicators
app/core/
  indicator_registry.py  # discovery + hot-reload
  hot_reload.py          # polling watcher for indicator files
app/ui/
  indicator_panel.py     # indicator list + parameter UI
```

## Built-in indicators (V1)
- EMA / SMA / WMA / RMA / HMA / VWMA
- RSI / Stoch / Stoch RSI
- MACD (line + histogram)
- Bollinger Bands / Keltner Channels / Donchian Channels
- ATR / ADX / DMI
- CCI / ROC / Momentum
- Supertrend / Parabolic SAR
- HSI (RSI + Stoch hybrid)

## Module contract
Each indicator module should expose:

- `schema() -> dict`
  - Returns `id`, `name`, `pane`, and `inputs`.
- `compute(bars, params, ctx) -> dict`
  - `bars`: list of lists `[ts_ms, open, high, low, close, volume]`
  - Returns render instructions (series/bands/hist/markers/regions).

Example (see `app/indicators/example_indicator/indicator.py`):
```
{
  "id": "ema",
  "name": "EMA",
  "pane": "price",
  "inputs": {
    "length": {"type": "int", "default": 20, "min": 1, "max": 500},
    "source": {"type": "select", "default": "close", "options": ["open","high","low","close"]},
    "color": {"type": "color", "default": "#00C853"}
  }
}
```

## Render instruction API (output)
Indicators return:
```
{
  "pane": "price" | "new",
  "series": [
    {"type":"line", "id":"ema", "values":[...], "color":"#00C853", "width":1}
  ],
  "bands": [
    {"type":"band", "id":"bb", "upper":[...], "lower":[...], "fill":"#00C85333"}
  ],
  "hist": [
    {"type":"hist", "id":"macd_hist", "values":[...], "color_up":"#00C853", "color_down":"#EF5350"}
  ],
  "markers": [
    {"time":ts_ms, "price":p, "shape":"arrow_up", "color":"#00C853", "text":"BUY"}
  ],
  "regions": [
    {"start_ts":..., "end_ts":..., "color":"#1E88E533"}
  ]
}
```

### Render API fields (full list)
`series` items (continuous data):
- `type`: `"line"` | `"step"` | `"scatter"`
- `id`: unique per indicator
- `values`: list aligned to bars length
- `color`: hex or rgba
- `width`: int
- `style`: `"solid"` | `"dash"` | `"dot"`
- `opacity`: 0..1
- `z`: optional z-order

`bands` items (upper/lower fill):
- `type`: `"band"`
- `id`
- `upper`: list aligned to bars length
- `lower`: list aligned to bars length
- `fill`: color (with alpha)
- `edge_color`: optional outline color
- `edge_width`: int
- `opacity`: 0..1

`hist` items (histogram):
- `type`: `"hist"`
- `id`
- `values`: list aligned to bars length
- `color_up`: color for positive bars
- `color_down`: color for negative bars
- `opacity`: 0..1
- `base`: baseline value (default 0)

`markers` items (signals/labels):
- `time`: ts_ms
- `price`: price (for price pane)
- `value`: y-value (for new panes)
- `shape`: `"arrow_up"` | `"arrow_down"` | `"circle"` | `"square"` | `"triangle"` | `"diamond"`
- `color`
- `size`: int
- `text`: optional label
- `when`: optional condition string (see conditions)

`regions` items (background blocks):
- `start_ts`, `end_ts`
- `color` (with alpha)
- `opacity`: 0..1
- `when`: optional condition string (see conditions)

`levels` items (horizontal levels):
- `value`: price/y
- `color`
- `style`: `"solid"` | `"dash"` | `"dot"`
- `width`: int
- `when`: optional condition string (see conditions)

### Conditions (optional)
`when` allows conditional rendering without emitting explicit timestamps.
Supported expressions (first pass):
- comparisons: `a > b`, `a < b`, `a >= b`, `a <= b`, `a == b`
- crossovers: `cross(a,b)`, `crossover(a,b)`, `crossunder(a,b)`
- series names can reference returned series by `id` (e.g., `hsi`, `signal`)
- levels can be numeric literals (e.g., `hsi > 88.6`)

If `when` is provided:
- `regions` become per-bar background spans.
- `markers` render on bars where `when` is true (use `time/value` if provided, otherwise bar time).
- `levels` render only when the condition is true.

Notes:
- `values`, `upper`, `lower` align to bars length (pad with NaN as needed).
- `pane="new"` creates a dedicated pane (oscillators, histograms).
- `markers` use absolute `time` (ts_ms) + `price` (for price pane) or y-value (for new panes).

## ctx helper API (frozen for v1)
Data access:
- `ctx.series(bars, "open"|"high"|"low"|"close"|"volume")`
- `ctx.time(bars)`
- `ctx.ohlc(bars)` (dict of arrays)
- `ctx.hl2(bars)` / `ctx.hlc3(bars)` / `ctx.ohlc4(bars)`
- `ctx.change(values)`
 - `ctx.request(timeframe, source)` (multi-timeframe series)

Lookback + alignment:
- `ctx.lookback(n)`
- `ctx.align(values)`
- `ctx.shift(values, n)`
- `ctx.nz(values, default=0.0)`

Moving averages:
- `ctx.sma(values, length)`
- `ctx.ema(values, length)`
- `ctx.wma(values, length)`
- `ctx.rma(values, length)`
- `ctx.vwma(values, length, volume_series=None)`
- `ctx.hma(values, length)`

Oscillators:
- `ctx.rsi(values, length)`
- `ctx.stoch(high, low, close, k_len, d_len)`
- `ctx.macd(values, fast, slow, signal)` -> (macd, signal, hist)
- `ctx.cci(high, low, close, length)`
- `ctx.momentum(values, length)`
- `ctx.roc(values, length)`

Volatility:
- `ctx.atr(high, low, close, length)`
- `ctx.stdev(values, length)`
- `ctx.bb(values, length, mult)` -> (upper, basis, lower)
- `ctx.keltner(high, low, close, length, mult)`

Trend / Directional:
- `ctx.adx(high, low, close, length)`
- `ctx.dmi(high, low, close, length)` -> (+DI, -DI)
- `ctx.supertrend(high, low, close, length, mult)`
- `ctx.psar(high, low, accel, max_accel)`

Helpers / conditions:
- `ctx.cross(a, b)`
- `ctx.crossover(a, b)`
- `ctx.crossunder(a, b)`
- `ctx.highest(values, length)`
- `ctx.lowest(values, length)`
- `ctx.percentile(values, length, p)`
- `ctx.slope(values, length)`
- `ctx.linreg(values, length)`

Math utils:
- `ctx.max(a, b)`
- `ctx.min(a, b)`
- `ctx.abs(values)`
- `ctx.mean(values)`
- `ctx.sum(values)`

## Planned hot-reload
- Watch `app/indicators/builtins/*.py`
- Watch `app/indicators/custom/*.py`
- Debounce file events (300-800ms)
- Recompute active indicators on change
- Surface errors in the error dock without clearing last good render
- Block compute until required lookback data is available

## Repainting rules (no-repaint policy)
- Indicators only recompute on candle close (no intrabar repainting).
- Runtime blocks compute until required lookback is available.
- Outputs must align to bars length (pad with NaN, never shift forward).
- Helpers must not use future bars.
- Window changes can trigger recompute, but values must remain aligned and deterministic.
- Multi-timeframe series repeat last HTF/LTF value until the next higher/lower candle closes.
- Multi-timeframe series are computed on candle close only (no intrabar updates).

## Multi-timeframe (MTF) indicators
- `ctx.request(timeframe, source)` returns a series aligned to the base chart.
- Supports both higher and lower timeframes.
- Alignment rule: repeat last value until the next candle of the requested timeframe closes.
- Runtime must ensure the requested timeframe bars are cached; if not, it blocks compute and triggers fetch.

## UI plan
- Indicator list panel with add/remove.
- Auto-generated settings form from `schema()`.
- Per-indicator header row below OHLC:
  - Hide/Unhide, Settings, Close icons on hover.
- Move indicator between panes.
- Copy/paste indicator instances between panes.
- Reset to defaults (per-field + whole indicator).
- Errors routed to Error dock, last good render stays visible.
 - Drag UI for moving indicators between panes (pane_id based).

## Execution plan (phased)
1) Implement runtime + ctx helpers (math + lookback) using NumPy arrays.
2) Implement renderer bridge (lines, bands, hist, markers, regions) with fill modes.
3) Implement registry + hot reload (load schema + compute).
4) Add built-in indicators (EMA, RSI, MACD, BB).
5) Wire UI (list, settings, header controls).
6) Auto-fetch lookback on demand (window loader).

## Current state
- Registry + hot-reload are wired for builtins/custom folders.
- Indicator runtime helpers and renderer bridge are in place.
- Built-in indicators are split under `app/indicators/builtins/`.
- `market_mode.py` exists as a separate module but is not wired to UI.
- `ctx.request()` (MTF) is planned but stubbed until auto-fetch lookback is wired.
- Render `when` conditions are specified but not yet evaluated by the renderer.


## Implementation notes (decisions)
- Pane identity: `pane_id` is assigned per pane; indicators reference a `pane_id`.
- Pane movement: drag UI to move indicators between panes.
- Multiple instances: required, with unique instance ids (e.g., `ema#1`, `ema#2`).
- Series alignment: runtime will pad to bars length (NaN) and enforce length match.
- Markers in non-price panes: use `value` for y-coordinate (not price).
- Render defaults: runtime applies defaults if fields are omitted.
- Settings persistence: store indicator configs in SQLite (no versioning yet).

## Example: EMA indicator
```python
def schema():
    return {
        "id": "ema",
        "name": "EMA",
        "pane": "price",
        "inputs": {
            "length": {"type": "int", "default": 20, "min": 1, "max": 500},
            "source": {"type": "select", "default": "close", "options": ["open","high","low","close"]},
            "color": {"type": "color", "default": "#00C853"}
        }
    }

def compute(bars, params, ctx):
    src = ctx.series(bars, params["source"])
    values = ctx.ema(src, params["length"])
    return {
        "pane": "price",
        "series": [
            {
                "type": "line",
                "id": "ema",
                "values": values,
                "color": params["color"],
                "width": 1
            }
        ]
    }
```

## Example: HSI (RSI + Stoch hybrid, Pine-style)
```python
def schema():
    return {
        "id": "hsi",
        "name": "HSI",
        "pane": "new",
        "inputs": {
            "rsi_length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "stoch_length": {"type": "int", "default": 14, "min": 1, "max": 200},
            "smooth_k": {"type": "int", "default": 3, "min": 1, "max": 50},
            "hsi_smooth1": {"type": "int", "default": 5, "min": 1, "max": 200},
            "hsi_smooth2": {"type": "int", "default": 3, "min": 1, "max": 200},
            "use_ema": {"type": "bool", "default": True},
            "ob_level": {"type": "float", "default": 88.6, "min": 0, "max": 100, "step": 0.1},
            "os_level": {"type": "float", "default": 11.3, "min": 0, "max": 100, "step": 0.1},
            "show_hist": {"type": "bool", "default": True},
            "show_signal": {"type": "bool", "default": True},
            "show_arrows": {"type": "bool", "default": True},
            "show_bg": {"type": "bool", "default": True},
            "hsi_color": {"type": "color", "default": "#42A5F5"},
            "signal_color": {"type": "color", "default": "#FFA726"},
            "hist_color": {"type": "color", "default": "#FFFFFF99"},
            "ob_color": {"type": "color", "default": "#FF6B6B"},
            "os_color": {"type": "color", "default": "#4ECDC4"},
            "bg_overbought": {"type": "color", "default": "#FF00001A"},
            "bg_oversold": {"type": "color", "default": "#00C8531A"},
        }
    }

def compute(bars, params, ctx):
    close = ctx.series(bars, "close")
    high = ctx.series(bars, "high")
    low = ctx.series(bars, "low")

    rsi_val = ctx.rsi(close, params["rsi_length"])
    k_raw, _ = ctx.stoch(high, low, close, params["stoch_length"], params["smooth_k"])
    k = ctx.sma(k_raw, params["smooth_k"])
    hsi_raw = (rsi_val * k) / 100.0

    smooth1 = params["hsi_smooth1"]
    smooth2 = params["hsi_smooth2"]
    if params["use_ema"]:
        hsi_fast = ctx.ema(hsi_raw, smooth1)
        hsi = ctx.ema(hsi_fast, smooth2)
        signal = ctx.ema(hsi, max(1, smooth2 * 2))
    else:
        hsi_fast = ctx.sma(hsi_raw, smooth1)
        hsi = ctx.sma(hsi_fast, smooth2)
        signal = ctx.sma(hsi, max(1, smooth2 * 2))
    hist = hsi - signal

    series = [
        {"type": "line", "id": "hsi", "values": hsi, "color": params["hsi_color"], "width": 2}
    ]
    if params["show_signal"]:
        series.append({"type": "line", "id": "signal", "values": signal, "color": params["signal_color"], "width": 1})

    hist_items = []
    if params["show_hist"]:
        hist_items.append({"type": "hist", "id": "hist", "values": hist, "color_up": params["hist_color"], "color_down": params["hist_color"]})

    regions = []
    if params["show_bg"]:
        regions.append({"start_ts": None, "end_ts": None, "color": params["bg_overbought"], "when": "hsi>ob"})
        regions.append({"start_ts": None, "end_ts": None, "color": params["bg_oversold"], "when": "hsi<os"})

    markers = []
    if params["show_arrows"]:
        markers.append({"time": None, "value": None, "shape": "triangleup", "color": "#00C853", "when": "crossup"})
        markers.append({"time": None, "value": None, "shape": "triangledown", "color": "#EF5350", "when": "crossdn"})

    return {
        "pane": "new",
        "series": series,
        "hist": hist_items,
        "levels": [
            {"value": params["ob_level"], "color": params["ob_color"], "style": "dash", "width": 1},
            {"value": params["os_level"], "color": params["os_color"], "style": "dash", "width": 1},
            {"value": 50, "color": "#9E9E9E99", "style": "dash", "width": 1},
        ],
        "regions": regions,
        "markers": markers,
    }
```
