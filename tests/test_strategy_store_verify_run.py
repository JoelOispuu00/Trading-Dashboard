import os
import sys
import tempfile
import unittest


# Allow `import core.*` like the app does when running `python app/main.py`.
REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
APP_DIR = os.path.join(REPO_ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


from core.strategies.store import StrategyStore


class StrategyStoreVerifyRunTests(unittest.TestCase):
    def _make_db(self) -> tuple[str, StrategyStore]:
        tmp = tempfile.NamedTemporaryFile(prefix="strategy_test_", suffix=".sqlite", delete=False)
        tmp.close()
        store = StrategyStore(tmp.name)
        return tmp.name, store

    def _cleanup_db_files(self, path: str) -> None:
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.remove(p)
            except Exception:
                pass

    def test_verify_run_ok_for_complete_bundle(self):
        path, store = self._make_db()
        try:
            run_id = "r1"
            store.insert_complete_run(
                run={
                    "run_id": run_id,
                    "created_at": 0,
                    "strategy_id": "s",
                    "strategy_name": "S",
                    "strategy_path": "x.py",
                    "symbol": "TEST",
                    "timeframe": "1m",
                    "start_ts": 0,
                    "end_ts": 2,
                    "warmup_bars": 0,
                    "initial_cash": 1000.0,
                    "leverage": 1.0,
                    "commission_bps": 0.0,
                    "slippage_bps": 0.0,
                    "status": "DONE",
                    "params_json": "{}",
                    "error_text": None,
                },
                equity_points=[
                    {"ts": 0, "equity": 1000.0, "drawdown": 0.0, "position_size": 0.0, "price": 0.0},
                    {"ts": 1, "equity": 1001.0, "drawdown": 0.0, "position_size": 0.0, "price": 0.0},
                    {"ts": 2, "equity": 999.0, "drawdown": 0.002, "position_size": 0.0, "price": 0.0},
                ],
                orders=[],
                trades=[],
                messages=[],
            )
            ok, issues, stats = store.verify_run(run_id)
            self.assertTrue(ok, msg=f"issues={issues} stats={stats}")
        finally:
            store.close()
            self._cleanup_db_files(path)

    def test_verify_run_fails_for_done_with_no_equity(self):
        path, store = self._make_db()
        try:
            run_id = "r2"
            # Intentionally write only the run row.
            store.create_run(
                {
                    "run_id": run_id,
                    "created_at": 0,
                    "strategy_id": "s",
                    "strategy_name": "S",
                    "strategy_path": "x.py",
                    "symbol": "TEST",
                    "timeframe": "1m",
                    "start_ts": 0,
                    "end_ts": 2,
                    "warmup_bars": 0,
                    "initial_cash": 1000.0,
                    "leverage": 1.0,
                    "commission_bps": 0.0,
                    "slippage_bps": 0.0,
                    "status": "DONE",
                    "params_json": "{}",
                    "error_text": None,
                }
            )
            ok, issues, _stats = store.verify_run(run_id)
            self.assertFalse(ok)
            self.assertTrue(any("equity" in s for s in issues))
        finally:
            store.close()
            self._cleanup_db_files(path)


if __name__ == "__main__":
    unittest.main()

