# PySuperChart

A local, open‑source trading workstation in progress - planned to cover the full workflow from analysis and strategy design to deep backtesting and automation.

**Why it exists**  
Most charting platforms are cloud-locked or technically constrained. PySuperChart stays
local so it’s fast, private, and scalable.

## Key Features
- High-performance charting with windowed loading and LOD
- SQLite cache + live market streams
- Indicator system under development
- Strategy runtime + backtesting architecture planned
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
- Indicator system (hot-reload + renderer) (in progress)
- - Strategy runtime + backtesting (in progress)
- Strategy overlays (entries/exits, stops/targets) (in progress)
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
- Portfolio backtesting (multi-symbol, multi-strategy)
- Walk-forward / Monte Carlo / optimization
- Deep backtester scaling + diagnostics
- Replay mode + advanced analytics

## Run Locally
```bash
pip install PyQt6 pyqtgraph requests numpy websocket-client
python app/main.py
```

## Docs
- `Architecture.md` — system design + roadmap
- `INDICATORS.md` — indicator API and plan
- `STRATEGIES.md` — strategy/backtesting architecture

## License
Apache-2.0
