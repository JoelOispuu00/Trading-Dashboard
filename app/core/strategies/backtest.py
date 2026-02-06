from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

from .broker import compute_fill_price, compute_fee, can_fill
from .context import StrategyContext
from .models import Order, Position, Trade, RunConfig, BacktestResult
from .portfolio import mark_to_market, close_position, position_side


def run_backtest(
    bars: np.ndarray,
    strategy_module: object,
    params: Dict[str, Any],
    config: RunConfig,
    cancel_flag: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Tuple[BacktestResult, str]:
    if bars is None or len(bars) < 2:
        raise ValueError("Not enough bars for backtest")

    ctx = StrategyContext(bars, params, config.initial_cash, config.leverage)
    result = BacktestResult()
    last_warn: Dict[str, bool] = {}

    on_init = getattr(strategy_module, "on_init", None)
    on_bar = getattr(strategy_module, "on_bar", None)
    on_order = getattr(strategy_module, "on_order", None)
    on_trade = getattr(strategy_module, "on_trade", None)
    on_finish = getattr(strategy_module, "on_finish", None)

    if on_init:
        on_init(ctx)

    n = len(bars)
    cancel_every = 100
    status = "DONE"

    ts_arr = bars[:, 0].astype(np.int64, copy=False)

    def _last_idx_at_or_before(ts_limit: int) -> int:
        # Rightmost index where ts_arr[idx] <= ts_limit, or -1 if none.
        try:
            idx = int(np.searchsorted(ts_arr, int(ts_limit), side="right") - 1)
        except Exception:
            idx = -1
        return idx

    pending_orders: list[dict] = []
    for i in range(0, n - 1):
        if cancel_flag and i % cancel_every == 0 and cancel_flag():
            status = "CANCELED"
            break
        if progress_cb and i % cancel_every == 0:
            progress_cb(i, n)

        ctx.set_bar_index(i)
        ts = int(bars[i][0])
        if ts > int(config.end_ts):
            # Hard boundary: never process/record beyond end_ts.
            break
        # Execute pending orders at the open of this bar (orders were submitted on bar i-1).
        if pending_orders:
            open_price = float(bars[i][1])
            for o in pending_orders:
                side = str(o.get("side", "")).upper()
                size = float(o.get("size", 0.0))
                if side == "FLATTEN":
                    # Flatten on flat is a clean no-op (no order event).
                    if ctx.position.size == 0:
                        continue
                    side = "SELL" if ctx.position.size > 0 else "BUY"
                    size = float(abs(ctx.position.size))

                order = Order(submitted_ts=int(o.get("submitted_ts", ts)), side=side, size=size)

                if not np.isfinite(size) or size <= 0.0:
                    order.status = "REJECTED"
                    order.reason = "invalid_size"
                    result.orders.append(order)
                    if on_order:
                        on_order(ctx, order)
                    continue

                fill_price = compute_fill_price(open_price, side, config.slippage_bps)
                ok, _ = can_fill(size, fill_price, ctx.portfolio.equity, config.leverage)
                if not ok:
                    order.status = "REJECTED"
                    order.reason = "margin"
                    result.orders.append(order)
                    if on_order:
                        on_order(ctx, order)
                    continue

                fee = compute_fee(size, fill_price, config.commission_bps)
                order.fill_ts = ts
                order.fill_price = fill_price
                order.fee = fee
                order.status = "FILLED"
                result.orders.append(order)

                if ctx.position.size == 0:
                    # Open new position: entry fee is recorded on the position and subtracted from cash once.
                    ctx.position.size = size if side == "BUY" else -size
                    ctx.position.entry_price = fill_price
                    ctx.position.entry_ts = ts
                    ctx.position.entry_fee_total = float(fee)
                    ctx.portfolio.cash -= fee
                    # Keep equity consistent for subsequent fills on the same open.
                    mark_to_market(ctx.portfolio, ctx.position, fill_price)
                else:
                    # Close position (or reject scaling-in/out in V2).
                    if (ctx.position.size > 0 and side == "BUY") or (ctx.position.size < 0 and side == "SELL"):
                        if not last_warn.get("scale", False):
                            ctx.logger.warn("scaling not supported in V2", ts, ts)
                            last_warn["scale"] = True
                        continue

                    entry_price = float(ctx.position.entry_price or fill_price)
                    entry_ts = int(ctx.position.entry_ts or ts)
                    entry_fee_total = float(getattr(ctx.position, "entry_fee_total", 0.0))
                    gross_pnl = (fill_price - entry_price) * ctx.position.size

                    # Cash already reflects entry fees; on exit apply gross pnl and exit fee.
                    ctx.portfolio.cash += gross_pnl
                    ctx.portfolio.cash -= fee

                    fee_total = entry_fee_total + fee
                    trade = Trade(
                        side=position_side(ctx.position) or "LONG",
                        size=abs(ctx.position.size),
                        entry_ts=entry_ts,
                        entry_price=entry_price,
                        exit_ts=ts,
                        exit_price=fill_price,
                        pnl=gross_pnl - fee_total,
                        fee_total=fee_total,
                        bars_held=max(1, int((ts - entry_ts) / max(1, (bars[1][0] - bars[0][0])))),
                    )
                    result.trades.append(trade)
                    if on_trade:
                        on_trade(ctx, trade)
                    close_position(ctx.position)
                    ctx.position.entry_fee_total = 0.0
                    mark_to_market(ctx.portfolio, ctx.position, fill_price)

                if on_order:
                    on_order(ctx, order)

        close_price = float(bars[i][4])
        mark_to_market(ctx.portfolio, ctx.position, close_price)

        if ts >= config.start_ts and ts <= int(config.end_ts):
            result.equity_ts.append(ts)
            result.equity.append(ctx.portfolio.equity)
            result.drawdown.append(ctx.portfolio.drawdown)
            result.position_size.append(ctx.position.size)
            result.price.append(close_price)

        if ts < config.start_ts:
            ctx.trading_enabled = False
            if on_bar:
                on_bar(ctx, i)
            ctx.trading_enabled = True
        else:
            if on_bar:
                on_bar(ctx, i)
        # Orders placed during on_bar(i) should execute at open of bar i+1.
        pending_orders = ctx.pop_orders()

    if status == "CANCELED":
        # Forced close at the last bar at-or-before end_ts (or current bar if earlier).
        close_idx = _last_idx_at_or_before(min(int(config.end_ts), int(ts_arr[min(i, n - 1)])))
        if close_idx < 0:
            close_idx = min(i, n - 1)
        if ctx.position.size != 0 and close_idx >= 0:
            close_price = float(bars[close_idx][4])
            close_side = "SELL" if ctx.position.size > 0 else "BUY"
            fill_price = compute_fill_price(close_price, close_side, config.slippage_bps)
            fee = compute_fee(abs(ctx.position.size), fill_price, config.commission_bps)
            entry_price = float(ctx.position.entry_price or fill_price)
            entry_fee_total = float(getattr(ctx.position, "entry_fee_total", 0.0))
            gross_pnl = (fill_price - entry_price) * ctx.position.size
            ctx.portfolio.cash += gross_pnl - fee
            fee_total = entry_fee_total + fee
            trade = Trade(
                side=position_side(ctx.position) or "LONG",
                size=abs(ctx.position.size),
                entry_ts=int(ctx.position.entry_ts or bars[close_idx][0]),
                entry_price=entry_price,
                exit_ts=int(bars[close_idx][0]),
                exit_price=fill_price,
                pnl=gross_pnl - fee_total,
                fee_total=fee_total,
                bars_held=1,
            )
            result.trades.append(trade)
            close_position(ctx.position)
            ctx.position.entry_fee_total = 0.0
    else:
        if config.close_on_finish and ctx.position.size != 0:
            close_idx = _last_idx_at_or_before(int(config.end_ts))
            if close_idx < 0:
                # No bar at-or-before end_ts; nothing deterministic to do.
                close_idx = -1
            if close_idx < 0:
                pending_orders = []
                result.logs.extend(ctx.get_logs())
                if on_finish:
                    on_finish(ctx)
                return result, status
            close_price = float(bars[close_idx][4])
            close_side = "SELL" if ctx.position.size > 0 else "BUY"
            fill_price = compute_fill_price(close_price, close_side, config.slippage_bps)
            fee = compute_fee(abs(ctx.position.size), fill_price, config.commission_bps)
            entry_price = float(ctx.position.entry_price or fill_price)
            entry_fee_total = float(getattr(ctx.position, "entry_fee_total", 0.0))
            gross_pnl = (fill_price - entry_price) * ctx.position.size
            ctx.portfolio.cash += gross_pnl - fee
            fee_total = entry_fee_total + fee
            trade = Trade(
                side=position_side(ctx.position) or "LONG",
                size=abs(ctx.position.size),
                entry_ts=int(ctx.position.entry_ts or bars[close_idx][0]),
                entry_price=entry_price,
                exit_ts=int(bars[close_idx][0]),
                exit_price=fill_price,
                pnl=gross_pnl - fee_total,
                fee_total=fee_total,
                bars_held=1,
            )
            result.trades.append(trade)
            close_position(ctx.position)
            ctx.position.entry_fee_total = 0.0

    pending_orders = []
    result.logs.extend(ctx.get_logs())
    if on_finish:
        on_finish(ctx)
    return result, status
