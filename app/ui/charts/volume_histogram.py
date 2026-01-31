from bisect import bisect_left, bisect_right
from typing import Any, Callable, Iterable, List, Optional, Tuple, Union

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QPainter, QPicture

from .performance import calculate_lod_step, MAX_VISIBLE_BARS_DENSE


class VolumeHistogramItem(pg.GraphicsObject):
    def __init__(
        self,
        up_color: QColor,
        down_color: QColor,
        base_color: QColor,
        bar_width: float,
        volume_height_ratio: float,
        chunk_size: int = 300,
    ) -> None:
        super().__init__()
        self._up_color = QColor(up_color)
        self._down_color = QColor(down_color)
        self._base_color = QColor(base_color)
        self._bar_width = float(bar_width)
        self._volume_height_ratio = float(volume_height_ratio)
        self._chunk_size = int(chunk_size)
        self._x: Optional[np.ndarray] = None
        self._vol: Optional[np.ndarray] = None
        self._is_up: Optional[np.ndarray] = None
        self._ts_cache: List[float] = []
        self._chunk_cache: dict[int, QPicture] = {}
        self._render_key: Optional[Tuple[int, float, float, float]] = None
        self._view_hint: Optional[Tuple[int, int, int]] = None
        self._view_hint_key: Optional[Tuple[float, float, int]] = None
        self._cached_bounds: Optional[QRectF] = None
        self._data_len = 0
        self._last_ts: Optional[float] = None
        self.volume_max: float = 0.0
        self._tail_index: Optional[int] = None
        self._tail_x: Optional[float] = None
        self._tail_vol: Optional[float] = None
        self._tail_is_up: Optional[bool] = None
        self._tail_enabled = False

    def set_data(
        self,
        data: List[Iterable],
        extract_volume: Callable[[Any, int], float],
        extract_x: Callable[[Any, int], float],
        extract_is_up: Optional[Callable[[Any, int], Optional[bool]]] = None,
    ) -> None:
        if not data:
            self._x = None
            self._vol = None
            self._is_up = None
            self._ts_cache = []
            self._chunk_cache = {}
            self._cached_bounds = QRectF(0, 0, 1, 1)
            self.volume_max = 0.0
            self.update()
            return
        try:
            last_ts = float(extract_x(data[-1], len(data) - 1))
        except Exception:
            last_ts = None
        if self._data_len == len(data) and self._last_ts == last_ts:
            return
        self._data_len = len(data)
        self._last_ts = last_ts
        x_vals = []
        vol_vals = []
        up_vals = []
        for idx, item in enumerate(data):
            try:
                x_vals.append(float(extract_x(item, idx)))
                vol_vals.append(float(extract_volume(item, idx)))
            except Exception:
                x_vals.append(float(idx))
                vol_vals.append(0.0)
            if extract_is_up is not None:
                try:
                    is_up = extract_is_up(item, idx)
                except Exception:
                    is_up = None
                up_vals.append(bool(is_up) if is_up is not None else True)
        self._x = np.asarray(x_vals, dtype=np.float64)
        self._vol = np.asarray(vol_vals, dtype=np.float64)
        self._is_up = np.asarray(up_vals, dtype=bool) if up_vals else None
        self._ts_cache = list(x_vals)
        try:
            vmax = float(np.nanmax(self._vol)) if self._vol is not None and self._vol.size else 0.0
        except Exception:
            vmax = 0.0
        self.volume_max = vmax if vmax > 0 else 0.0
        if self._x is not None and self._x.size:
            x_min = float(np.nanmin(self._x))
            x_max = float(np.nanmax(self._x))
            self._cached_bounds = QRectF(x_min, 0, x_max - x_min, 1)
        else:
            self._cached_bounds = QRectF(0, 0, 1, 1)
        self._chunk_cache = {}
        self._render_key = None
        self.update()

    def set_arrays(
        self,
        x_vals: Union[List[float], np.ndarray],
        vol_vals: Union[List[float], np.ndarray],
        is_up: Optional[Union[List[bool], np.ndarray]] = None,
    ) -> None:
        if x_vals is None or vol_vals is None or len(x_vals) == 0:
            self._x = None
            self._vol = None
            self._is_up = None
            self._ts_cache = []
            self._chunk_cache = {}
            self._cached_bounds = QRectF(0, 0, 1, 1)
            self.volume_max = 0.0
            self.update()
            return
        self._x = np.asarray(x_vals, dtype=np.float64)
        self._vol = np.asarray(vol_vals, dtype=np.float64)
        if is_up is not None:
            self._is_up = np.asarray(is_up, dtype=bool)
        else:
            self._is_up = None
        self._ts_cache = self._x.tolist()
        try:
            vmax = float(np.nanmax(self._vol)) if self._vol is not None and self._vol.size else 0.0
        except Exception:
            vmax = 0.0
        self.volume_max = vmax if vmax > 0 else 0.0
        if self._x is not None and self._x.size:
            x_min = float(np.nanmin(self._x))
            x_max = float(np.nanmax(self._x))
            self._cached_bounds = QRectF(x_min, 0, x_max - x_min, 1)
        else:
            self._cached_bounds = QRectF(0, 0, 1, 1)
        self._chunk_cache = {}
        self._render_key = None
        self.update()

    def set_view_hint(self, x_min: float, x_max: float, start_idx: int, end_idx: int, step: int) -> None:
        self._view_hint = (start_idx, end_idx, step)
        self._view_hint_key = (float(x_min), float(x_max), int(self._data_len))

    def set_view_bounds(self, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        try:
            x_min = float(x_min)
            x_max = float(x_max)
            y_min = float(y_min)
            y_max = float(y_max)
        except Exception:
            return
        if not np.isfinite(x_min) or not np.isfinite(x_max) or not np.isfinite(y_min) or not np.isfinite(y_max):
            return
        if x_max <= x_min or y_max <= y_min:
            return
        rect = QRectF(x_min, y_min, x_max - x_min, y_max - y_min)
        if self._cached_bounds is None or self._cached_bounds != rect:
            self.prepareGeometryChange()
            self._cached_bounds = rect

    def set_tail(self, index: int, x_val: float, volume: float, is_up: bool) -> None:
        try:
            index = int(index)
            x_val = float(x_val)
            volume = float(volume)
        except Exception:
            return
        if self._tail_index != index:
            self._chunk_cache = {}
            self._render_key = None
        self._tail_index = index
        self._tail_x = x_val
        self._tail_vol = volume
        self._tail_is_up = bool(is_up)
        self._tail_enabled = True
        self.update()

    def clear_tail(self) -> None:
        if self._tail_enabled:
            self._tail_enabled = False
            self._tail_index = None
            self._tail_x = None
            self._tail_vol = None
            self._tail_is_up = None
            self.update()

    def boundingRect(self) -> QRectF:
        if self._cached_bounds is not None and self._cached_bounds.isValid():
            return self._cached_bounds
        return QRectF(0, 0, 1, 1)

    def paint(self, painter: QPainter, option, widget) -> None:
        if self._x is None or self._vol is None or self._x.size == 0:
            return
        try:
            view_box = self.getViewBox()
            if view_box:
                (x_range, y_range) = view_box.viewRange()
                x_min, x_max = x_range
                y_min, y_max = y_range
            else:
                x_min, x_max = self._x[0], self._x[-1]
                y_min, y_max = 0.0, 1.0
        except Exception:
            x_min, x_max = self._x[0], self._x[-1]
            y_min, y_max = 0.0, 1.0
        if x_max <= x_min:
            return
        if self._view_hint_key == (float(x_min), float(x_max), int(self._data_len)) and self._view_hint is not None:
            start_idx, end_idx, step = self._view_hint
        else:
            start_idx = max(0, bisect_left(self._ts_cache, x_min) - 10)
            end_idx = min(self._x.size, bisect_right(self._ts_cache, x_max) + 10)
            visible_count = max(0, end_idx - start_idx)
            if visible_count <= 0:
                return
            step = calculate_lod_step(visible_count, MAX_VISIBLE_BARS_DENSE)
        visible_slice = self._vol[start_idx:end_idx:step]
        if visible_slice.size == 0:
            return
        try:
            volume_max = float(np.nanmax(visible_slice))
        except Exception:
            volume_max = 0.0
        if volume_max <= 0:
            volume_max = 1.0
        visible_range = max(1e-9, float(y_max - y_min))
        volume_max_height = visible_range * self._volume_height_ratio
        volume_bottom = y_min
        render_key = (step, float(volume_bottom), float(volume_max), float(self._bar_width))
        if render_key != self._render_key:
            self._chunk_cache = {}
            self._render_key = render_key
        min_height = max(visible_range * 0.001, 1e-6)
        chunk_start = start_idx // self._chunk_size
        chunk_end = (end_idx - 1) // self._chunk_size if end_idx > 0 else chunk_start
        for chunk_idx in range(chunk_start, chunk_end + 1):
            picture = self._chunk_cache.get(chunk_idx)
            if picture is None:
                picture = QPicture()
                qp = QPainter(picture)
                try:
                    c_start = chunk_idx * self._chunk_size
                    c_end = min(self._x.size, c_start + self._chunk_size)
                    for idx in range(c_start, c_end):
                        if idx < start_idx or idx >= end_idx:
                            continue
                        if step > 1 and (idx % step) != 0:
                            continue
                        if self._tail_enabled and self._tail_index is not None and idx == self._tail_index:
                            continue
                        vol = float(self._vol[idx])
                        if not np.isfinite(vol) or vol <= 0:
                            continue
                        height = (vol / volume_max) * volume_max_height
                        if height < min_height:
                            height = min_height
                        color = self._base_color
                        if self._is_up is not None and idx < self._is_up.size:
                            color = self._up_color if bool(self._is_up[idx]) else self._down_color
                        qp.setPen(pg.mkPen(QColor(0, 0, 0, 0)))
                        qp.setBrush(color)
                        x_val = float(self._x[idx])
                        qp.drawRect(QRectF(x_val - self._bar_width / 2.0, volume_bottom, self._bar_width, height))
                finally:
                    qp.end()
                self._chunk_cache[chunk_idx] = picture
            painter.drawPicture(0, 0, picture)
        if self._tail_enabled and self._tail_x is not None and self._tail_vol is not None:
            if x_min <= self._tail_x <= x_max:
                vol = self._tail_vol
                if np.isfinite(vol) and vol > 0:
                    height = (vol / volume_max) * volume_max_height
                    if height < min_height:
                        height = min_height
                    color = self._base_color
                    if self._tail_is_up is not None:
                        color = self._up_color if self._tail_is_up else self._down_color
                    painter.setPen(pg.mkPen(QColor(0, 0, 0, 0)))
                    painter.setBrush(color)
                    painter.drawRect(QRectF(self._tail_x - self._bar_width / 2.0, volume_bottom, self._bar_width, height))


def update_volume_histogram(
    plot_widget: pg.PlotWidget,
    volume_item: Optional[VolumeHistogramItem],
    base_color: QColor,
    data: List[Iterable],
    extract_volume: Callable[[Any, int], float],
    extract_x: Callable[[Any, int], float],
    extract_is_up: Optional[Callable[[Any, int], Optional[bool]]] = None,
    up_color: Optional[QColor] = None,
    down_color: Optional[QColor] = None,
    volume_height_ratio: float = 0.15,
    bar_width: float = 0.8,
    flush_bottom: bool = True,
) -> Tuple[Optional[VolumeHistogramItem], float]:
    if not data:
        if volume_item is not None:
            try:
                plot_widget.removeItem(volume_item)
            except Exception:
                pass
        return None, 0.0
    up_color = QColor(up_color) if up_color is not None else QColor('#22C55E')
    down_color = QColor(down_color) if down_color is not None else QColor('#EF5350')
    base_color = QColor(base_color)
    if volume_item is None or not isinstance(volume_item, VolumeHistogramItem):
        volume_item = VolumeHistogramItem(
            up_color=up_color,
            down_color=down_color,
            base_color=base_color,
            bar_width=bar_width,
            volume_height_ratio=volume_height_ratio,
        )
        volume_item.setZValue(10)
        plot_widget.addItem(volume_item)
    volume_item.set_data(data, extract_volume, extract_x, extract_is_up)
    return volume_item, volume_item.volume_max


def update_volume_histogram_arrays(
    plot_widget: pg.PlotWidget,
    volume_item: Optional[VolumeHistogramItem],
    base_color: QColor,
    x_vals: Union[List[float], np.ndarray],
    vol_vals: Union[List[float], np.ndarray],
    is_up: Optional[Union[List[bool], np.ndarray]] = None,
    up_color: Optional[QColor] = None,
    down_color: Optional[QColor] = None,
    volume_height_ratio: float = 0.15,
    bar_width: float = 0.8,
) -> Tuple[Optional[VolumeHistogramItem], float]:
    if x_vals is None or vol_vals is None or len(x_vals) == 0:
        if volume_item is not None:
            try:
                plot_widget.removeItem(volume_item)
            except Exception:
                pass
        return None, 0.0
    up_color = QColor(up_color) if up_color is not None else QColor('#22C55E')
    down_color = QColor(down_color) if down_color is not None else QColor('#EF5350')
    base_color = QColor(base_color)
    if volume_item is None or not isinstance(volume_item, VolumeHistogramItem):
        volume_item = VolumeHistogramItem(
            up_color=up_color,
            down_color=down_color,
            base_color=base_color,
            bar_width=bar_width,
            volume_height_ratio=volume_height_ratio,
        )
        volume_item.setZValue(10)
        plot_widget.addItem(volume_item)
    volume_item.set_arrays(x_vals, vol_vals, is_up)
    return volume_item, volume_item.volume_max
