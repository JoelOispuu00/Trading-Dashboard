from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from . import helpers


def normalize_bars(bars: Iterable) -> List[List[float]]:
    normalized: List[List[float]] = []
    for bar in bars:
        if isinstance(bar, dict):
            try:
                ts = float(bar.get("time", bar.get("ts_ms", 0)))
                o = float(bar.get("open", 0))
                h = float(bar.get("high", 0))
                l = float(bar.get("low", 0))
                c = float(bar.get("close", 0))
                v = float(bar.get("volume", 0))
            except Exception:
                continue
            normalized.append([ts, o, h, l, c, v])
        else:
            try:
                row = list(bar)
            except Exception:
                continue
            if len(row) < 5:
                continue
            if len(row) < 6:
                row = row + [0.0]
            normalized.append([float(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
    return normalized


class IndicatorContext:
    def __init__(self, bars_np: np.ndarray) -> None:
        self._bars_np = bars_np
        self._bundle = helpers.series_bundle(bars_np)
        self._required_lookback = 0

    @property
    def required_lookback(self) -> int:
        return self._required_lookback

    def lookback(self, n: int) -> None:
        try:
            n = int(n)
        except Exception:
            return
        if n > self._required_lookback:
            self._required_lookback = n

    def series(self, bars: Iterable, field: str) -> np.ndarray:
        if field == "open":
            return self._bundle.open.copy()
        if field == "high":
            return self._bundle.high.copy()
        if field == "low":
            return self._bundle.low.copy()
        if field == "close":
            return self._bundle.close.copy()
        if field == "volume":
            return self._bundle.volume.copy()
        return self._bundle.close.copy()

    def time(self, bars: Iterable) -> np.ndarray:
        return self._bundle.time.astype(np.int64, copy=True)

    def ohlc(self, bars: Iterable) -> Dict[str, np.ndarray]:
        return {
            "time": self._bundle.time.copy(),
            "open": self._bundle.open.copy(),
            "high": self._bundle.high.copy(),
            "low": self._bundle.low.copy(),
            "close": self._bundle.close.copy(),
            "volume": self._bundle.volume.copy(),
        }

    def hl2(self, bars: Iterable) -> np.ndarray:
        return (self._bundle.high + self._bundle.low) / 2.0

    def hlc3(self, bars: Iterable) -> np.ndarray:
        return (self._bundle.high + self._bundle.low + self._bundle.close) / 3.0

    def ohlc4(self, bars: Iterable) -> np.ndarray:
        return (self._bundle.open + self._bundle.high + self._bundle.low + self._bundle.close) / 4.0

    def change(self, values: Iterable[float]) -> np.ndarray:
        return helpers.change(values)

    def align(self, values: Iterable[float]) -> np.ndarray:
        return helpers.align(values, len(self._bundle.time))

    def shift(self, values: Iterable[float], n: int) -> np.ndarray:
        return helpers.shift(values, n)

    def nz(self, values: Iterable[float], default: float = 0.0) -> np.ndarray:
        return helpers.nz(values, default=default)

    def sma(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.sma(values, length)

    def ema(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.ema(values, length)

    def wma(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.wma(values, length)

    def rma(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.rma(values, length)

    def vwma(self, values: Iterable[float], length: int, volume_series: Optional[Iterable[float]] = None) -> np.ndarray:
        vol = volume_series if volume_series is not None else self._bundle.volume
        return helpers.vwma(values, length, vol)

    def hma(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.hma(values, length)

    def rsi(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.rsi(values, length)

    def stoch(self, high: Iterable[float], low: Iterable[float], close: Iterable[float], k_len: int, d_len: int) -> Tuple[np.ndarray, np.ndarray]:
        return helpers.stoch(high, low, close, k_len, d_len)

    def macd(self, values: Iterable[float], fast: int, slow: int, signal: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        return helpers.macd(values, fast, slow, signal)

    def cci(self, high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int) -> np.ndarray:
        return helpers.cci(high, low, close, length)

    def momentum(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.momentum(values, length)

    def roc(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.roc(values, length)

    def atr(self, high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int) -> np.ndarray:
        return helpers.atr(high, low, close, length)

    def stdev(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.stdev(values, length)

    def bb(self, values: Iterable[float], length: int, mult: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        return helpers.bb(values, length, mult)

    def keltner(self, high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int, mult: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        return helpers.keltner(high, low, close, length, mult)

    def dmi(self, high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int) -> Tuple[np.ndarray, np.ndarray]:
        return helpers.dmi(high, low, close, length)

    def adx(self, high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int) -> np.ndarray:
        return helpers.adx(high, low, close, length)

    def supertrend(self, high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int, mult: float) -> np.ndarray:
        return helpers.supertrend(high, low, close, length, mult)

    def psar(self, high: Iterable[float], low: Iterable[float], accel: float, max_accel: float) -> np.ndarray:
        return helpers.psar(high, low, accel, max_accel)

    def cross(self, a: Iterable[float], b: Iterable[float]) -> np.ndarray:
        return helpers.cross(a, b)

    def crossover(self, a: Iterable[float], b: Iterable[float]) -> np.ndarray:
        return helpers.crossover(a, b)

    def crossunder(self, a: Iterable[float], b: Iterable[float]) -> np.ndarray:
        return helpers.crossunder(a, b)

    def highest(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.highest(values, length)

    def lowest(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.lowest(values, length)

    def percentile(self, values: Iterable[float], length: int, p: float) -> np.ndarray:
        return helpers.percentile(values, length, p)

    def slope(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.slope(values, length)

    def linreg(self, values: Iterable[float], length: int) -> np.ndarray:
        return helpers.linreg(values, length)

    def max(self, a: Iterable[float], b: Iterable[float]) -> np.ndarray:
        return helpers.max_arr(a, b)

    def min(self, a: Iterable[float], b: Iterable[float]) -> np.ndarray:
        return helpers.min_arr(a, b)

    def abs(self, values: Iterable[float]) -> np.ndarray:
        return np.abs(np.asarray(values, dtype=np.float64))

    def mean(self, values: Iterable[float]) -> float:
        return float(helpers.mean(values))

    def sum(self, values: Iterable[float]) -> float:
        return float(helpers.sum_arr(values))

    def request(self, timeframe: str, source: str) -> np.ndarray:
        raise NotImplementedError("ctx.request (MTF) not wired yet")


def run_compute(
    bars: List[Iterable[float]],
    params: Dict[str, Any],
    compute_fn,
) -> Tuple[Dict[str, Any], int]:
    normalized = normalize_bars(bars)
    bars_np = helpers.bars_to_numpy(normalized)
    ctx = IndicatorContext(bars_np)
    result = compute_fn(normalized, params, ctx)
    return result, ctx.required_lookback
