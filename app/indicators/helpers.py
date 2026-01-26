from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np


@dataclass
class SeriesBundle:
    time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray


def bars_to_numpy(bars: List[Iterable[float]]) -> np.ndarray:
    if not bars:
        return np.empty((0, 6), dtype=np.float64)
    arr = np.asarray(bars, dtype=np.float64)
    if arr.ndim == 1:
        arr = np.expand_dims(arr, 0)
    if arr.shape[1] < 6:
        pad = np.zeros((arr.shape[0], 6 - arr.shape[1]), dtype=np.float64)
        arr = np.hstack((arr, pad))
    return arr[:, :6]


def series_bundle(bars_np: np.ndarray) -> SeriesBundle:
    if bars_np.size == 0:
        empty = np.empty(0, dtype=np.float64)
        return SeriesBundle(empty, empty, empty, empty, empty, empty)
    return SeriesBundle(
        time=bars_np[:, 0],
        open=bars_np[:, 1],
        high=bars_np[:, 2],
        low=bars_np[:, 3],
        close=bars_np[:, 4],
        volume=bars_np[:, 5],
    )


def _pad_nan(values: np.ndarray, length: int) -> np.ndarray:
    if values.size >= length:
        return values
    pad = np.full(length - values.size, np.nan, dtype=np.float64)
    return np.concatenate((pad, values))


