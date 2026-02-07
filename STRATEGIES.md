# V1 Strategy + Backtesting Execution Plan (Desktop‑Only, Local‑First)

This document is the **V1-only** architecture and execution plan for adding **strategy management + deterministic backtesting** to the existing local Python/PyQt6/pyqtgraph desktop charting application.

It rewrites the raw `strategies.md` input into explicit V1 tasks, removes ambiguity, **fixes mismatches against the current codebase**, and excludes anything not required for V1.

---

## 0) V1 Scope Summary

### What V1 delivers

#### Local Python strategy discovery + hot reload
- Strategies are single `.py` files on disk.
- Schema-defined parameters generate UI forms automatically.
- Strategies reload on file change without restarting the app (debounced).

#### Deterministic single-symbol backtesting
- Uses existing OHLCV cache in `app/data/ohlcv.sqlite`.
- Optional backfill uses existing Binance REST fetch pipeline.
- Reproducible fill model with **market orders only** (V1).
- Deterministic “range loader” guarantees complete `[start‑warmup, end]` coverage or hard fail.

#### Portfolio + equity curve + trade list
- Bar-by-bar equity curve aligned to **timestamps** (epoch ms).
- Simple trade list (entry/exit, size, pnl).
- Basic stats (return, max drawdown, win rate, profit factor).

#### UI integration
- Strategy dock: select strategy + edit params + run config + run/stop.
- Report dock: equity curve + trade list + stats.
- Chart overlays: entry/exit markers rendered on price pane, positioned by **timestamp** and mapped to chart **index-x** at render time.

### What V1 explicitly does NOT deliver
Everything in the raw strategy doc that goes beyond the minimal backtester and UI:
- Paper trading mode (live feed + simulated fills)
- Live execution adapters (real broker/exchange routing)
- Deep backtester realism (limit/stop lifecycle, partial fills, VWAP depth, latency model, funding schedules, etc.)
- Multi-timeframe (MTF), multi-symbol portfolio backtesting
- Optimization (sweeps, walk-forward, Monte Carlo), replay mode

---

## 1) Constraints & Reality Alignment (from current system)

### Current system facts we must integrate with
- App is local desktop (Python, PyQt6, pyqtgraph). No server.
- Historical OHLCV cached in SQLite: `app/data/ohlcv.sqlite` via:
  - `app/core/data_store.py` (read/write)
  - `app/core/data_fetch.py` (load_recent/load_window/backfill logic)
- Chart lifecycle and performance constraints:
  - `ChartView` orchestrates symbol/timeframe selection, data loads, indicators, and debug reporting.
  - `CandlestickChart` renders with chunked QPicture caching and LOD logic.
- Indicator system exists (registry/runtime/renderer) with hot reload watchers and caching.
- Chart x-axis is **index-based** internally (bars indexed), while bars include timestamps.
- Time is epoch ms; timezone-agnostic.

### V1 design implications
- Backtest must run entirely local in-process, avoid freezing UI (worker thread).
- Strategies must reuse existing NumPy bar normalization patterns (same bar indexing/timestamps as chart & indicators).
- Overlay rendering must avoid performance regressions: chunk caching, LOD, and invalidation keyed on bar-data changes.

### 1.1 Data loading API mismatch (ChartView windowed loads vs backtest range requirement)
**Mismatch:** The plan must not rely on `data_fetch.load_window` for backtests. Current fetch/backfill is UI/window-driven. Backtests require a deterministic guarantee: load full `[start‑warmup, end]` span or hard fail.

**V1 fix:** Add a dedicated deterministic API in `app/core/data_fetch.py`:

- `load_range_bars(symbol, timeframe, start_ts, end_ts, *, allow_fetch=True) -> np.ndarray`

Behavior:
- Query `DataStore` for the full range.
- Validate coverage against expected timeframe step.
- If missing and `allow_fetch=True`, fetch missing segments via existing REST provider, persist, re-query, and re-validate.
- If still missing: **fail run** with a clear missing-range error.

### 1.2 Timestamp vs index axis (overlay stability across window loads)
**Mismatch:** Chart renders in index-x; strategy results are timestamp-addressed; there’s no explicit public timestamp→index API.

