import os
import sys
import tempfile
import unittest

import numpy as np

# Allow `import core.*` like the app does when running `python app/main.py`.
REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
APP_DIR = os.path.join(REPO_ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.data_store import DataStore
from core.data_fetch import load_range_bars, timeframe_to_ms


class RangeLoaderTests(unittest.TestCase):
    def _make_store(self) -> tuple[DataStore, str]:
        tmp = tempfile.NamedTemporaryFile(prefix="ohlcv_test_", suffix=".sqlite", delete=False)
        tmp.close()
        return DataStore(tmp.name), tmp.name

    def _cleanup_db_files(self, path: str) -> None:
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.remove(p)
            except Exception:
                pass

    def _store_bars(self, store: DataStore, *, exchange: str, symbol: str, timeframe: str, ts_list: list[int]) -> None:
        bars = []
        for ts in ts_list:
            # ts, open, high, low, close, volume
            bars.append([ts, 10.0, 11.0, 9.0, 10.5, 100.0])
        store.store_bars(exchange, symbol, timeframe, bars)

    def test_load_range_bars_missing_leading_raises(self):
        store, path = self._make_store()
        try:
            exchange = "binance"
            symbol = "TEST"
            timeframe = "1m"
            interval = timeframe_to_ms(timeframe)
            start = 0
            end = 10 * interval

            # Missing leading: first cached bar starts at 2*interval.
            self._store_bars(
                store,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                ts_list=[2 * interval, 3 * interval, 4 * interval],
            )
            with self.assertRaises(ValueError) as ctx:
                load_range_bars(store, exchange, symbol, timeframe, start, end, allow_fetch=False)
            msg = str(ctx.exception)
            self.assertIn("Missing OHLCV data", msg)
            self.assertIn(f"({start}, {2 * interval - interval})", msg)
        finally:
            self._cleanup_db_files(path)

    def test_load_range_bars_internal_gap_raises(self):
        store, path = self._make_store()
        try:
            exchange = "binance"
            symbol = "TEST"
            timeframe = "1m"
            interval = timeframe_to_ms(timeframe)
            start = 0
            end = 10 * interval

            # Internal gap: skip 6*interval; delta from 5->7 is 2*interval (> 1.5*interval).
            ts_list = [i * interval for i in range(0, 11) if i != 6]
            self._store_bars(store, exchange=exchange, symbol=symbol, timeframe=timeframe, ts_list=ts_list)
            with self.assertRaises(ValueError) as ctx:
                load_range_bars(store, exchange, symbol, timeframe, start, end, allow_fetch=False)
            msg = str(ctx.exception)
            self.assertIn("Missing OHLCV data", msg)
            self.assertIn(f"({6 * interval}, {6 * interval})", msg)
        finally:
            self._cleanup_db_files(path)

    def test_load_range_bars_missing_trailing_raises(self):
        store, path = self._make_store()
        try:
            exchange = "binance"
            symbol = "TEST"
            timeframe = "1m"
            interval = timeframe_to_ms(timeframe)
            start = 0
            end = 10 * interval

            # Missing trailing: last cached bar ends at 8*interval.
            self._store_bars(
                store,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                ts_list=[i * interval for i in range(0, 9)],
            )
            with self.assertRaises(ValueError) as ctx:
                load_range_bars(store, exchange, symbol, timeframe, start, end, allow_fetch=False)
            msg = str(ctx.exception)
            self.assertIn("Missing OHLCV data", msg)
            self.assertIn(f"({9 * interval}, {end})", msg)
        finally:
            self._cleanup_db_files(path)


if __name__ == "__main__":
    unittest.main()