def align(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == length:
        return arr
    if arr.size > length:
        return arr[-length:]
    return _pad_nan(arr, length)


def shift(values: Iterable[float], n: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    out = np.full_like(arr, np.nan, dtype=np.float64)
    if n == 0:
        return arr.copy()
    if n > 0:
        out[n:] = arr[:-n]
    else:
        out[:n] = arr[-n:]
    return out


def nz(values: Iterable[float], default: float = 0.0) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    out = arr.copy()
    out[np.isnan(out)] = default
    return out


def change(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return arr
    out = np.full_like(arr, np.nan, dtype=np.float64)
    out[1:] = np.diff(arr)
    return out


def sma(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0 or n == 0:
        return out
    csum = np.cumsum(np.nan_to_num(arr), dtype=np.float64)
    csum[length:] = csum[length:] - csum[:-length]
    out[length - 1:] = csum[length - 1:] / float(length)
    return out


def ema(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0 or n == 0:
        return out
    alpha = 2.0 / (length + 1.0)
    ema_val = np.nan
    for i in range(n):
        v = arr[i]
        if np.isnan(v):
            out[i] = ema_val
            continue
        if np.isnan(ema_val):
            ema_val = v
        else:
            ema_val = alpha * v + (1 - alpha) * ema_val
        out[i] = ema_val
    return out


def rma(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0 or n == 0:
        return out
    alpha = 1.0 / float(length)
    rma_val = np.nan
    for i in range(n):
        v = arr[i]
        if np.isnan(v):
            out[i] = rma_val
            continue
        if np.isnan(rma_val):
            rma_val = v
        else:
            rma_val = alpha * v + (1 - alpha) * rma_val
        out[i] = rma_val
    return out


def wma(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0 or n == 0:
        return out
    weights = np.arange(1, length + 1, dtype=np.float64)
    wsum = weights.sum()
    for i in range(length - 1, n):
        window = arr[i + 1 - length: i + 1]
        if np.any(np.isnan(window)):
            continue
        out[i] = np.dot(window, weights) / wsum
    return out


def vwma(values: Iterable[float], length: int, volume: Optional[Iterable[float]] = None) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    vol = np.asarray(volume, dtype=np.float64) if volume is not None else None
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0 or n == 0 or vol is None:
        return out
    for i in range(length - 1, n):
        p = arr[i + 1 - length: i + 1]
        v = vol[i + 1 - length: i + 1]
        if np.any(np.isnan(p)) or np.any(np.isnan(v)):
            continue
        denom = np.sum(v)
        if denom == 0:
            continue
        out[i] = np.sum(p * v) / denom
    return out


def hma(values: Iterable[float], length: int) -> np.ndarray:
    if length <= 0:
        return np.full(len(list(values)), np.nan, dtype=np.float64)
    half = max(1, int(length / 2))
    sqrt_len = max(1, int(math.sqrt(length)))
    wma_full = wma(values, length)
    wma_half = wma(values, half)
    diff = (2 * wma_half) - wma_full
    return wma(diff, sqrt_len)


def rsi(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0 or n == 0:
        return out
    diffs = np.diff(arr)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = rma(gains, length)
    avg_loss = rma(losses, length)
    rs = np.divide(avg_gain, avg_loss, out=np.full_like(avg_gain, np.nan), where=avg_loss != 0)
    rsi_vals = 100 - (100 / (1 + rs))
    out[1:] = rsi_vals
    return out


def stoch(high: Iterable[float], low: Iterable[float], close: Iterable[float], k_len: int, d_len: int) -> Tuple[np.ndarray, np.ndarray]:
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    k = np.full(n, np.nan, dtype=np.float64)
    if k_len <= 0 or n == 0:
        return k, k.copy()
    for i in range(k_len - 1, n):
        hh = np.nanmax(h[i + 1 - k_len: i + 1])
        ll = np.nanmin(l[i + 1 - k_len: i + 1])
        denom = hh - ll
        if denom == 0:
            continue
        k[i] = (c[i] - ll) / denom * 100.0
    d = sma(k, d_len)
    return k, d


def macd(values: Iterable[float], fast: int, slow: int, signal: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def cci(high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int) -> np.ndarray:
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    tp = (h + l + c) / 3.0
    ma = sma(tp, length)
    n = tp.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0:
        return out
    for i in range(length - 1, n):
        window = tp[i + 1 - length: i + 1]
        if np.any(np.isnan(window)) or np.isnan(ma[i]):
            continue
        dev = np.mean(np.abs(window - ma[i]))
        if dev == 0:
            continue
        out[i] = (tp[i] - ma[i]) / (0.015 * dev)
    return out


def momentum(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    out = np.full(arr.size, np.nan, dtype=np.float64)
    if length <= 0:
        return out
    out[length:] = arr[length:] - arr[:-length]
    return out


def roc(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    out = np.full(arr.size, np.nan, dtype=np.float64)
    if length <= 0:
        return out
    prev = arr[:-length]
    curr = arr[length:]
    out[length:] = np.where(prev != 0, (curr - prev) / prev * 100.0, np.nan)
    return out


def atr(high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int) -> np.ndarray:
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    tr = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return tr
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return rma(tr, length)


def stdev(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0:
        return out
    for i in range(length - 1, n):
        window = arr[i + 1 - length: i + 1]
        if np.any(np.isnan(window)):
            continue
        out[i] = np.std(window)
    return out


def bb(values: Iterable[float], length: int, mult: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    basis = sma(values, length)
    dev = stdev(values, length) * mult
    upper = basis + dev
    lower = basis - dev
    return upper, basis, lower


def keltner(high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int, mult: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    basis = ema(close, length)
    range_atr = atr(high, low, close, length)
    upper = basis + (mult * range_atr)
    lower = basis - (mult * range_atr)
    return upper, basis, lower


def dmi(high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int) -> Tuple[np.ndarray, np.ndarray]:
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    plus_dm = np.zeros(n, dtype=np.float64)
    minus_dm = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        up = h[i] - h[i - 1]
        down = l[i - 1] - l[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    tr = atr(h, l, c, 1)
    atr_vals = rma(tr, length)
    plus_di = 100 * rma(plus_dm, length) / atr_vals
    minus_di = 100 * rma(minus_dm, length) / atr_vals
    return plus_di, minus_di


def adx(high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int) -> np.ndarray:
    plus_di, minus_di = dmi(high, low, close, length)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    return rma(dx, length)


def supertrend(high: Iterable[float], low: Iterable[float], close: Iterable[float], length: int, mult: float) -> np.ndarray:
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    out = np.full(n, np.nan, dtype=np.float64)
    atr_vals = atr(h, l, c, length)
    hl2 = (h + l) / 2.0
    upper = hl2 + mult * atr_vals
    lower = hl2 - mult * atr_vals
    direction = 1
    for i in range(n):
        if np.isnan(atr_vals[i]):
            continue
        if i == 0:
            out[i] = upper[i]
            continue
        if c[i] > upper[i - 1]:
            direction = 1
        elif c[i] < lower[i - 1]:
            direction = -1
        if direction > 0:
            out[i] = max(lower[i], out[i - 1] if not np.isnan(out[i - 1]) else lower[i])
        else:
            out[i] = min(upper[i], out[i - 1] if not np.isnan(out[i - 1]) else upper[i])
    return out


def psar(high: Iterable[float], low: Iterable[float], accel: float, max_accel: float) -> np.ndarray:
    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    n = h.size
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return out
    uptrend = True
    ep = h[0]
    sar = l[0]
    af = accel
    out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if uptrend:
            sar = min(sar, l[i - 1], l[i])
            if h[i] > ep:
                ep = h[i]
                af = min(af + accel, max_accel)
            if l[i] < sar:
                uptrend = False
                sar = ep
                ep = l[i]
                af = accel
        else:
            sar = max(sar, h[i - 1], h[i])
            if l[i] < ep:
                ep = l[i]
                af = min(af + accel, max_accel)
            if h[i] > sar:
                uptrend = True
                sar = ep
                ep = h[i]
                af = accel
        out[i] = sar
    return out


def cross(a: Iterable[float], b: Iterable[float]) -> np.ndarray:
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    out = np.zeros_like(a_arr, dtype=bool)
    if a_arr.size < 2:
        return out
    out[1:] = ((a_arr[1:] > b_arr[1:]) & (a_arr[:-1] <= b_arr[:-1])) | ((a_arr[1:] < b_arr[1:]) & (a_arr[:-1] >= b_arr[:-1]))
    return out


def crossover(a: Iterable[float], b: Iterable[float]) -> np.ndarray:
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    out = np.zeros_like(a_arr, dtype=bool)
    if a_arr.size < 2:
        return out
    out[1:] = (a_arr[1:] > b_arr[1:]) & (a_arr[:-1] <= b_arr[:-1])
    return out


def crossunder(a: Iterable[float], b: Iterable[float]) -> np.ndarray:
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    out = np.zeros_like(a_arr, dtype=bool)
    if a_arr.size < 2:
        return out
    out[1:] = (a_arr[1:] < b_arr[1:]) & (a_arr[:-1] >= b_arr[:-1])
    return out


def highest(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0:
        return out
    for i in range(length - 1, n):
        out[i] = np.nanmax(arr[i + 1 - length: i + 1])
    return out


def lowest(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0:
        return out
    for i in range(length - 1, n):
        out[i] = np.nanmin(arr[i + 1 - length: i + 1])
    return out


def percentile(values: Iterable[float], length: int, p: float) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 0:
        return out
    for i in range(length - 1, n):
        out[i] = np.nanpercentile(arr[i + 1 - length: i + 1], p)
    return out


def slope(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 1:
        return out
    x = np.arange(length, dtype=np.float64)
    x_mean = np.mean(x)
    denom = np.sum((x - x_mean) ** 2)
    if denom == 0:
        return out
    for i in range(length - 1, n):
        y = arr[i + 1 - length: i + 1]
        if np.any(np.isnan(y)):
            continue
        y_mean = np.mean(y)
        out[i] = np.sum((x - x_mean) * (y - y_mean)) / denom
    return out


def linreg(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    n = arr.size
    out = np.full(n, np.nan, dtype=np.float64)
    if length <= 1:
        return out
    x = np.arange(length, dtype=np.float64)
    x_mean = np.mean(x)
    denom = np.sum((x - x_mean) ** 2)
    if denom == 0:
        return out
    for i in range(length - 1, n):
        y = arr[i + 1 - length: i + 1]
        if np.any(np.isnan(y)):
            continue
        y_mean = np.mean(y)
        slope_val = np.sum((x - x_mean) * (y - y_mean)) / denom
        intercept = y_mean - slope_val * x_mean
        out[i] = intercept + slope_val * (length - 1)
    return out


def max_arr(a: Iterable[float], b: Iterable[float]) -> np.ndarray:
    return np.maximum(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64))


def min_arr(a: Iterable[float], b: Iterable[float]) -> np.ndarray:
    return np.minimum(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64))


def mean(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    return np.nanmean(arr)


def sum_arr(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    return np.nansum(arr)