**V1 fix:** Make **timestamps canonical** for strategy/backtest artifacts, and add mapping to chart:
- Add `index_for_ts(ts_ms) -> int | None` to `CandlestickChart` (or its data model), implemented via `np.searchsorted(time_array, ts_ms)` + bounds check.
- Invalidate overlay caches whenever chart bars change.

Overlays store `{ts, price, kind, side, label}` and compute index at render time so markers remain correct after backfills/window changes.

### 1.3 Indicator system coupling (NaNs and determinism)
**Mismatch:** Helper functions may produce NaNs and mixed float values at series start.

**V1 fix:** Accept NaNs as deterministic outputs and require strategies to guard warmup:
- `ctx.ind` wraps `app/indicators/helpers.py` as pure vectorized transforms.
- NaNs are allowed; no special casing in engine.
- `ctx.ind` memoizes outputs per run to avoid recompute inside `on_bar`.

### 1.4 SQLite storage operations (second DB)
**Mismatch:** `app/data/` is ignored in git; DB files are runtime artifacts.

**V1 fix:** Use `app/data/strategy.sqlite` and create it **lazily on first run** (not on startup). WAL mode enabled.

### 1.5 Hot‑reload infra duplication
**Mismatch:** App already has hot reload infrastructure (`hot_reload.py`). Creating a separate watcher duplicates logic.

**V1 fix:** Reuse existing hot reload watcher infra for strategies:
- Watch: `app/strategies/builtins/*.py`, `app/strategies/custom/*.py`.
- StrategyRegistry registers callbacks and debouncing via shared infra.

### 1.6 UI docking + settings persistence
**Mismatch:** Docks should persist across sessions via existing QSettings layout restore.

**V1 fix:** Give new docks stable objectNames:
- `StrategyPanelDock`
- `StrategyReportDock`
and let existing layout persistence handle them in the same QSettings namespace used by the app.

### 1.7 “Visible range” run config must be snapshot-based
**Mismatch:** Visible range is volatile during window loads.

**V1 fix:** Define “use visible range” as:
- Snapshot `(ts_min, ts_max)` at Run-click time via `ChartView.get_visible_ts_range_snapshot()`.
- Backtest uses that snapshot only.

### 1.8 Equity curve x-link mismatch
**Mismatch:** `setXLink` assumes same x coordinate system; price chart uses index-x; equity should be timestamp-x.

**V1 fix:** Equity curve uses **timestamp-x** and is synced manually:
- ChartView emits `visible_ts_range_changed(ts_min, ts_max)`.
- Equity widget sets its x-range accordingly.

### 1.9 Strategy overlay rendering integration point
**Mismatch:** No generic overlay renderer API beyond indicators/candles.

**V1 fix:** Add `StrategyOverlayRenderer` (`app/ui/charts/strategy_overlay.py`) with:
- chunked QPicture rendering
- caching keyed by `(run_id, bars_key, visible_range_bucket, lod_level)`
- timestamp→index mapping via `index_for_ts()`

---

## 2) V1 Non‑Goals and Explicit Deferrals
These items are excluded from V1 even if described in the raw strategy doc:

### Execution modes excluded
- Paper trading mode (live data + simulated fills)
- Live execution adapters (real broker/exchange routing)

### Deep backtester excluded
- Partial fills, realistic order lifecycle, stop/limit logic, VWAP depth model, latency simulation, funding schedules, margin model complexity, MAE/MFE diagnostics engine, regime stats, etc.

### Data complexity excluded
- Multi-timeframe (MTF) requests
- Multi-symbol portfolio backtesting
- Cross-strategy netting, contention rules, scheduling
- Replay mode

### Optimization excluded
- Parameter sweeps, grid search, optimizers
- Walk-forward analysis, Monte Carlo, stress tests

### Market realism excluded
- Order book / best bid/ask
- Trade prints for market impact
- Funding rate polling/refresh

V1 focuses on a simple deterministic bar-based backtest sufficient to validate strategies and render outcomes on the chart.

---

## 3) V1 Decisions to Resolve Ambiguity (Locked)

### 3.1 Orders supported in V1
- Market orders only:
  - `ctx.buy(size)` and `ctx.sell(size)` enqueue market orders.
  - `ctx.flatten()` closes the current position.
