from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class RunConfig:
    symbol: str
    timeframe: str
    start_ts: int
    end_ts: int
    warmup_bars: int
    initial_cash: float
    leverage: float
    commission_bps: float
    slippage_bps: float
    close_on_finish: bool = True


@dataclass
class Order:
    submitted_ts: int
    side: str
    size: float
    status: str = "SUBMITTED"
    fill_ts: Optional[int] = None
    fill_price: Optional[float] = None
    fee: Optional[float] = None
    reason: Optional[str] = None


@dataclass
class Position:
    size: float = 0.0
    entry_price: Optional[float] = None
    entry_ts: Optional[int] = None
    # Entry commission accrued when opening the position. Exit commission is computed on close.
    entry_fee_total: float = 0.0


@dataclass
class Trade:
    side: str
    size: float
    entry_ts: int
    entry_price: float
    exit_ts: int
    exit_price: float
    pnl: float
    fee_total: float
    bars_held: int


@dataclass
class Portfolio:
    cash: float
    equity: float
    drawdown: float = 0.0
    max_drawdown: float = 0.0
    peak_equity: float = 0.0

    def update_drawdown(self) -> None:
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        if self.peak_equity > 0:
            self.drawdown = (self.peak_equity - self.equity) / self.peak_equity
        else:
            self.drawdown = 0.0
        if self.drawdown > self.max_drawdown:
            self.max_drawdown = self.drawdown


@dataclass
class BacktestResult:
    equity_ts: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    drawdown: list[float] = field(default_factory=list)
    position_size: list[float] = field(default_factory=list)
    price: list[float] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    logs: list[Dict[str, Any]] = field(default_factory=list)
