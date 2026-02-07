import time
from typing import List, Optional, Tuple

from core.data_store import DataStore
from core.data_providers import binance


def load_recent_bars(
    store: DataStore,
    exchange: str,
    symbol: str,
    timeframe: str,
    bar_count: int,
) -> List[List[float]]:
    now_ms = int(time.time() * 1000)
    interval_ms = timeframe_to_ms(timeframe)
    start_ms = now_ms - (bar_count * interval_ms)

    cached_range = store.get_cached_range(exchange, symbol, timeframe)
    if cached_range is not None:
        _, cached_max = cached_range
        if cached_max < now_ms - interval_ms:
            forward_bars = binance.fetch_ohlcv(symbol, timeframe, cached_max + interval_ms, now_ms)
            store.store_bars(exchange, symbol, timeframe, forward_bars)

    # Always refresh the most recent candle window to avoid stale closes near rollover.
    recent_start = max(0, now_ms - (interval_ms * 2))
    recent = binance.fetch_ohlcv(symbol, timeframe, recent_start, now_ms)
    store.store_bars(exchange, symbol, timeframe, recent)

    cached = store.load_bars(exchange, symbol, timeframe, start_ms, now_ms)
    if cached:
        expected_min = int(bar_count * 0.9)
        has_gap = False
        if len(cached) >= 2:
            prev_ts = int(cached[0][0])
            for row in cached[1:]:
                ts = int(row[0])
                if ts - prev_ts > interval_ms * 1.5:
                    has_gap = True
                    break
                prev_ts = ts
        if len(cached) >= expected_min and not has_gap:
            return [list(row) for row in cached]
        # If the cache is sparse or has gaps inside the requested window, refetch the full window.
        refetch = binance.fetch_ohlcv(symbol, timeframe, start_ms, now_ms)
        store.store_bars(exchange, symbol, timeframe, refetch)
        cached = store.load_bars(exchange, symbol, timeframe, start_ms, now_ms)
        return [list(row) for row in cached]

    bars = binance.fetch_ohlcv(symbol, timeframe, start_ms, now_ms)
    store.store_bars(exchange, symbol, timeframe, bars)
    return [list(row) for row in bars]


def load_cached_bars(
    store: DataStore,
    exchange: str,
    symbol: str,
    timeframe: str,
    bar_count: int,
) -> List[List[float]]:
    cached_range = store.get_cached_range(exchange, symbol, timeframe)
    if cached_range is None:
        return []
    min_ts, max_ts = cached_range
    interval_ms = timeframe_to_ms(timeframe)
    start_ms = max(min_ts, max_ts - (bar_count * interval_ms))
    cached = store.load_bars(exchange, symbol, timeframe, start_ms, max_ts)
    return [list(row) for row in cached]