- No limit/stop/stop-limit; no TIF.
- `ctx.cancel(order_id)` is **not supported** in V1 (no-op + warning).

### 3.2 Fill model (deterministic)
- Signal on bar close; fill on next bar open:
  - Strategy runs on bar index `i` using bar `i` values.
  - Orders placed during `on_bar(i)` execute at `open[i+1]`.
- No same-bar fills.

### 3.3 Slippage + commission (simple, deterministic)
- Slippage model: fixed bps
  - Buy fill = `open[i+1] * (1 + slippage_bps/10000)`
  - Sell fill = `open[i+1] * (1 - slippage_bps/10000)`
- Commission model: fixed bps of notional
  - `fee = abs(size) * fill_price * commission_bps/10000`

### 3.4 Position model (V1 simplification)
- One net position at a time:
  - flat → long → flat
  - flat → short → flat
- No scaling in/out:
  - If long, `buy()` ignored (warn once per run per method).
  - If short, `sell()` ignored (warn once per run per method).
- Closing via `ctx.flatten()` or opposite-side order.

### 3.5 Leverage & margin (minimal guardrail)
- Simple margin check at execution time:
  - `required_margin = abs(size) * fill_price / leverage`
  - Reject order if `required_margin > equity`
- No liquidation, no funding, no margin interest.

### 3.6 Strategy state persistence
- `ctx.state` persists **during a run only**.
- Not persisted across runs in V1.

### 3.7 Logging granularity (and storage decision)
Persist:
- Run record
- Orders (submitted + filled/rejected)
- Trades (closed)
- Equity curve every bar (trading window only)
- Strategy logs: stored in `strategy_messages` table (**locked choice**) (not embedded in orders).

### 3.8 Backtest range & warmup
Run config includes:
- `start_ts`, `end_ts` (epoch ms)
- `warmup_bars` default 200

Bars are loaded for: `[start_ts - warmup, end_ts]`.

Warmup behavior:
- Strategy is executed on warmup bars to build indicators/state.
- Trading is disabled until `time[i] >= start_ts`.
- Calls to `ctx.buy/sell/flatten` while trading disabled warn **once per run per method** and no-op.

### 3.9 Close-on-finish price (locked)
- If a position is open at end of run, close it at **last processed bar close** (`close[last_i]`).
- This avoids missing next open and keeps cancellation finalization consistent.

### 3.10 Percent-of-equity sizing price (locked)
- `ctx.size.percent_equity(pct)` uses **signal-time close[i]** to compute size (no lookahead).
- Fill still occurs next open; slight drift from exact pct is expected and deterministic.

### 3.11 Cancellation behavior (locked)
- Cancel stops the loop, marks run `CANCELED`, and finalizes by closing any open position at **last processed bar close** (same as close-on-finish).

---

## 4) V1 File/Module Layout

### 4.1 Strategy source files (user-facing)
```
app/strategies/
  builtins/            # optional shipped strategies
  custom/              # user strategies (editable)
```

### 4.2 Core engine (V1-only)
```
app/core/strategies/
  registry.py          # discovery + hot reload + schema validation (reuse shared watcher)
  schema.py            # schema types + param resolution helpers
  context.py           # StrategyContext (ctx API) + indicator memoization + logger adapter
  models.py            # Order, Trade, Position, RunConfig dataclasses
  backtest.py          # deterministic backtest loop
  broker.py            # fill price calc + fee/slippage application + margin checks
  portfolio.py         # equity, pnl, drawdown tracking (simple)
  report.py            # normalize results for UI (markers/stats/series)
  store.py             # SQLite persistence for runs/orders/trades/equity/messages
```

Excluded in V1:
- `app/core/strategies/deep/*`

### 4.3 UI (V1-only)
```
app/ui/
  strategy_panel.py         # strategy list + param form + run controls
  strategy_report.py        # stats + trade list + equity curve view
  strategy_equity.py        # equity curve widget (timestamp-x; manual sync)
  charts/strategy_overlay.py  # chart overlay renderer (timestamp markers -> index-x)
```

