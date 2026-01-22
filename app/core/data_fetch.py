import time
from typing import List

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
) -> List[List[float]]:
    cached_range = store.get_cached_range(exchange, symbol, timeframe)
    if cached_range is None:
        return load_recent_bars(store, exchange, symbol, timeframe, bar_count)

    min_ts, max_ts = cached_range
    interval_ms = timeframe_to_ms(timeframe)
    new_start = max(0, min_ts - (bar_count * interval_ms))
    if new_start >= min_ts:
        return [list(row) for row in store.load_bars(exchange, symbol, timeframe, min_ts, max_ts)]

    bars = binance.fetch_ohlcv(symbol, timeframe, new_start, min_ts - 1)
    store.store_bars(exchange, symbol, timeframe, bars)
    merged = store.load_bars(exchange, symbol, timeframe, new_start, max_ts)
    return [list(row) for row in merged]


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
    return 60_000
