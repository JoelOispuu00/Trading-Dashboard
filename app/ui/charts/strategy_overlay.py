from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Any, Dict, List, Optional

import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPicture


class StrategyOverlayRenderer(pg.GraphicsObject):
    """
    Lightweight marker overlay for backtest entries/exits.

    Important constraints:
    - Paint must be "dumb draw" (no viewbox queries, no range changes) to avoid feedback loops.
    - We keep markers sorted by ts and use the exposed rect (in item coords) to draw only visible chunks.
    """

    def __init__(self, markers: List[Dict[str, Any]]) -> None:
        super().__init__()
        self._markers: List[Dict[str, Any]] = []
        self._marker_ts: List[float] = []
        self._chunk_size = 400
        self._chunk_cache: Dict[int, QPicture] = {}
        self._ts_cache: Optional[List[float]] = None
        self._bounds = QRectF()
        self.set_markers(markers)

    def set_ts_cache(self, ts_cache: List[float]) -> None:
        self._ts_cache = ts_cache
        self._chunk_cache.clear()
        self.prepareGeometryChange()
        self._bounds = self._compute_bounds()
        self.update()

    def set_markers(self, markers: List[Dict[str, Any]]) -> None:
        markers = markers or []
        self._markers = sorted(markers, key=lambda m: float(m.get("ts", 0.0)))
        self._marker_ts = [float(m.get("ts", 0.0)) for m in self._markers]
        self._chunk_cache.clear()
        self.prepareGeometryChange()
        self._bounds = self._compute_bounds()
        self.update()

    def paint(self, painter: QPainter, option, widget) -> None:
        if not self._markers:
            return

        try:
            exposed: QRectF = option.exposedRect  # type: ignore[attr-defined]
            x_min = float(exposed.left())
            x_max = float(exposed.right())
        except Exception:
            x_min = float(self._marker_ts[0])
            x_max = float(self._marker_ts[-1])

        i0 = bisect_left(self._marker_ts, x_min)
        i1 = bisect_right(self._marker_ts, x_max)
        if i1 <= i0:
            return

        start_chunk = i0 // self._chunk_size
        end_chunk = (i1 - 1) // self._chunk_size
        for chunk_idx in range(start_chunk, end_chunk + 1):
            picture = self._chunk_cache.get(chunk_idx)
            if picture is None:
                picture = self._render_chunk(chunk_idx)
                self._chunk_cache[chunk_idx] = picture
            painter.drawPicture(0, 0, picture)

    def boundingRect(self):
        return self._bounds if not self._bounds.isNull() else super().boundingRect()

    def _compute_bounds(self) -> QRectF:
        if not self._markers:
            return QRectF()
        try:
            min_ts = float(self._marker_ts[0])
            max_ts = float(self._marker_ts[-1])
            price_vals = [float(m.get("price", 0.0)) for m in self._markers]
            min_p = min(price_vals)
            max_p = max(price_vals)
            if max_ts == min_ts:
                max_ts += 1.0
            if max_p == min_p:
                max_p += 1.0
            return QRectF(min_ts, min_p, max_ts - min_ts, max_p - min_p)
        except Exception:
            return QRectF()

    def _render_chunk(self, chunk_idx: int) -> QPicture:
        picture = QPicture()
        painter = QPainter(picture)
        try:
            start = chunk_idx * self._chunk_size
            end = min(len(self._markers), start + self._chunk_size)
            for marker in self._markers[start:end]:
                ts = float(marker.get("ts", 0.0))
                price = float(marker.get("price", 0.0))
                kind = str(marker.get("kind", "entry"))
                side = str(marker.get("side", "LONG")).upper()
                color = QColor("#22C55E") if side == "LONG" else QColor("#EF5350")
                painter.setPen(pg.mkPen(color))
                painter.setBrush(pg.mkBrush(color))

                size = 6.0
                if kind == "entry":
                    points = [
                        QPointF(ts, price + size),
                        QPointF(ts - size, price - size),
                        QPointF(ts + size, price - size),
                    ]
                else:
                    points = [
                        QPointF(ts, price - size),
                        QPointF(ts - size, price + size),
                        QPointF(ts + size, price + size),
                    ]
                painter.drawPolygon(*points)
        finally:
            painter.end()
        return picture

