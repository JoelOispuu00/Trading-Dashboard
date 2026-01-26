import sqlite3
from typing import Iterable, List, Optional, Tuple


class DataStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS ohlcv (
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    PRIMARY KEY (exchange, symbol, timeframe, ts_ms)
                )
                '''
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup ON ohlcv (exchange, symbol, timeframe, ts_ms)'
            )
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS symbols (
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    PRIMARY KEY (exchange, symbol)
                )
                '''
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_symbols_exchange ON symbols (exchange)'
            )
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS ohlcv_limits (
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    oldest_ts INTEGER,
                    oldest_reached INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (exchange, symbol, timeframe)
                )
                '''
            )
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS indicator_instances (
                    instance_id TEXT PRIMARY KEY,
                    indicator_id TEXT NOT NULL,
                    pane_id TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    visible INTEGER NOT NULL DEFAULT 1,
                    sort_index INTEGER NOT NULL DEFAULT 0
                )
                '''
            )

    def get_cached_range(self, exchange: str, symbol: str, timeframe: str) -> Optional[Tuple[int, int]]:
        with self._connect() as conn:
            cur = conn.execute(
                'SELECT MIN(ts_ms), MAX(ts_ms) FROM ohlcv WHERE exchange=? AND symbol=? AND timeframe=?',
                (exchange, symbol, timeframe),
            )
            row = cur.fetchone()
            if row and row[0] is not None and row[1] is not None:
                return int(row[0]), int(row[1])
        return None

    def load_bars(self, exchange: str, symbol: str, timeframe: str, start_ts: int, end_ts: int) -> List[Iterable[float]]:
        with self._connect() as conn:
            cur = conn.execute(
                '''
                SELECT ts_ms, open, high, low, close, volume
                FROM ohlcv
                WHERE exchange=? AND symbol=? AND timeframe=? AND ts_ms BETWEEN ? AND ?
                ORDER BY ts_ms ASC
                ''',
                (exchange, symbol, timeframe, start_ts, end_ts),
            )
            return cur.fetchall()

    def store_bars(self, exchange: str, symbol: str, timeframe: str, bars: List[Iterable[float]]) -> None:
        if not bars:
            return
        rows = []
        for bar in bars:
            if len(bar) < 6:
                continue
            try:
                ts_ms = int(bar[0])
                o = float(bar[1])
                h = float(bar[2])
                l = float(bar[3])
                c = float(bar[4])
                v = float(bar[5])
            except (ValueError, TypeError):
                continue
            rows.append((exchange, symbol, timeframe, ts_ms, o, h, l, c, v))
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                '''
                INSERT OR REPLACE INTO ohlcv
                (exchange, symbol, timeframe, ts_ms, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                rows,
            )

    def get_symbols(self, exchange: str) -> List[str]:
        with self._connect() as conn:
            cur = conn.execute(
                'SELECT symbol FROM symbols WHERE exchange=? ORDER BY symbol ASC',
                (exchange,),
            )
            return [row[0] for row in cur.fetchall()]

    def get_symbols_last_fetch(self, exchange: str) -> Optional[int]:
        with self._connect() as conn:
            cur = conn.execute(
                'SELECT MAX(fetched_at) FROM symbols WHERE exchange=?',
                (exchange,),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return int(row[0])
        return None

    def store_symbols(self, exchange: str, symbols: List[str], fetched_at: int) -> None:
        if not symbols:
            return
        rows = [(exchange, symbol, fetched_at) for symbol in symbols]
        with self._connect() as conn:
            conn.executemany(
                '''
                INSERT OR REPLACE INTO symbols (exchange, symbol, fetched_at)
                VALUES (?, ?, ?)
                ''',
                rows,
            )

    def get_history_limit(self, exchange: str, symbol: str, timeframe: str) -> Tuple[Optional[int], bool]:
        with self._connect() as conn:
            cur = conn.execute(
                '''
                SELECT oldest_ts, oldest_reached
                FROM ohlcv_limits
                WHERE exchange=? AND symbol=? AND timeframe=?
                ''',
                (exchange, symbol, timeframe),
            )
            row = cur.fetchone()
            if row:
                oldest_ts = int(row[0]) if row[0] is not None else None
                oldest_reached = bool(row[1])
                return oldest_ts, oldest_reached
        return None, False

    def set_history_limit(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        oldest_ts: Optional[int],
        oldest_reached: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                '''
                INSERT OR REPLACE INTO ohlcv_limits
                (exchange, symbol, timeframe, oldest_ts, oldest_reached)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (exchange, symbol, timeframe, oldest_ts, int(oldest_reached)),
            )

    def clear_history_limit(self, exchange: str, symbol: str, timeframe: str) -> None:
        with self._connect() as conn:
            conn.execute(
                '''
                DELETE FROM ohlcv_limits
                WHERE exchange=? AND symbol=? AND timeframe=?
                ''',
                (exchange, symbol, timeframe),
            )

    def get_indicator_instances(self) -> List[Tuple[str, str, str, str, bool, int]]:
        with self._connect() as conn:
            cur = conn.execute(
                '''
                SELECT instance_id, indicator_id, pane_id, params_json, visible, sort_index
                FROM indicator_instances
                ORDER BY sort_index ASC
                '''
            )
            rows = cur.fetchall()
        return [(row[0], row[1], row[2], row[3], bool(row[4]), int(row[5])) for row in rows]

    def upsert_indicator_instance(
        self,
        instance_id: str,
        indicator_id: str,
        pane_id: str,
        params_json: str,
        visible: bool,
        sort_index: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                '''
                INSERT OR REPLACE INTO indicator_instances
                (instance_id, indicator_id, pane_id, params_json, visible, sort_index)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (instance_id, indicator_id, pane_id, params_json, int(visible), sort_index),
            )

    def delete_indicator_instance(self, instance_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                '''
                DELETE FROM indicator_instances
                WHERE instance_id=?
                ''',
                (instance_id,),
            )
