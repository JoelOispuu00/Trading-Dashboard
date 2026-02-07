# PySuperChart

A local, open-source trading workstation in progress - planned to cover the full workflow from analysis and strategy design to deep backtesting and automation.

**Why it exists**  
Most charting platforms are cloud-locked or technically constrained. PySuperChart stays
local so it's fast, private, and scalable.

## Key Features
- High-performance charting with windowed loading and LOD
- SQLite cache + live market streams
- Indicator system (hot-reload + renderer) (core logic done, needs UX work)
- Strategy runtime + base backtesting (core logic done, needs UX work)
- Deep backtester architecture planned
- Clean, modern dark-theme UI

## Tech Stack
- Python + PyQt6 + pyqtgraph
- SQLite cache
- Modular data providers (main exchanges now, brokers later)

## Status
**V1 is WIP.** Core charting is stable, indicators and backtesting are under active development.

## Roadmap

### V1 (WIP)
- Charting + windowed loading + cache (done)
- Polished UI/UX (done)
- Indicator system (hot-reload + renderer) (core logic done, needs UX/UI work)
- Strategy runtime + base backtesting (core logic done, needs UX/UI work)
- Strategy overlays (entries/exits) (env-gated; stability work in progress)
- Renko
- Integrate more exchanges (spot + futures pairs)
- Working settings menus + theme editor

### V2 (planned)
- Deep backtester
- Market realism (fees, funding, slippage models)
- Expanded indicator library
- Drawing tools
- Integrate additional stock exchanges and brokerage platforms
- Export strategies as deployable CLI packages for Linux VPS execution
- Provide in-application monitoring and management for deployed strategies

### V3 (planned)
- Broker/exchange live execution adapters
- Portfolio backtesting (multi-symbol, multi-strategy)
- Walk-forward / Monte Carlo / optimization
- Deep backtester scaling + diagnostics
- Replay mode + advanced analytics
- Market scanner module

## Run Locally
```bash
pip install PyQt6 pyqtgraph requests numpy websocket-client
python app/main.py
```

## Headless tools
```bash
# Show all flags
python -m core.strategies.cli -h

# Run a deterministic, synthetic stress backtest (no network, no OHLCV DB needed)
python -m core.strategies.cli --strategy ema_cross --symbol BTCUSDT --timeframe 5m --start 2025-01-01 --end 2025-01-02 --stress-bars 200000

# Persist a run to app/data/strategy.sqlite (atomic bundle insert)
python -m core.strategies.cli --strategy ema_cross --symbol BTCUSDT --timeframe 5m --start 2025-01-01 --end 2025-01-02 --stress-bars 200000 --persist

# Use real cached OHLCV from app/data/ohlcv.sqlite (and fetch gaps by default)
python -m core.strategies.cli --strategy ema_cross --symbol BTCUSDT --timeframe 5m --start 2025-11-01 --end 2026-02-01

# Force offline mode for OHLCV: hard-fail if any bars are missing (no REST calls)
python -m core.strategies.cli --strategy ema_cross --symbol BTCUSDT --timeframe 5m --start 2025-11-01 --end 2026-02-01 --no-fetch

# Customize execution assumptions
python -m core.strategies.cli --strategy ema_cross --symbol BTCUSDT --timeframe 5m --start 2025-11-01 --end 2026-02-01 --warmup-bars 200 --initial-cash 10000 --leverage 1.0 --commission-bps 2.0 --slippage-bps 1.0

# Use custom DB/roots (defaults are under app/)
python -m core.strategies.cli --strategy ema_cross --symbol BTCUSDT --timeframe 5m --start 2025-11-01 --end 2026-02-01 --ohlcv-db app/data/ohlcv.sqlite --strategy-db app/data/strategy.sqlite --strategies-root app/strategies
```

Notes:
- `--start`/`--end` accept either epoch ms or ISO date/time (`YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS`).
- By default the CLI will attempt to fetch missing OHLCV ranges via the provider; use `--no-fetch` to guarantee offline runs.
- `--stress-bars` generates deterministic synthetic bars and forces `end_ts` to the last generated bar to actually stress scale paths.

## Docs
- `Architecture.md` - system design + roadmap
- `INDICATORS.md` - indicator API and plan
- `STRATEGIES.md` - strategy/backtesting architecture

## License
Apache-2.0
