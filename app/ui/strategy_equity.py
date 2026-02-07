from __future__ import annotations

from typing import List

import pyqtgraph as pg


class StrategyEquityWidget(pg.PlotWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setBackground("#131722")
        self.showGrid(x=True, y=True, alpha=0.2)
        self.setMinimumHeight(160)
        self._curve = self.plot([], [], pen=pg.mkPen('#4ADE80', width=2))
        self._last_range: tuple[int, int] | None = None
        self._last_ts: List[int] = []
        self._last_equity: List[float] = []

        # Match chart theme ticks.
        try:
            for ax_name in ("bottom", "left"):
                ax = self.getAxis(ax_name)
                ax.setPen(pg.mkPen("#2A2E39"))
                ax.setTextPen(pg.mkPen("#B2B5BE"))
        except Exception:
            pass

    def set_equity(self, ts: List[int], equity: List[float]) -> None:
        self._last_ts = list(ts)
        self._last_equity = list(equity)
        self._curve.setData(ts, equity)

    def set_visible_range(self, ts_min: int, ts_max: int) -> None:
        if self._last_range == (ts_min, ts_max):
            return
        self._last_range = (ts_min, ts_max)
        try:
            self.blockSignals(True)
            self.setXRange(ts_min, ts_max, padding=0)
        except Exception:
            pass
        finally:
            try:
                self.blockSignals(False)
            except Exception:
                pass