### 4.4 Required additions to existing app modules (V1)
- `app/core/data_fetch.py`: add `load_range_bars(...)` for deterministic backtests.
- `app/ui/charts/candlestick_chart.py` (or its data model): add `index_for_ts(ts_ms)` and invalidate overlay caches on bars updates.
- `app/ui/chart_view.py`: add:
  - `get_visible_ts_range_snapshot()`
  - signal `visible_ts_range_changed(ts_min, ts_max)`
  - `jump_to_ts(ts_ms)` helper (loads window around ts then centers)

---

## 5) Strategy Contract (V1)

### 5.1 Required functions
Each strategy is one `.py` file with:
- `schema() -> dict`
- `on_init(ctx)`
- `on_bar(ctx, i)`

### 5.2 Optional functions (supported but minimal)
- `on_order(ctx, order)`
- `on_trade(ctx, trade)`
- `on_finish(ctx)`

### 5.3 Schema format (V1 canonical)
V1 supports input types: `int`, `float`, `bool`, `select`.

```python
def schema():
    return {
        "id": "ema_cross",
        "name": "EMA Cross",
        "inputs": {
            "fast": {"type": "int", "default": 12, "min": 1, "max": 200},
            "slow": {"type": "int", "default": 26, "min": 1, "max": 200},
            "size": {"type": "float", "default": 1.0, "min": 0.001, "max": 100.0}
        },
        "meta": {"version": "1.0"}  # optional
    }
```

### 5.4 Validation rules (V1)
- `id`: required, unique, `[a-z0-9_]+`
- `name`: required
- `inputs`: dict of input specs; each must include:
  - `type`, `default`
  - `min/max` required for numeric types
  - `options` required for `select`
- Unknown fields allowed under `meta` only; ignored elsewhere.

Invalid schema: registry reports error; last valid remains visible/selectable.

---

## 6) Strategy Context API (ctx) — V1 Surface

### 6.1 Data access (NumPy arrays)
- `ctx.bars` → `np.ndarray` shape `(n, 6)` columns:
  - `[time, open, high, low, close, volume]` (epoch ms + floats)
- Convenience:
  - `ctx.time`, `ctx.open`, `ctx.high`, `ctx.low`, `ctx.close`, `ctx.volume`

### 6.2 Indicators (reuse existing helper math)
- `ctx.ind` exposes vectorized indicator functions backed by `app/indicators/helpers.py`.
- `ctx.ind` memoizes results by `(fn_name, args_signature, kwargs_signature)` per run.
- NaNs are accepted and deterministic.

### 6.3 Orders (V1)
- `ctx.buy(size)` → enqueue market buy (executed next bar open)
- `ctx.sell(size)` → enqueue market sell (executed next bar open)
- `ctx.flatten()` → enqueue close-position action (executed next bar open)
- `ctx.cancel(order_id)` → not supported in V1 (warn + no-op)

### 6.4 Portfolio/position (V1)
- `ctx.position.size` (float; +long / -short / 0 flat)
- `ctx.position.entry_price`
- `ctx.position.unrealized_pnl`
- `ctx.portfolio.cash`
- `ctx.portfolio.equity`
- `ctx.portfolio.drawdown`
- `ctx.portfolio.max_drawdown`

### 6.5 Params/logging/state
- `ctx.params` resolved param dict
- `ctx.state` run-lifetime dict
- `ctx.logger.info/warn/error`:
  - stores in-memory for UI
  - persists to `strategy_messages` in SQLite

### 6.6 Sizing helpers (V1 minimal)
- `ctx.size.fixed(units)` → returns units
- `ctx.size.percent_equity(pct)` → returns units:
  - `units = (ctx.portfolio.equity * pct * leverage) / ctx.close[i]` (signal-time close)
- Excluded: risk-based sizing in V1.

---

## 7) Backtesting Engine (V1)

### 7.1 Data pipeline (deterministic)
Backtest runner uses:
- `DataStore` (`app/core/data_store.py`) for cached OHLCV reads.
- New deterministic loader: `data_fetch.load_range_bars(...)` for full coverage.

V1 behavior:
- Load bars for:
  - `symbol`, `timeframe`, `(start_ts - warmup)`, `end_ts`
- If coverage missing:
  - attempt backfill via existing REST provider
  - if still missing: **fail run** with explicit missing range

### 7.2 Bar loop (V1 canonical)
Definitions:
- `i` indexes current bar (signal bar).
- fills happen on `i+1` open.

