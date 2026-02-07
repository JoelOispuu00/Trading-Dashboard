from __future__ import annotations

import sqlite3
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import Trade
from .report import StrategyReport, build_report


class StrategyStore:
    """
    Writes are often bulk (equity curve per bar). Keep one connection and batch inserts
    to avoid UI stalls after a backtest finishes.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn = conn
        return self._conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at INTEGER,
                    strategy_id TEXT,
                    strategy_name TEXT,
                    strategy_path TEXT,
                    symbol TEXT,
                    timeframe TEXT,
                    start_ts INTEGER,
                    end_ts INTEGER,
                    warmup_bars INTEGER,
                    initial_cash REAL,
                    leverage REAL,
                    commission_bps REAL,
                    slippage_bps REAL,
                    status TEXT,
                    params_json TEXT,
                    error_text TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    submitted_ts INTEGER,
                    fill_ts INTEGER,
                    side TEXT,
                    size REAL,
                    fill_price REAL,
                    fee REAL,
                    status TEXT,
                    reason TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    side TEXT,
                    size REAL,
                    entry_ts INTEGER,
                    entry_price REAL,
                    exit_ts INTEGER,
                    exit_price REAL,
                    pnl REAL,
                    fee_total REAL,
                    bars_held INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_equity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    ts INTEGER,
                    equity REAL,
                    drawdown REAL,
                    position_size REAL,
                    price REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    ts INTEGER,
                    level TEXT,
                    message TEXT,
                    bar_ts INTEGER
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_equity_run_ts ON strategy_equity (run_id, ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_orders_run_ts ON strategy_orders (run_id, submitted_ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_trades_run_ts ON strategy_trades (run_id, entry_ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_msgs_run_ts ON strategy_messages (run_id, ts)")
            conn.commit()

    def create_run(self, run: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO strategy_runs (
                    run_id, created_at, strategy_id, strategy_name, strategy_path,
                    symbol, timeframe, start_ts, end_ts, warmup_bars, initial_cash,
                    leverage, commission_bps, slippage_bps, status, params_json, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.get("run_id"),
                    run.get("created_at"),
                    run.get("strategy_id"),
                    run.get("strategy_name"),
                    run.get("strategy_path"),
                    run.get("symbol"),
                    run.get("timeframe"),
                    run.get("start_ts"),
                    run.get("end_ts"),
                    run.get("warmup_bars"),
                    run.get("initial_cash"),
                    run.get("leverage"),
                    run.get("commission_bps"),
                    run.get("slippage_bps"),
                    run.get("status"),
                    run.get("params_json"),
                    run.get("error_text"),
                ),
            )
            conn.commit()

    def insert_complete_run(
        self,
        *,
        run: Dict[str, Any],
        orders: Optional[Iterable[Dict[str, Any]]] = None,
        trades: Optional[Iterable[Dict[str, Any]]] = None,
        equity_points: Optional[Iterable[Dict[str, Any]]] = None,
        messages: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        """
        Atomically persist a completed run: run row + orders/trades/equity/messages in a single transaction.
        This avoids partial runs if the process is interrupted during backtest finish persistence.
        """
        run_id = run.get("run_id")
        if not run_id:
            raise ValueError("insert_complete_run requires run['run_id']")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """
                    INSERT INTO strategy_runs (
                        run_id, created_at, strategy_id, strategy_name, strategy_path,
                        symbol, timeframe, start_ts, end_ts, warmup_bars, initial_cash,
                        leverage, commission_bps, slippage_bps, status, params_json, error_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.get("run_id"),
                        run.get("created_at"),
                        run.get("strategy_id"),
                        run.get("strategy_name"),
                        run.get("strategy_path"),
                        run.get("symbol"),
                        run.get("timeframe"),
                        run.get("start_ts"),
                        run.get("end_ts"),
                        run.get("warmup_bars"),
                        run.get("initial_cash"),
                        run.get("leverage"),
                        run.get("commission_bps"),
                        run.get("slippage_bps"),
                        run.get("status"),
                        run.get("params_json"),
                        run.get("error_text"),
                    ),
                )
                # Bulk inserts share the same transaction.
                if orders:
                    self._insert_orders_conn(conn, run_id, orders)
                if trades:
                    self._insert_trades_conn(conn, run_id, trades)
                if equity_points:
                    self._insert_equity_conn(conn, run_id, equity_points)
                if messages:
                    self._insert_messages_conn(conn, run_id, messages)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    def update_run_status(self, run_id: str, status: str, error_text: Optional[str] = None) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("UPDATE strategy_runs SET status=?, error_text=? WHERE run_id=?", (status, error_text, run_id))
            conn.commit()

    def insert_order_event(self, run_id: str, order: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO strategy_orders (
                    run_id, submitted_ts, fill_ts, side, size, fill_price, fee, status, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    order.get("submitted_ts"),
                    order.get("fill_ts"),
                    order.get("side"),
                    order.get("size"),
                    order.get("fill_price"),
                    order.get("fee"),
                    order.get("status"),
                    order.get("reason"),
                ),
            )
            conn.commit()

    def insert_trade(self, run_id: str, trade: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO strategy_trades (
                    run_id, side, size, entry_ts, entry_price, exit_ts, exit_price, pnl, fee_total, bars_held
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    trade.get("side"),
                    trade.get("size"),
                    trade.get("entry_ts"),
                    trade.get("entry_price"),
                    trade.get("exit_ts"),
                    trade.get("exit_price"),
                    trade.get("pnl"),
                    trade.get("fee_total"),
                    trade.get("bars_held"),
                ),
            )
            conn.commit()

    def list_recent_runs(self, *, symbol: str, timeframe: str, strategy_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """
                SELECT run_id, created_at, status, start_ts, end_ts
                FROM strategy_runs
                WHERE symbol=? AND timeframe=? AND strategy_id=?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (symbol, timeframe, strategy_id, int(limit)),
            )
            rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append({
                "run_id": r[0],
                "created_at": r[1],
                "status": r[2],
                "start_ts": r[3],
                "end_ts": r[4],
            })
        return out

    def load_run_report(self, run_id: str) -> Optional[StrategyReport]:
        if not run_id:
            return None
        with self._lock:
            conn = self._connect()
            tcur = conn.execute(
                """
                SELECT side, size, entry_ts, entry_price, exit_ts, exit_price, pnl, fee_total, bars_held
                FROM strategy_trades
                WHERE run_id=?
                ORDER BY entry_ts ASC
                """,
                (run_id,),
            )
            trades_rows = tcur.fetchall()
            ecur = conn.execute(
                """
                SELECT ts, equity, drawdown
                FROM strategy_equity
                WHERE run_id=?
                ORDER BY ts ASC
                """,
                (run_id,),
            )
            equity_rows = ecur.fetchall()

        trades: List[Trade] = []
        for r in trades_rows:
            try:
                trades.append(
                    Trade(
                        side=str(r[0]),
                        size=float(r[1]),
                        entry_ts=int(r[2]),
                        entry_price=float(r[3]),
                        exit_ts=int(r[4]),
                        exit_price=float(r[5]),
                        pnl=float(r[6]),
                        fee_total=float(r[7]),
                        bars_held=int(r[8]),
                    )
                )
            except Exception:
                continue

        equity_ts: List[int] = []
        equity: List[float] = []
        drawdown: List[float] = []
        for r in equity_rows:
            try:
                equity_ts.append(int(r[0]))
                equity.append(float(r[1]))
                drawdown.append(float(r[2]))
            except Exception:
                continue

        return build_report(run_id=run_id, trades=trades, equity_ts=equity_ts, equity=equity, drawdown=drawdown)

    def insert_equity_point(self, run_id: str, point: Dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO strategy_equity (run_id, ts, equity, drawdown, position_size, price) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    point.get("ts"),
                    point.get("equity"),
                    point.get("drawdown"),
                    point.get("position_size"),
                    point.get("price"),
                ),
            )
            conn.commit()

    def insert_run_bundle(
        self,
        run_id: str,
        *,
        orders: Optional[Iterable[Dict[str, Any]]] = None,
        trades: Optional[Iterable[Dict[str, Any]]] = None,
        equity_points: Optional[Iterable[Dict[str, Any]]] = None,
        messages: Optional[Iterable[Dict[str, Any]]] = None,
        ) -> None:
        # Single transaction for backtest finish.
        with self._lock:
            conn = self._connect()
            if orders:
                self._insert_orders_conn(conn, run_id, orders)
            if trades:
                self._insert_trades_conn(conn, run_id, trades)
            if equity_points:
                self._insert_equity_conn(conn, run_id, equity_points)
            if messages:
                self._insert_messages_conn(conn, run_id, messages)
            conn.commit()

    @staticmethod
    def _insert_equity_conn(conn: sqlite3.Connection, run_id: str, points: Iterable[Dict[str, Any]]) -> None:
        # Equity curves can be large; batch inserts to avoid building huge in-memory row lists.
        batch: list[tuple] = []
        batch_size = 20000
        sql = "INSERT INTO strategy_equity (run_id, ts, equity, drawdown, position_size, price) VALUES (?, ?, ?, ?, ?, ?)"
        for p in points:
            batch.append((run_id, p.get("ts"), p.get("equity"), p.get("drawdown"), p.get("position_size"), p.get("price")))
            if len(batch) >= batch_size:
                conn.executemany(sql, batch)
                batch.clear()
        if batch:
            conn.executemany(sql, batch)

    @staticmethod
    def _insert_orders_conn(conn: sqlite3.Connection, run_id: str, orders: Iterable[Dict[str, Any]]) -> None:
        rows = [
            (
                run_id,
                o.get("submitted_ts"),
                o.get("fill_ts"),
                o.get("side"),
                o.get("size"),
                o.get("fill_price"),
                o.get("fee"),
                o.get("status"),
                o.get("reason"),
            )
            for o in orders
        ]
        if not rows:
            return
        conn.executemany(
            "INSERT INTO strategy_orders (run_id, submitted_ts, fill_ts, side, size, fill_price, fee, status, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    @staticmethod
    def _insert_trades_conn(conn: sqlite3.Connection, run_id: str, trades: Iterable[Dict[str, Any]]) -> None:
        rows = [
            (
                run_id,
                t.get("side"),
                t.get("size"),
                t.get("entry_ts"),
                t.get("entry_price"),
                t.get("exit_ts"),
                t.get("exit_price"),
                t.get("pnl"),
                t.get("fee_total"),
                t.get("bars_held"),
            )
            for t in trades
        ]
        if not rows:
            return
        conn.executemany(
            "INSERT INTO strategy_trades (run_id, side, size, entry_ts, entry_price, exit_ts, exit_price, pnl, fee_total, bars_held) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    @staticmethod
    def _insert_messages_conn(conn: sqlite3.Connection, run_id: str, messages: Iterable[Dict[str, Any]]) -> None:
        rows = [(run_id, m.get("ts"), m.get("level"), m.get("message"), m.get("bar_ts")) for m in messages]
        if not rows:
            return
        conn.executemany(
            "INSERT INTO strategy_messages (run_id, ts, level, message, bar_ts) VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    def load_latest_run_for(self, symbol: str, timeframe: str, strategy_id: str) -> Optional[str]:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """
                SELECT run_id FROM strategy_runs
                WHERE symbol=? AND timeframe=? AND strategy_id=?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (symbol, timeframe, strategy_id),
            )
            row = cur.fetchone()
            return row[0] if row else None

    @staticmethod
    def _timeframe_to_ms(timeframe: str) -> int:
        # Keep local to avoid importing chart/data modules from core.strategies.
        if not timeframe:
            return 60_000
        unit = timeframe[-1].lower()
        try:
            mult = int(timeframe[:-1])
        except Exception:
            return 60_000
        if unit == "m":
            return mult * 60_000
        if unit == "h":
            return mult * 3_600_000
        if unit == "d":
            return mult * 86_400_000
        if unit == "w":
            return mult * 7 * 86_400_000
        return 60_000

    def verify_run(self, run_id: str) -> Tuple[bool, List[str], Dict[str, Any]]:
        """
        Lightweight integrity check to catch partial/corrupt runs (e.g. crash mid-persist).
        Intended for debug use, not for every UI refresh.
        """
        issues: List[str] = []
        stats: Dict[str, Any] = {"run_id": run_id}
        if not run_id:
            return False, ["missing run_id"], stats

        with self._lock:
            conn = self._connect()
            rcur = conn.execute(
                """
                SELECT status, start_ts, end_ts, warmup_bars, timeframe
                FROM strategy_runs
                WHERE run_id=?
                """,
                (run_id,),
            )
            run_rows = rcur.fetchall()
            if len(run_rows) != 1:
                issues.append(f"strategy_runs rows != 1 (got {len(run_rows)})")
                return False, issues, stats

            status, start_ts, end_ts, warmup_bars, timeframe = run_rows[0]
            status = str(status or "")
            try:
                start_ts = int(start_ts)
                end_ts = int(end_ts)
                warmup_bars = int(warmup_bars or 0)
            except Exception:
                issues.append("invalid run bounds fields (start_ts/end_ts/warmup_bars)")
                return False, issues, stats
            tf_ms = self._timeframe_to_ms(str(timeframe or ""))
            warmup_start = int(start_ts - (warmup_bars * tf_ms))
            stats.update({"status": status, "start_ts": start_ts, "end_ts": end_ts, "warmup_start_ts": warmup_start})

            def _count(table: str) -> int:
                try:
                    return int(conn.execute(f"SELECT COUNT(1) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[0])
                except Exception:
                    return -1

            eq_count = _count("strategy_equity")
            ord_count = _count("strategy_orders")
            trd_count = _count("strategy_trades")
            msg_count = _count("strategy_messages")
            stats.update(
                {
                    "equity_rows": eq_count,
                    "order_rows": ord_count,
                    "trade_rows": trd_count,
                    "message_rows": msg_count,
                }
            )

            if status in ("DONE", "CANCELED") and eq_count <= 0:
                issues.append("strategy_equity rows == 0 for completed run")

            # Equity ts monotonic and within bounds.
            if eq_count > 0:
                ecur = conn.execute(
                    """
                    SELECT ts, equity, drawdown
                    FROM strategy_equity
                    WHERE run_id=?
                    ORDER BY ts ASC
                    """,
                    (run_id,),
                )
                prev_ts: Optional[int] = None
                min_ts: Optional[int] = None
                max_ts: Optional[int] = None
                bad_dd = 0
                non_finite = 0
                for ts, eq, dd in ecur.fetchall():
                    try:
                        ts_i = int(ts)
                    except Exception:
                        issues.append("non-integer equity ts")
                        break
                    if prev_ts is not None and ts_i <= prev_ts:
                        issues.append("equity ts not strictly increasing")
                        break
                    prev_ts = ts_i
                    if min_ts is None or ts_i < min_ts:
                        min_ts = ts_i
                    if max_ts is None or ts_i > max_ts:
                        max_ts = ts_i
                    try:
                        eq_f = float(eq)
                        dd_f = float(dd)
                        if not (eq_f == eq_f and abs(eq_f) != float("inf")):
                            non_finite += 1
                        if not (dd_f == dd_f and abs(dd_f) != float("inf")):
                            non_finite += 1
                        if dd_f < 0.0 or dd_f > 1.0:
                            bad_dd += 1
                    except Exception:
                        non_finite += 1
                stats.update({"equity_min_ts": min_ts, "equity_max_ts": max_ts, "equity_bad_dd": bad_dd, "equity_non_finite": non_finite})
                if min_ts is not None and min_ts < warmup_start:
                    issues.append("equity ts before warmup_start")
                if max_ts is not None and max_ts > end_ts:
                    issues.append("equity ts after end_ts")
                if bad_dd:
                    issues.append(f"equity drawdown out of range (count={bad_dd})")
                if non_finite:
                    issues.append(f"equity non-finite values (count={non_finite})")

            # Orders and trades: required fields non-null.
            # This is intentionally minimal; deeper semantic checks live in unit tests.
            o_bad = 0
            for row in conn.execute(
                """
                SELECT submitted_ts, side, size, status
                FROM strategy_orders
                WHERE run_id=?
                """,
                (run_id,),
            ).fetchall():
                submitted_ts, side, size, ostatus = row
                if submitted_ts is None or side is None or size is None or ostatus is None:
                    o_bad += 1
            if o_bad:
                issues.append(f"orders missing required fields (count={o_bad})")
            t_bad = 0
            for row in conn.execute(
                """
                SELECT side, size, entry_ts, entry_price, exit_ts, exit_price, pnl, fee_total
                FROM strategy_trades
                WHERE run_id=?
                """,
                (run_id,),
            ).fetchall():
                if any(v is None for v in row):
                    t_bad += 1
            if t_bad:
                issues.append(f"trades missing required fields (count={t_bad})")

        return (len(issues) == 0), issues, stats
