from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from typing import Optional

import numpy as np

from core.data_store import DataStore
from core.data_fetch import load_range_bars, timeframe_to_ms
from core.strategies.backtest import run_backtest
from core.strategies.models import RunConfig
from core.strategies.registry import discover_strategies
from core.strategies.report import build_report
from core.strategies.schema import resolve_params
from core.strategies.store import StrategyStore


def _parse_ts(val: str) -> int:
    """
    Parse a timestamp as either:
    - epoch ms integer string
    - ISO date/time (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
    """
    v = val.strip()
    if v.isdigit():
        return int(v)
    dt = datetime.fromisoformat(v)
    return int(dt.timestamp() * 1000)


def _find_strategy(strategy_id: str, root_paths: list[str]):
    infos = discover_strategies(root_paths)
    wanted = strategy_id.strip()
    for info in infos:
        if info.strategy_id == wanted:
            return info
    # Fallback: allow basename match (e.g. ema_cross.py -> ema_cross)
    for info in infos:
        base = os.path.splitext(os.path.basename(info.path))[0]
        if base == wanted:
            return info
    found = ", ".join(sorted({i.strategy_id for i in infos})) if infos else "(none)"
    roots = ", ".join(root_paths)
    raise SystemExit(f"Strategy not found: {wanted}. searched=[{roots}] found=[{found}]")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Headless V2 backtest runner (no UI).")
    ap.add_argument("--exchange", default="binance", help="Exchange id used in the OHLCV cache (default: binance)")
    ap.add_argument("--symbol", required=True, help="Symbol, e.g. BTCUSDT")
    ap.add_argument("--timeframe", required=True, help="Timeframe, e.g. 5m")
    ap.add_argument("--strategy", required=True, help="Strategy id, e.g. ema_cross")
    ap.add_argument("--start", required=True, help="Start ts (epoch ms) or ISO date/time")
    ap.add_argument("--end", required=True, help="End ts (epoch ms) or ISO date/time")
    ap.add_argument("--warmup-bars", type=int, default=200)
    ap.add_argument("--initial-cash", type=float, default=10_000.0)
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--commission-bps", type=float, default=0.0)
    ap.add_argument("--slippage-bps", type=float, default=0.0)
    ap.add_argument(
        "--stress-bars",
        type=int,
        default=0,
        help="Generate N synthetic bars and run the backtest without OHLCV DB/network (optional).",
    )
    ap.add_argument(
        "--persist",
        action="store_true",
        help="Persist the run to strategy.sqlite using the same atomic bundle insert path (works with --stress-bars too).",
    )
    fetch_group = ap.add_mutually_exclusive_group()
    fetch_group.add_argument(
        "--no-fetch",
        action="store_true",
        help="Do not fetch missing OHLCV ranges; hard-fail on gaps (default behavior is to fetch).",
    )
    fetch_group.add_argument(
        "--allow-fetch",
        action="store_true",
        help="Deprecated (now default): fetch missing OHLCV ranges via provider.",
    )
    here = os.path.abspath(os.path.dirname(__file__))
    # This module lives at app/core/strategies/cli.py.
    # We want defaults relative to app/, not repo root.
    default_ohlcv = os.path.normpath(os.path.join(here, "..", "..", "data", "ohlcv.sqlite"))
    default_strats = os.path.normpath(os.path.join(here, "..", "..", "strategies"))
    default_strategy_db = os.path.normpath(os.path.join(here, "..", "..", "data", "strategy.sqlite"))
    ap.add_argument("--ohlcv-db", default=default_ohlcv)
    ap.add_argument("--strategies-root", default=default_strats)
    ap.add_argument("--strategy-db", default=default_strategy_db)

    args = ap.parse_args(argv)

    start_ts = _parse_ts(args.start)
    end_ts = _parse_ts(args.end)
    allow_fetch = not bool(args.no_fetch)

    strategies_root = os.path.abspath(args.strategies_root)
    roots = [
        os.path.join(strategies_root, "builtins"),
        os.path.join(strategies_root, "custom"),
    ]
    missing_dirs = [p for p in roots if not os.path.isdir(p)]
    if missing_dirs:
        raise SystemExit(f"Strategy roots not found: {missing_dirs}. strategies_root={strategies_root}")
    info = _find_strategy(args.strategy, roots)

    tf_ms = timeframe_to_ms(args.timeframe)
    if int(args.stress_bars or 0) > 0:
        n = int(args.stress_bars)
        if n < 2:
            raise SystemExit("--stress-bars must be >= 2")
        # Deterministic synthetic bars: gentle trend + bounded wiggle (no randomness).
        ts0 = int(start_ts - int(args.warmup_bars * tf_ms))
        ts = ts0 + (np.arange(n, dtype=np.int64) * int(tf_ms))
        # In stress mode, ensure the run's end_ts covers the generated data to actually exercise scale paths.
        end_ts = int(ts[-1])
        base = 100.0 + (np.arange(n, dtype=np.float64) * 0.0001)
        wiggle = 0.5 * np.sin(np.arange(n, dtype=np.float64) * 0.01)
        close = base + wiggle
        open_ = np.concatenate(([close[0]], close[:-1]))
        high = np.maximum(open_, close) + 0.1
        low = np.minimum(open_, close) - 0.1
        vol = np.full(n, 100.0, dtype=np.float64)
        bars_np = np.column_stack([ts.astype(np.float64), open_, high, low, close, vol]).astype(np.float64, copy=False)
    else:
        store = DataStore(os.path.abspath(args.ohlcv_db))
        bars = load_range_bars(
            store,
            args.exchange,
            args.symbol,
            args.timeframe,
            start_ts - int(args.warmup_bars * tf_ms),
            end_ts,
            allow_fetch=allow_fetch,
        )
        if not bars:
            raise SystemExit("No bars returned for the requested range.")
        bars_np = np.asarray(bars, dtype=np.float64)

    # Resolve params from schema defaults (no UI values yet).
    schema = info.module.schema()
    params = resolve_params(schema, {})

    cfg = RunConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_ts=start_ts,
        end_ts=end_ts,
        warmup_bars=int(args.warmup_bars),
        initial_cash=float(args.initial_cash),
        leverage=float(args.leverage),
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
        close_on_finish=True,
    )

    result, status = run_backtest(bars_np, info.module, params, cfg)
    report = build_report(
        run_id="cli_run",
        trades=getattr(result, "trades", []),
        equity_ts=getattr(result, "equity_ts", []),
        equity=getattr(result, "equity", []),
        drawdown=getattr(result, "drawdown", []),
    )

    if bool(args.persist):
        run_id = f"cli_{int(time.time()*1000)}_{os.getpid()}"
        created_at = int(time.time() * 1000)
        # Minimal run payload compatible with StrategyStore.insert_complete_run.
        run_payload = {
            "run_id": run_id,
            "created_at": created_at,
            "strategy_id": getattr(info, "strategy_id", args.strategy),
            "strategy_name": getattr(info, "name", args.strategy),
            "strategy_path": getattr(info, "path", ""),
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "start_ts": cfg.start_ts,
            "end_ts": cfg.end_ts,
            "warmup_bars": cfg.warmup_bars,
            "initial_cash": cfg.initial_cash,
            "leverage": cfg.leverage,
            "commission_bps": cfg.commission_bps,
            "slippage_bps": cfg.slippage_bps,
            "status": status,
            "params_json": "{}",
            "error_text": None,
        }
        orders_payload = [
            {
                "submitted_ts": o.submitted_ts,
                "fill_ts": o.fill_ts,
                "side": o.side,
                "size": o.size,
                "fill_price": o.fill_price,
                "fee": o.fee,
                "status": o.status,
                "reason": o.reason,
            }
            for o in getattr(result, "orders", [])
        ]
        trades_payload = [
            {
                "side": t.side,
                "size": t.size,
                "entry_ts": t.entry_ts,
                "entry_price": t.entry_price,
                "exit_ts": t.exit_ts,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "fee_total": t.fee_total,
                "bars_held": t.bars_held,
            }
            for t in getattr(result, "trades", [])
        ]
        equity_payload = [
            {"ts": ts, "equity": eq, "drawdown": dd, "position_size": 0.0, "price": 0.0}
            for ts, eq, dd in zip(getattr(result, "equity_ts", []), getattr(result, "equity", []), getattr(result, "drawdown", []))
        ]
        messages_payload = list(getattr(result, "logs", []))
        sstore = StrategyStore(os.path.abspath(args.strategy_db))
        sstore.insert_complete_run(
            run=run_payload,
            orders=orders_payload,
            trades=trades_payload,
            equity_points=equity_payload,
            messages=messages_payload,
        )
        ok, issues, stats = sstore.verify_run(run_id)
        print(f"persisted run_id={run_id} verify_ok={ok} issues={issues} stats={stats}")

    final_equity = report.equity[-1] if report.equity else float(cfg.initial_cash)
    print(f"status={status} bars={len(bars_np)} trades={len(report.trades)} final_equity={final_equity:.2f}")
    for k, v in report.stats.items():
        print(f"{k}={v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