Pseudo-sequence:
1) Normalize bars into arrays; create ctx.
2) Call `strategy.on_init(ctx)` once.
3) For `i in range(0, n-1)`:
   - Mark-to-market at `close[i]`:
     - update unrealized pnl and equity
     - record equity snapshot if `time[i] >= start_ts`
   - Execute any orders scheduled for bar `i` open (queued at `i-1`).
   - If `time[i] < start_ts`:
     - trading disabled; `buy/sell/flatten` no-op (warn once per run per method)
   - Else:
     - call `strategy.on_bar(ctx, i)`
     - queue any orders for execution at open of `i+1`
   - Check cancel flag every N bars (e.g., 100) to keep UI responsive.
4) If canceled:
   - mark run CANCELED
   - if position open: close at last processed `close[i]` (and finalize trade)
5) Else normal end:
   - if position open: close at `close[n-1]`

### 7.3 End-of-run close convention (locked)
- Close at last processed bar close, with slippage/fees applied using that close price.
- This is the only fill-on-close behavior in V1.

### 7.4 Determinism guardrails (V1)
- No randomness.
- Bars are fixed before simulation starts.
- Use float64 NumPy; no display rounding in engine.
- Store config + params as JSON for reproducibility.

---

## 8) Persistence (SQLite) — V1

### 8.1 Storage location
- `app/data/strategy.sqlite` (WAL mode), created lazily on first run.

### 8.2 Tables (V1 required)

**strategy_runs**
- run_id TEXT PK
- created_at INTEGER (epoch ms)
- strategy_id TEXT
- strategy_name TEXT
- strategy_path TEXT
- symbol TEXT
- timeframe TEXT
- start_ts INTEGER
- end_ts INTEGER
- warmup_bars INTEGER
- initial_cash REAL
- leverage REAL
- commission_bps REAL
- slippage_bps REAL
- status TEXT (RUNNING|DONE|ERROR|CANCELED)
- params_json TEXT
- error_text TEXT NULL

**strategy_orders**
- id INTEGER PK AUTOINCREMENT
- run_id TEXT
- submitted_ts INTEGER
- fill_ts INTEGER NULL
- side TEXT (BUY|SELL|FLATTEN)
- size REAL
- fill_price REAL NULL
- fee REAL NULL
- status TEXT (SUBMITTED|FILLED|REJECTED|CANCELED)
- reason TEXT NULL

**strategy_trades**
- id INTEGER PK AUTOINCREMENT
- run_id TEXT
- side TEXT (LONG|SHORT)
- size REAL
- entry_ts INTEGER
- entry_price REAL
- exit_ts INTEGER
- exit_price REAL
- pnl REAL
- fee_total REAL
- bars_held INTEGER

**strategy_equity**
- id INTEGER PK AUTOINCREMENT
- run_id TEXT
- ts INTEGER
- equity REAL
- drawdown REAL
- position_size REAL
- price REAL

**strategy_messages** (**V1 locked**)
- id INTEGER PK AUTOINCREMENT
- run_id TEXT
- ts INTEGER
- level TEXT (INFO|WARN|ERROR)
- message TEXT
- bar_ts INTEGER NULL

### 8.3 Indexes (V1)
- strategy_equity(run_id, ts)
- strategy_orders(run_id, submitted_ts)
- strategy_trades(run_id, entry_ts)
- strategy_messages(run_id, ts)

### 8.4 Store API (V1)
`StrategyStore` in `app/core/strategies/store.py` provides:
- create_run(...)
- update_run_status(run_id, status, error_text=None)
- insert_order_event(...)
- insert_trade(...)
- insert_equity_point(...)
- insert_message(...)
- load_latest_run_for(symbol, timeframe, strategy_id) (for UI restore)

---

## 9) Report & Overlay Schema (V1)

### 9.1 In-memory report object
`StrategyReport` (in `report.py`) contains:
- run_id
- stats dict:
  - total_return_pct
  - max_drawdown_pct
  - num_trades
  - win_rate_pct
  - profit_factor
- equity_series arrays aligned by timestamp:
  - ts[], equity[], drawdown[]
- trades[] list
- markers[] for chart overlay (timestamp-addressed):
  - entry marker at entry_ts/entry_price
  - exit marker at exit_ts/exit_price with pnl label (optional)