def load_cached_full(
    store: DataStore,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> List[List[float]]:
    cached_range = store.get_cached_range(exchange, symbol, timeframe)
    if cached_range is None:
        return []
    min_ts, max_ts = cached_range
    cached = store.load_bars(exchange, symbol, timeframe, min_ts, max_ts)
    return [list(row) for row in cached]


def load_symbols(store: DataStore, exchange: str, max_age_sec: int = 86400) -> List[str]:
    now = int(time.time())
    last_fetch = store.get_symbols_last_fetch(exchange)
    if last_fetch is not None and (now - last_fetch) < max_age_sec:
        cached = store.get_symbols(exchange)
        if cached:
            return cached

    symbols = binance.fetch_symbols()
    store.store_symbols(exchange, symbols, now)
    return symbols


def load_more_history(
    store: DataStore,
    exchange: str,
    symbol: str,
    timeframe: str,
    bar_count: int,
    current_min_ts: Optional[int] = None,
    current_max_ts: Optional[int] = None,
) -> List[List[float]]:
    cached_range = store.get_cached_range(exchange, symbol, timeframe)
    if cached_range is None:
        return load_recent_bars(store, exchange, symbol, timeframe, bar_count)

    min_ts, max_ts = cached_range
    if current_min_ts is None:
        current_min_ts = min_ts
    if current_max_ts is None:
        current_max_ts = max_ts
    oldest_ts, oldest_reached = store.get_history_limit(exchange, symbol, timeframe)
    if oldest_reached and oldest_ts is not None and current_min_ts <= oldest_ts:
        return [list(row) for row in store.load_bars(exchange, symbol, timeframe, current_min_ts, current_max_ts)]
    interval_ms = timeframe_to_ms(timeframe)
    new_start = max(0, current_min_ts - (bar_count * interval_ms))
    if new_start >= current_min_ts:
        return [list(row) for row in store.load_bars(exchange, symbol, timeframe, current_min_ts, current_max_ts)]

    cached_prev = store.load_bars(exchange, symbol, timeframe, new_start, current_min_ts - 1)
    expected_count = int((current_min_ts - 1 - new_start) / interval_ms) + 1 if interval_ms > 0 else 0
    has_gap = False
    if cached_prev and len(cached_prev) >= max(1, int(expected_count * 0.9)):
        prev_ts = int(cached_prev[0][0])
        for row in cached_prev[1:]:
            ts = int(row[0])
            if ts - prev_ts > interval_ms * 1.5:
                has_gap = True
                break
            prev_ts = ts
    else:
        has_gap = True

    if has_gap:
        bars = binance.fetch_ohlcv(symbol, timeframe, new_start, current_min_ts - 1)
        if bars:
            store.store_bars(exchange, symbol, timeframe, bars)
            try:
                earliest = int(bars[0][0])
            except Exception:
                earliest = None
            if earliest is not None and earliest >= current_min_ts:
                store.set_history_limit(exchange, symbol, timeframe, current_min_ts, True)
        else:
            store.set_history_limit(exchange, symbol, timeframe, current_min_ts, True)
            return [list(row) for row in store.load_bars(exchange, symbol, timeframe, current_min_ts, current_max_ts)]
    merged = store.load_bars(exchange, symbol, timeframe, new_start, current_max_ts)
    return [list(row) for row in merged]


def load_window_bars(
    store: DataStore,
    exchange: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> List[List[float]]:
    if start_ms >= end_ms:
        return []
    interval_ms = timeframe_to_ms(timeframe)
    cached = store.load_bars(exchange, symbol, timeframe, start_ms, end_ms)
    if cached:
        has_gap = False
        prev_ts = int(cached[0][0])
        for row in cached[1:]:
            ts = int(row[0])
            if interval_ms > 0 and ts - prev_ts > interval_ms * 1.5:
                has_gap = True
                break
            prev_ts = ts

        cached_start = int(cached[0][0])
        cached_end = int(cached[-1][0])
        missing_ranges: List[Tuple[int, int]] = []
        if interval_ms > 0:
            if cached_start > start_ms + int(interval_ms * 0.5):
                missing_ranges.append((start_ms, cached_start - interval_ms))
            if cached_end < end_ms - int(interval_ms * 0.5):
                missing_ranges.append((cached_end + interval_ms, end_ms))

        if not has_gap and missing_ranges:
            for miss_start, miss_end in missing_ranges:
                if miss_start >= miss_end:
                    continue
                bars = binance.fetch_ohlcv(symbol, timeframe, miss_start, miss_end)
                if bars:
                    store.store_bars(exchange, symbol, timeframe, bars)
            cached = store.load_bars(exchange, symbol, timeframe, start_ms, end_ms)
            return [list(row) for row in cached]

        expected_min = int((end_ms - start_ms) / interval_ms) if interval_ms > 0 else 0
        if expected_min > 0:
            expected_min = int(expected_min * 0.9)
        if not has_gap and (expected_min <= 0 or len(cached) >= expected_min):
            return [list(row) for row in cached]

    cached_range = store.get_cached_range(exchange, symbol, timeframe)
    min_ts = cached_range[0] if cached_range else None

    bars = binance.fetch_ohlcv(symbol, timeframe, start_ms, end_ms)
    if not bars and min_ts is not None and start_ms <= min_ts:
        time.sleep(0.5)
        bars = binance.fetch_ohlcv(symbol, timeframe, start_ms, end_ms)
        if not bars:
            earliest = _find_earliest_ohlcv(symbol, timeframe)
            if earliest is not None:
                reached = min_ts <= earliest
                store.set_history_limit(exchange, symbol, timeframe, earliest, reached)
            else:
                store.set_history_limit(exchange, symbol, timeframe, min_ts, True)
    if bars:
        store.store_bars(exchange, symbol, timeframe, bars)
    cached = store.load_bars(exchange, symbol, timeframe, start_ms, end_ms)
    return [list(row) for row in cached]


def load_range_bars(
    store: DataStore,
    exchange: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    allow_fetch: bool = True,
) -> List[List[float]]:
    if start_ms >= end_ms:
        return []
    interval_ms = timeframe_to_ms(timeframe)
    now_ms = int(time.time() * 1000)
    if end_ms > now_ms + (interval_ms * 2):
        # End is meaningfully in the future; treat as user/config error for deterministic backtests.
        raise ValueError(f"Backtest end_ts is in the future: {end_ms} > {now_ms}")

    def _missing_ranges(rows: List[List[float]]) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]], List[Tuple[int, int]], List[Tuple[int, int]]]:
        if not rows:
            all_missing = [(start_ms, end_ms)]
            return all_missing, all_missing, [], []
        leading: List[Tuple[int, int]] = []
        gaps: List[Tuple[int, int]] = []
        trailing: List[Tuple[int, int]] = []

        if int(rows[0][0]) > start_ms + int(interval_ms * 0.5):
            leading.append((start_ms, int(rows[0][0]) - interval_ms))
        prev_ts = int(rows[0][0])
        for row in rows[1:]:
            ts = int(row[0])
            if interval_ms > 0 and ts - prev_ts > interval_ms * 1.5:
                gaps.append((prev_ts + interval_ms, ts - interval_ms))
            prev_ts = ts
        if int(rows[-1][0]) < end_ms - int(interval_ms * 0.5):
            trailing.append((int(rows[-1][0]) + interval_ms, end_ms))

        # Keep single-bar gaps (s == e) so the loader can fetch/validate them deterministically.
        def _norm(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
            return [(int(s), int(e)) for s, e in ranges if int(s) <= int(e)]

        leading = _norm(leading)
        gaps = _norm(gaps)
        trailing = _norm(trailing)

        # Encode categories in the message, but keep fetch loop using a flat list.
        all_missing = leading + gaps + trailing
        return all_missing, leading, gaps, trailing

    cached = store.load_bars(exchange, symbol, timeframe, start_ms, end_ms)
    cached_list = [list(row) for row in cached]
    missing, leading, gaps, trailing = _missing_ranges(cached_list)
    if missing and allow_fetch:
        # Iterate: fetching a range can still leave gaps (rate limits, provider limits).
        for _ in range(3):
            if not missing:
                break
            for miss_start, miss_end in missing:
                if miss_start > miss_end:
                    continue
                bars = binance.fetch_ohlcv(symbol, timeframe, miss_start, miss_end)
                if bars:
                    store.store_bars(exchange, symbol, timeframe, bars)
            cached = store.load_bars(exchange, symbol, timeframe, start_ms, end_ms)
            cached_list = [list(row) for row in cached]
            missing, leading, gaps, trailing = _missing_ranges(cached_list)
    if missing:
        # Stable, user-facing formatting: leading/gaps/trailing.
        raise ValueError(f"Missing OHLCV data: leading={leading} gaps={gaps} trailing={trailing}")
    return cached_list


def timeframe_to_ms(timeframe: str) -> int:
    if not timeframe:
        return 60_000
    unit = timeframe[-1].lower()
    try:
        mult = int(timeframe[:-1])
    except (ValueError, TypeError):
        return 60_000
    if unit == 'm':
        return mult * 60_000
    if unit == 'h':
        return mult * 3_600_000
    if unit == 'd':
        return mult * 86_400_000
    if unit == 'w':
        return mult * 7 * 86_400_000
    if unit == 'M':
        return mult * 30 * 86_400_000
    return 60_000


def ensure_history_floor(
    store: DataStore,
    exchange: str,
    symbol: str,
    timeframe: str,
    max_iters: int = 12,
) -> Optional[int]:
    oldest_ts, oldest_reached = store.get_history_limit(exchange, symbol, timeframe)
    if oldest_ts is not None:
        if not oldest_reached:
            cached_range = store.get_cached_range(exchange, symbol, timeframe)
            if cached_range:
                min_ts, _ = cached_range
                if min_ts <= oldest_ts:
                    store.set_history_limit(exchange, symbol, timeframe, oldest_ts, True)
        return oldest_ts
    earliest = _find_earliest_ohlcv(symbol, timeframe, max_iters=max_iters)
    if earliest is not None:
        cached_range = store.get_cached_range(exchange, symbol, timeframe)
        if cached_range:
            min_ts, _ = cached_range
            reached = min_ts <= earliest
        else:
            reached = False
        store.set_history_limit(exchange, symbol, timeframe, earliest, reached)
    return earliest


def _find_earliest_ohlcv(
    symbol: str,
    timeframe: str,
    max_iters: int = 12,
) -> Optional[int]:
    interval_ms = timeframe_to_ms(timeframe)
    if interval_ms <= 0:
        return None
    now_ms = int(time.time() * 1000)
    low = 0
    high = now_ms - interval_ms
    candidate: Optional[int] = None

    for _ in range(max_iters):
        if low > high:
            break
        mid = (low + high) // 2
        end = min(now_ms, mid + interval_ms * 5)
        try:
            bars = binance.fetch_ohlcv(symbol, timeframe, mid, end)
        except Exception:
            bars = []
        if bars:
            candidate = int(bars[0][0])
            high = mid - interval_ms
        else:
            low = mid + interval_ms
    return candidate
