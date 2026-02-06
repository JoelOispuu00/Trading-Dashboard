import os
import sys
import types
import unittest

import numpy as np

# Allow `import core.*` like the app does when running `python app/main.py`.
REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
APP_DIR = os.path.join(REPO_ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.strategies.backtest import run_backtest
from core.strategies.models import RunConfig


def _make_module(on_bar):
    return types.SimpleNamespace(on_bar=on_bar, on_init=lambda ctx: None)


class BacktestCoreTests(unittest.TestCase):
    def test_fill_model_next_open(self):
        # time, open, high, low, close, volume
        bars = np.array(
            [
                [0, 10, 12, 9, 11, 100],
                [1, 11, 13, 10, 12, 100],
                [2, 12, 14, 11, 13, 100],
                [3, 13, 15, 12, 14, 100],
            ],
            dtype=np.float64,
        )

        def on_bar(ctx, i):
            if i == 0:
                ctx.buy(1.0)
            if i == 1:
                ctx.flatten()

        module = _make_module(on_bar)
        cfg = RunConfig(
            symbol="TEST",
            timeframe="1m",
            start_ts=0,
            end_ts=3,
            warmup_bars=0,
            initial_cash=1000.0,
            leverage=1.0,
            commission_bps=0.0,
            slippage_bps=0.0,
        )
        result, status = run_backtest(bars, module, {}, cfg)
        self.assertEqual(status, "DONE")
        self.assertGreaterEqual(len(result.orders), 2)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        # buy filled at open[1] == 11, sell at open[2] == 12
        self.assertEqual(trade.entry_price, 11)
        self.assertEqual(trade.exit_price, 12)
        self.assertEqual(trade.pnl, 1.0)

    def test_trade_fee_accounting_entry_and_exit(self):
        bars = np.array(
            [
                [0, 10, 12, 9, 11, 100],
                [1, 11, 13, 10, 12, 100],
                [2, 12, 14, 11, 13, 100],
                [3, 13, 15, 12, 14, 100],
            ],
            dtype=np.float64,
        )

        def on_bar(ctx, i):
            if i == 0:
                ctx.buy(1.0)
            if i == 1:
                ctx.flatten()

        module = _make_module(on_bar)
        cfg = RunConfig(
            symbol="TEST",
            timeframe="1m",
            start_ts=0,
            end_ts=3,
            warmup_bars=0,
            initial_cash=1000.0,
            leverage=1.0,
            commission_bps=100.0,  # 1%
            slippage_bps=0.0,
        )
        result, status = run_backtest(bars, module, {}, cfg)
        self.assertEqual(status, "DONE")
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]

        entry_fee = 11.0 * 0.01
        exit_fee = 12.0 * 0.01
        gross_pnl = 1.0
        expected_fee_total = entry_fee + exit_fee
        expected_net = gross_pnl - expected_fee_total
        expected_final_cash = 1000.0 + expected_net

        self.assertAlmostEqual(trade.fee_total, expected_fee_total, places=10)
        self.assertAlmostEqual(trade.pnl, expected_net, places=10)
        # After the trade is closed, equity at the last recorded point equals cash.
        self.assertAlmostEqual(result.equity[-1], expected_final_cash, places=10)

    def test_forced_close_applies_slippage(self):
        bars = np.array(
            [
                [0, 10, 12, 9, 11, 100],
                [1, 11, 13, 10, 12, 100],
                [2, 12, 14, 11, 13, 100],
                [3, 13, 15, 12, 14, 100],  # last close == 14
            ],
            dtype=np.float64,
        )

        def on_bar(ctx, i):
            if i == 0:
                ctx.buy(1.0)
            # Never flatten; forced close on finish.

        module = _make_module(on_bar)
        cfg = RunConfig(
            symbol="TEST",
            timeframe="1m",
            start_ts=0,
            end_ts=3,
            warmup_bars=0,
            initial_cash=1000.0,
            leverage=1.0,
            commission_bps=0.0,
            slippage_bps=100.0,  # 1%
            close_on_finish=True,
        )
        result, status = run_backtest(bars, module, {}, cfg)
        self.assertEqual(status, "DONE")
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertLess(trade.exit_price, 14.0)  # long forced close sells below close when slippage > 0
        self.assertAlmostEqual(trade.exit_price, 14.0 * 0.99, places=10)

    def test_margin_rejection_reason(self):
        bars = np.array(
            [
                [0, 10, 12, 9, 11, 100],
                [1, 11, 13, 10, 12, 100],
                [2, 12, 14, 11, 13, 100],
            ],
            dtype=np.float64,
        )

        def on_bar(ctx, i):
            if i == 0:
                ctx.buy(100.0)  # should fail margin at open[1]

        module = _make_module(on_bar)
        cfg = RunConfig(
            symbol="TEST",
            timeframe="1m",
            start_ts=0,
            end_ts=2,
            warmup_bars=0,
            initial_cash=10.0,
            leverage=1.0,
            commission_bps=0.0,
            slippage_bps=0.0,
        )
        result, status = run_backtest(bars, module, {}, cfg)
        self.assertEqual(status, "DONE")
        self.assertGreaterEqual(len(result.orders), 1)
        self.assertEqual(result.orders[0].status, "REJECTED")
        self.assertEqual(result.orders[0].reason, "margin")

    def test_warmup_warn_once_and_no_orders(self):
        bars = np.array(
            [
                [0, 10, 12, 9, 11, 100],
                [1, 11, 13, 10, 12, 100],
                [2, 12, 14, 11, 13, 100],
                [3, 13, 15, 12, 14, 100],
            ],
            dtype=np.float64,
        )

        def on_bar(ctx, i):
            # During warmup (ts < start_ts), these should be ignored with warn-once.
            if int(ctx.time[i]) < 2:
                ctx.buy(1.0)
                ctx.flatten()

        module = _make_module(on_bar)
        cfg = RunConfig(
            symbol="TEST",
            timeframe="1m",
            start_ts=2,
            end_ts=3,
            warmup_bars=0,
            initial_cash=1000.0,
            leverage=1.0,
            commission_bps=0.0,
            slippage_bps=0.0,
        )
        result, status = run_backtest(bars, module, {}, cfg)
        self.assertEqual(status, "DONE")
        self.assertEqual(len(result.orders), 0)
        warns = [m for m in result.logs if m.get("level") == "WARN" and "trading disabled" in (m.get("message") or "")]
        # buy + flatten should each warn once.
        self.assertEqual(len(warns), 2)

    def test_short_trade_pnl_sign(self):
        bars = np.array(
            [
                [0, 10, 12, 9, 11, 100],
                [1, 11, 13, 10, 12, 100],
                [2, 12, 14, 11, 13, 100],
                [3, 13, 15, 12, 14, 100],
            ],
            dtype=np.float64,
        )

        def on_bar(ctx, i):
            if i == 0:
                ctx.sell(1.0)  # short entry at open[1] == 11
            if i == 1:
                ctx.flatten()  # close at open[2] == 12

        module = _make_module(on_bar)
        cfg = RunConfig(
            symbol="TEST",
            timeframe="1m",
            start_ts=0,
            end_ts=3,
            warmup_bars=0,
            initial_cash=1000.0,
            leverage=1.0,
            commission_bps=0.0,
            slippage_bps=0.0,
        )
        result, status = run_backtest(bars, module, {}, cfg)
        self.assertEqual(status, "DONE")
        self.assertEqual(len(result.trades), 1)
        self.assertAlmostEqual(result.trades[0].pnl, -1.0, places=10)

    def test_end_ts_boundary_and_forced_close_uses_last_bar_at_or_before_end(self):
        bars = np.array(
            [
                [0, 10, 12, 9, 11, 100],
                [1, 11, 13, 10, 12, 100],  # end_ts will be 1
                [2, 12, 14, 11, 13, 100],
                [3, 13, 15, 12, 14, 100],
            ],
            dtype=np.float64,
        )

        def on_bar(ctx, i):
            if i == 0:
                ctx.buy(1.0)

        module = _make_module(on_bar)
        cfg = RunConfig(
            symbol="TEST",
            timeframe="1m",
            start_ts=0,
            end_ts=1,
            warmup_bars=0,
            initial_cash=1000.0,
            leverage=1.0,
            commission_bps=0.0,
            slippage_bps=0.0,
            close_on_finish=True,
        )
        result, status = run_backtest(bars, module, {}, cfg)
        self.assertEqual(status, "DONE")
        # Equity points should not include ts > end_ts.
        self.assertTrue(all(ts <= 1 for ts in result.equity_ts))
        # Position should be forced-closed on bar ts == 1 (last bar <= end_ts).
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_ts, 1)

    def test_determinism(self):
        bars = np.array(
            [
                [0, 10, 12, 9, 11, 100],
                [1, 11, 13, 10, 12, 100],
                [2, 12, 14, 11, 13, 100],
                [3, 13, 15, 12, 14, 100],
            ],
            dtype=np.float64,
        )

        def on_bar(ctx, i):
            if i == 0:
                ctx.buy(1.0)
            if i == 1:
                ctx.flatten()

        module = _make_module(on_bar)
        cfg = RunConfig(
            symbol="TEST",
            timeframe="1m",
            start_ts=0,
            end_ts=3,
            warmup_bars=0,
            initial_cash=1000.0,
            leverage=1.0,
            commission_bps=0.0,
            slippage_bps=0.0,
        )
        result1, status1 = run_backtest(bars, module, {}, cfg)
        result2, status2 = run_backtest(bars, module, {}, cfg)
        self.assertEqual(status1, "DONE")
        self.assertEqual(status2, "DONE")
        self.assertEqual(result1.equity, result2.equity)
        t1 = [(t.entry_price, t.exit_price, t.pnl) for t in result1.trades]
        t2 = [(t.entry_price, t.exit_price, t.pnl) for t in result2.trades]
        self.assertEqual(t1, t2)


if __name__ == "__main__":
    unittest.main()