### 9.2 Overlay rendering (timestamp → index)
`StrategyOverlayRenderer`:
- receives markers (timestamp-addressed)
- maps `ts -> index` using `index_for_ts(ts)`
- caches chunked QPictures keyed by `(run_id, bars_key, visible_ts_bucket, lod_level)`
- invalidates when chart bars change

Interaction:
- clicking trade in report triggers `ChartView.jump_to_ts(entry_ts)` then highlights trade markers.

---

## 10) UI Integration (V1)

### 10.1 New docks/widgets
StrategyPanel Dock (`app/ui/strategy_panel.py`):
- strategy list (from registry)
- schema-driven param form
- run config:
  - start/end: datetime pickers OR “use visible range”
  - warmup_bars
  - initial_cash
  - leverage
  - commission_bps
  - slippage_bps
- buttons: Run Backtest / Stop / Reset defaults

StrategyReport Dock (`app/ui/strategy_report.py`):
- stats summary
- trades table (click -> jump to chart)
- equity curve widget

Equity curve widget (`app/ui/strategy_equity.py`):
- pyqtgraph PlotWidget with **timestamp-x**
- **manual sync** to chart visible ts range (no setXLink)

Dock persistence:
- set `objectName="StrategyPanelDock"` and `"StrategyReportDock"` so QSettings layout restore captures them.

### 10.2 ChartView integration points (V1)
Modify `app/ui/chart_view.py` to:
- Own a StrategyRegistry instance (or be able to access one owned by MainWindow).
- Provide snapshot helper:
  - `get_visible_ts_range_snapshot() -> (ts_min, ts_max)`
- Emit sync signal:
  - `visible_ts_range_changed(ts_min, ts_max)` whenever view changes
- Provide navigation:
  - `jump_to_ts(ts)`:
    - ensure chart window includes ts (trigger window load/backfill around ts)
    - center view at the computed index (`index_for_ts(ts)`)
- Manage active run lifecycle:
  - on run complete: attach overlay renderer + show report
  - on symbol/timeframe change: clear overlay/report if mismatch

### 10.3 Background execution (required)
Backtest runs on worker thread (QThread/QRunnable):
- emits progress
- emits finished(report) or error(text)
- supports cancellation flag checked every N bars
- updates run status in SQLite

---

## 11) V1 Implementation Task Breakdown (Updated)

### Task 1 — Deterministic range loader (required for correctness)
Deliverables:
- `data_fetch.load_range_bars(symbol, timeframe, start_ts, end_ts, allow_fetch=True)`
- coverage validation + hard-fail error reporting

Acceptance:
- Backtest either gets full coverage or fails clearly with missing-range info.

### Task 2 — Create strategy directories + sample builtin
Deliverables:
- `app/strategies/builtins/ema_cross.py`
- `app/strategies/custom/` created if missing

Acceptance:
- App shows at least one strategy in StrategyPanel.

### Task 3 — Strategy schema + validation
Files:
- `app/core/strategies/schema.py`

Acceptance:
- Invalid schema shows error and strategy remains unavailable.
- Valid schema resolves defaults correctly.

### Task 4 — StrategyRegistry + hot reload (reuse shared watcher)
Files:
- `app/core/strategies/registry.py`

Work:
- Discover `.py` in builtins + custom.
- Register paths in existing hot reload infra (`hot_reload.py`).
- Debounce events 300–800ms.
- Preserve last-known-good module on reload failure.

Acceptance:
- Edit strategy file -> UI updates without restart; failures do not break last good strategy.

### Task 5 — StrategyContext (ctx) with indicator memoization + logger
Files:
- `app/core/strategies/context.py`

Acceptance:
- `ctx.ind` memoizes indicator outputs per run.
- `ctx.logger` logs appear in UI and persist to `strategy_messages`.

### Task 6 — Models (orders, position, trades, config)
Files:
- `app/core/strategies/models.py`

Acceptance:
- Engine and report share stable dataclasses.

### Task 7 — Broker + portfolio primitives (V1-simple)
Files:
- `app/core/strategies/broker.py`
- `app/core/strategies/portfolio.py`

Acceptance:
- Deterministic fills with fixed bps fees/slippage; margin rejects logged and stored.

