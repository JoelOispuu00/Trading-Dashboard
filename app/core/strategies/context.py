from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .models import Position, Portfolio
from indicators import helpers as indicator_helpers


@dataclass
class LogMessage:
    ts: int
    level: str
    message: str
    bar_ts: Optional[int] = None


class StrategyLogger:
    def __init__(self, collector: List[Dict[str, Any]], emit: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self._collector = collector
        self._emit = emit

    def _log(self, level: str, message: str, ts: int, bar_ts: Optional[int]) -> None:
        payload = {"level": level, "message": message, "ts": ts, "bar_ts": bar_ts}
        self._collector.append(payload)
        if self._emit:
            self._emit(payload)

    def info(self, message: str, ts: int, bar_ts: Optional[int] = None) -> None:
        self._log("INFO", message, ts, bar_ts)

    def warn(self, message: str, ts: int, bar_ts: Optional[int] = None) -> None:
        self._log("WARN", message, ts, bar_ts)

    def error(self, message: str, ts: int, bar_ts: Optional[int] = None) -> None:
        self._log("ERROR", message, ts, bar_ts)


class IndicatorProxy:
    def __init__(self, cache: Dict[str, np.ndarray]) -> None:
        self._cache = cache

    def __getattr__(self, name: str):
        fn = getattr(indicator_helpers, name, None)
        if fn is None or not callable(fn):
            raise AttributeError(name)

        def wrapper(*args, **kwargs):
            key = (name, _sig_args(args), _sig_args(kwargs))
            key_s = str(key)
            if key_s in self._cache:
                return self._cache[key_s]
            result = fn(*args, **kwargs)
            if isinstance(result, np.ndarray):
                self._cache[key_s] = result
            else:
                try:
                    self._cache[key_s] = np.asarray(result, dtype=np.float64)
                except Exception:
                    self._cache[key_s] = result
            return self._cache[key_s]

        return wrapper


def _sig_args(obj: Any) -> str:
    try:
        if isinstance(obj, np.ndarray):
            return f"nd:{obj.shape}:{obj.dtype}"
        if isinstance(obj, (list, tuple)):
            return f"seq:{tuple(_sig_args(x) for x in obj)}"
        if isinstance(obj, dict):
            return f"dict:{tuple((k, _sig_args(v)) for k, v in sorted(obj.items()))}"
    except Exception:
        return "unknown"
    return repr(obj)


class SizeHelper:
    def __init__(self, ctx: "StrategyContext") -> None:
        self._ctx = ctx

    def fixed(self, units: float) -> float:
        return float(units)

    def percent_equity(self, pct: float) -> float:
        if self._ctx._current_close is None:
            return 0.0
        equity = float(self._ctx.portfolio.equity)
        leverage = float(self._ctx._leverage)
        price = float(self._ctx._current_close)
        return (equity * pct * leverage) / price if price > 0 else 0.0


class StrategyContext:
    def __init__(
        self,
        bars: np.ndarray,
        params: Dict[str, Any],
        initial_cash: float,
        leverage: float,
        log_emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.bars = bars
        self.time = bars[:, 0]
        self.open = bars[:, 1]
        self.high = bars[:, 2]
        self.low = bars[:, 3]
        self.close = bars[:, 4]
        self.volume = bars[:, 5]
        self.params = params
        self.state: Dict[str, Any] = {}
        self.position = Position()
        self.portfolio = Portfolio(cash=initial_cash, equity=initial_cash, peak_equity=initial_cash)
        self._leverage = leverage
        self._pending_orders: List[Dict[str, Any]] = []
        self._indicator_cache: Dict[str, np.ndarray] = {}
        self.ind = IndicatorProxy(self._indicator_cache)
        self._logs: List[Dict[str, Any]] = []
        self.logger = StrategyLogger(self._logs, emit=log_emit)
        self.size = SizeHelper(self)
        self.trading_enabled = True
        # Warmup/no-trading warn-once per method (buy/sell/flatten) for this run.
        self._disabled_warned: set[str] = set()
        self._current_index: Optional[int] = None
        self._current_close: Optional[float] = None

    def set_bar_index(self, i: int) -> None:
        self._current_index = i
        try:
            self._current_close = float(self.close[i])
        except Exception:
            self._current_close = None

    def buy(self, size: float) -> None:
        self._enqueue_order("BUY", size)

    def sell(self, size: float) -> None:
        self._enqueue_order("SELL", size)

    def flatten(self) -> None:
        # Flatten on flat position is a clean no-op in V2, but warmup/no-trading still warns once.
        if not self.trading_enabled:
            self._enqueue_order("FLATTEN", 0.0)
            return
        if self.position.size == 0:
            return
        self._enqueue_order("FLATTEN", 0.0)

    def cancel(self, _order_id: str) -> None:
        if self._current_index is None:
            return
        ts = int(self.time[self._current_index])
        self.logger.warn("cancel not supported in V2", ts, ts)

    def _enqueue_order(self, side: str, size: float) -> None:
        if self._current_index is None:
            return
        ts = int(self.time[self._current_index])
        if not self.trading_enabled:
            key = side.upper()
            if key not in self._disabled_warned:
                # Warn once per run per method, otherwise strategies can spam logs during warmup.
                self._disabled_warned.add(key)
                msg = "trading disabled, flatten ignored" if key == "FLATTEN" else f"trading disabled, {key.lower()} ignored"
                self.logger.warn(msg, ts, ts)
            return
        self._pending_orders.append({"side": side, "size": float(size), "submitted_ts": ts})

    def pop_orders(self) -> List[Dict[str, Any]]:
        orders = self._pending_orders
        self._pending_orders = []
        return orders

    def get_logs(self) -> List[Dict[str, Any]]:
        return list(self._logs)

    def reset_indicator_cache(self) -> None:
        self._indicator_cache = {}
        self.ind = IndicatorProxy(self._indicator_cache)