### Task 8 — Backtest runner (bar loop)
Files:
- `app/core/strategies/backtest.py`

Acceptance:
- EMA cross produces trades/equity.
- Cancel marks run CANCELED and finalizes open position at last processed close.

### Task 9 — StrategyStore (strategy.sqlite) (lazy create)
Files:
- `app/core/strategies/store.py`

Acceptance:
- Results survive restart; includes messages table.

### Task 10 — Report normalization (UI schema)
Files:
- `app/core/strategies/report.py`

Acceptance:
- Stats + markers generated without UI recomputation.

### Task 11 — StrategyPanel UI (dock)
Files:
- `app/ui/strategy_panel.py`

Acceptance:
- “Use visible range” snapshots ts range at Run click time.

### Task 12 — StrategyReport UI (dock) + equity widget
Files:
- `app/ui/strategy_report.py`
- `app/ui/strategy_equity.py`

Acceptance:
- Equity plot is timestamp-x and syncs via `visible_ts_range_changed`.

### Task 13 — Strategy overlay renderer on chart
Files:
- `app/ui/charts/strategy_overlay.py`
- plus chart API additions (`index_for_ts`)

Acceptance:
- Markers render correctly despite window changes; no performance regression during pan/zoom.

### Task 14 — Determinism + correctness tests (minimum)
Files:
- `tests/test_backtest_determinism.py`
- `tests/test_fill_model.py`

Acceptance:
- Re-running produces identical outputs (within float tolerance).

---

## 12) Definition of Done (V1)

V1 is complete when:
- Strategies can be added/edited locally and hot-reloaded.
- Backtest runs deterministically using `load_range_bars()` or fails clearly.
- Results appear in:
  - report dock (stats, equity curve, trade list)
  - price chart overlay (entries/exits)
- Results persist to `app/data/strategy.sqlite` (runs, orders, trades, equity, messages).
- UI stays responsive during backtests (worker thread).
- Core behavior matches the locked decisions in §3.

## V1 backtest semantics (implemented)
- Fee accounting: `Trade.pnl` is net PnL after both entry and exit fees; `Trade.fee_total` includes entry + exit fees.
- Forced close: end-of-run and cancel forced closes fill at bar close with side-aware slippage bps applied and commissions charged on that fill.
- Warmup: `on_bar` runs during warmup to build state, but trading is disabled; `buy/sell/flatten` become no-ops and warn once per run per method.
- Deterministic data coverage: backtests require complete `[start_ts - warmup, end_ts]` OHLCV coverage; missing ranges are fetched (if allowed) or the run hard-fails with explicit missing segments.
- Strategy overlays: kept disabled until the RecursionError root cause is fixed.

---

## 13) V2+ Deferred List (for clarity only; not to implement in V1)
Do not implement in V1:
- Deep backtester modules and realistic order lifecycle
- Limit/stop orders, partial fills
- Paper/live execution modes
- Order book, spread-aware or VWAP slippage
- Funding rates, fee schedules from exchange metadata
- Multi-timeframe and multi-symbol portfolio backtests
- Optimization, sweeps, walk-forward, Monte Carlo
- Replay mode

---

## Appendix A — V1 Example Strategy (builtin)

```python
def schema():
    return {
        "id": "ema_cross",
        "name": "EMA Cross (V1)",
        "inputs": {
            "fast": {"type": "int", "default": 12, "min": 1, "max": 200},
            "slow": {"type": "int", "default": 26, "min": 1, "max": 200},
            "size": {"type": "float", "default": 1.0, "min": 0.001, "max": 100.0},
        }
    }

def on_init(ctx):
    ctx.state["armed"] = True

def on_bar(ctx, i):
    fast = ctx.ind.ema(ctx.close, int(ctx.params["fast"]))
    slow = ctx.ind.ema(ctx.close, int(ctx.params["slow"]))

    if i < 1:
        return

    cross_up = fast[i] > slow[i] and fast[i-1] <= slow[i-1]
    cross_dn = fast[i] < slow[i] and fast[i-1] >= slow[i-1]

    if cross_up and ctx.position.size == 0:
        ctx.buy(float(ctx.params["size"]))

    if cross_dn and ctx.position.size != 0:
        ctx.flatten()
```

---
