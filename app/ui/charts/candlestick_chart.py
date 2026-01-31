from bisect import bisect_left, bisect_right
from datetime import datetime
import math
import time
from typing import Callable, Iterable, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer, QLineF, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QPicture, QColor, QPainterPath

from .volume_histogram import (
    update_volume_histogram,
    update_volume_histogram_arrays,
    VolumeHistogramItem,
    MAX_VISIBLE_BARS_DENSE,
    calculate_lod_step,
)


class VolumePrepWorker(QThread):
    ready = pyqtSignal(int, object, object, object, object)
    error = pyqtSignal(str)

    def __init__(self, candles: List[Iterable[float]], x_min: Optional[float], x_max: Optional[float], seq: int) -> None:
        super().__init__()
        self._candles = candles
        self._x_min = x_min
        self._x_max = x_max
        self._seq = seq

    def run(self) -> None:
        try:
            x_vals = []
            vol_vals = []
            is_up = []
            for idx, candle in enumerate(self._candles):
                try:
                    x_vals.append(float(candle[0]))
                    vol_vals.append(float(candle[5]) if len(candle) > 5 and candle[5] is not None else 0.0)
                    o = float(candle[1])
                    c = float(candle[4])
                    is_up.append(c >= o)
                except Exception:
                    x_vals.append(float(idx))
                    vol_vals.append(0.0)
                    is_up.append(True)
            view_hint = None
            if self._x_min is not None and self._x_max is not None and x_vals:
                start_idx = max(0, bisect_left(x_vals, self._x_min) - 10)
                end_idx = min(len(x_vals), bisect_right(x_vals, self._x_max) + 10)
                visible_count = max(0, end_idx - start_idx)
                step = calculate_lod_step(visible_count, MAX_VISIBLE_BARS_DENSE)
                view_hint = (start_idx, end_idx, step, float(self._x_min), float(self._x_max))
            self.ready.emit(self._seq, x_vals, vol_vals, is_up, view_hint)
        except Exception as exc:
            self.error.emit(str(exc))


class CandlestickItem(pg.GraphicsObject):
    def __init__(
        self,
        data: List[Iterable[float]],
        up_color: QColor,
        down_color: QColor,
        render_callback: Optional[Callable[[], None]] = None,
        bar_colors: Optional[List[Optional[QColor]]] = None,
    ) -> None:
        super().__init__()
        self.data = data
        self.base_color = up_color
        self.down_color = down_color
        self.bar_colors = bar_colors if bar_colors is not None else []
        self.picture = QPicture()
        self._cached_bounds: Optional[QRectF] = None
        self._is_painting = False
        self.candle_width_ms = 60_000 * 0.8
        self._ts_cache: List[float] = []
        self._chunk_size = 300
        self._chunk_cache: dict[int, QPicture] = {}
        self._line_chunk_cache: dict[int, QPicture] = {}
        self._pen_up = pg.mkPen(self.base_color, width=1)
        self._pen_down = pg.mkPen(self.down_color, width=1)
        self._brush_up = pg.mkBrush(self.base_color)
        self._brush_down = pg.mkBrush(self.down_color)
        self._pen_cache: dict[tuple[int, int, int, int], pg.QtGui.QPen] = {}
        self._brush_cache: dict[tuple[int, int, int, int], pg.QtGui.QBrush] = {}
        self._render_callback = render_callback
        self._bounds_min: Optional[float] = None
        self._bounds_max: Optional[float] = None
        self._bounds_dirty = True
        self._x: Optional[np.ndarray] = None
        self._open: Optional[np.ndarray] = None
        self._high: Optional[np.ndarray] = None
        self._low: Optional[np.ndarray] = None
        self._close: Optional[np.ndarray] = None
        self.generate_picture()

    def set_candle_width(self, width_ms: float) -> None:
        if width_ms <= 0:
            return
        if abs(width_ms - self.candle_width_ms) < 1.0:
            return
        self.candle_width_ms = width_ms
        self._chunk_cache = {}
        self._line_chunk_cache = {}
        self._bounds_dirty = True
        try:
            self.update()
        except RuntimeError:
            pass

    def _get_pen(self, color: QColor) -> pg.QtGui.QPen:
        try:
            key = color.getRgb()
        except Exception:
            return pg.mkPen(color, width=1)
        pen = self._pen_cache.get(key)
        if pen is None:
            pen = pg.mkPen(color, width=1)
            self._pen_cache[key] = pen
        return pen

    def _get_brush(self, color: QColor) -> pg.QtGui.QBrush:
        try:
            key = color.getRgb()
        except Exception:
            return pg.mkBrush(color)
        brush = self._brush_cache.get(key)
        if brush is None:
            brush = pg.mkBrush(color)
            self._brush_cache[key] = brush
        return brush

    def generate_picture(self) -> None:
        if self._is_painting:
            return
        self._is_painting = True
        try:
            self.picture = QPicture()
            if len(self.data) == 0:
                self._cached_bounds = QRectF(0, 0, 1, 1)
                self._bounds_dirty = False
                return
            if not self._bounds_dirty and self._cached_bounds is not None:
                return
            w = (self.candle_width_ms / 2.0) if self.candle_width_ms else 0.3
            if self._low is not None and self._high is not None and self._x is not None:
                mask = np.isfinite(self._low) & np.isfinite(self._high) & (self._low > 0) & (self._high > 0)
                if np.any(mask):
                    y_min = float(np.nanmin(self._low[mask]))
                    y_max = float(np.nanmax(self._high[mask]))
                    x_min = float(np.nanmin(self._x[mask])) - w
                    x_max = float(np.nanmax(self._x[mask])) + w
                    self._cached_bounds = QRectF(x_min, y_min, x_max - x_min, y_max - y_min)
                    self._bounds_min = y_min
                    self._bounds_max = y_max
                    self._bounds_dirty = False
                else:
                    self._cached_bounds = QRectF(0, 0, 1, 1)
                    self._bounds_dirty = False
            else:
                self._cached_bounds = QRectF(0, 0, 1, 1)
                self._bounds_dirty = False
        finally:
            self._is_painting = False

    def paint(self, painter: QPainter, option, widget) -> None:
        try:
            if not self.data:
                return
            w = (self.candle_width_ms / 2.0) if self.candle_width_ms else 0.3
            try:
                vb = self.getViewBox()
            except Exception:
                vb = None
            if vb and self._ts_cache:
                try:
                    (x_range, _) = vb.viewRange()
                    x_min_view, x_max_view = x_range
                except Exception:
                    x_min_view, x_max_view = None, None
            else:
                x_min_view, x_max_view = None, None
            if x_min_view is not None and x_max_view is not None and self._ts_cache:
                start_idx = max(0, bisect_left(self._ts_cache, x_min_view) - 10)
                end_idx = min(len(self.data), bisect_right(self._ts_cache, x_max_view) + 10)
            else:
                start_idx, end_idx = 0, len(self.data)
            visible_count = max(0, end_idx - start_idx)
            if visible_count > 750:
                chunk_start = start_idx // self._chunk_size
                chunk_end = (end_idx - 1) // self._chunk_size if end_idx > 0 else chunk_start
                for chunk_idx in range(chunk_start, chunk_end + 1):
                    picture = self._line_chunk_cache.get(chunk_idx)
                    if picture is None:
                        picture = self._render_chunk(chunk_idx, w, line_mode=True)
                        self._line_chunk_cache[chunk_idx] = picture
                    painter.drawPicture(0, 0, picture)
                return
            chunk_start = start_idx // self._chunk_size
            chunk_end = (end_idx - 1) // self._chunk_size if end_idx > 0 else chunk_start
            for chunk_idx in range(chunk_start, chunk_end + 1):
                picture = self._chunk_cache.get(chunk_idx)
                if picture is None:
                    picture = self._render_chunk(chunk_idx, w, line_mode=False)
                    self._chunk_cache[chunk_idx] = picture
                painter.drawPicture(0, 0, picture)
        except RuntimeError:
            pass
        if self._render_callback is not None:
            try:
                self._render_callback()
            except Exception:
                pass

    def boundingRect(self) -> QRectF:
        if self._cached_bounds is not None and self._cached_bounds.isValid():
            return self._cached_bounds
        if self._ts_cache:
            try:
                x_min = float(self._ts_cache[0])
                x_max = float(self._ts_cache[-1])
                return QRectF(x_min, 0, x_max - x_min, 1)
            except Exception:
                pass
        return QRectF(self.picture.boundingRect())

    def set_data(
        self,
        data: List[Iterable[float]],
        bar_colors: Optional[List[Optional[QColor]]] = None,
        invalidate_from_idx: Optional[int] = None,
    ) -> None:
        if bar_colors is not None:
            self.bar_colors = bar_colors
        previous_len = len(self.data)
        self.data = data
        self._ts_cache = []
        try:
            arr = np.asarray(self.data, dtype=np.float64)
            if arr.ndim == 2 and arr.shape[1] >= 5:
                self._x = arr[:, 0]
                self._open = arr[:, 1]
                self._high = arr[:, 2]
                self._low = arr[:, 3]
                self._close = arr[:, 4]
            else:
                self._x = None
                self._open = None
                self._high = None
                self._low = None
                self._close = None
        except Exception:
            self._x = None
            self._open = None
            self._high = None
            self._low = None
            self._close = None
        if invalidate_from_idx is None or len(self.data) < previous_len:
            self._chunk_cache = {}
            self._line_chunk_cache = {}
            self._bounds_dirty = True
        else:
            start_chunk = max(0, int(invalidate_from_idx) // self._chunk_size)
            self._chunk_cache = {k: v for k, v in self._chunk_cache.items() if k < start_chunk}
            self._line_chunk_cache = {k: v for k, v in self._line_chunk_cache.items() if k < start_chunk}
            if self._low is not None and self._high is not None:
                try:
                    lo = float(np.nanmin(self._low[invalidate_from_idx:]))
                    hi = float(np.nanmax(self._high[invalidate_from_idx:]))
                    if self._bounds_min is None or self._bounds_max is None:
                        self._bounds_dirty = True
                    else:
                        if lo < self._bounds_min or hi > self._bounds_max:
                            self._bounds_dirty = True
                except Exception:
                    self._bounds_dirty = True
        for candle in self.data:
            if len(candle) < 1:
                continue
            try:
                ts = float(candle[0])
            except (ValueError, TypeError):
                ts = 0.0
            self._ts_cache.append(ts)
        bounds_was_dirty = self._bounds_dirty
        self.generate_picture()
        if bounds_was_dirty:
            try:
                self.informViewBoundsChanged()
            except RuntimeError:
                pass
        try:
            self.update()
        except RuntimeError:
            pass

    def _render_chunk(self, chunk_idx: int, w: float, line_mode: bool = False) -> QPicture:
        picture = QPicture()
        painter = QPainter(picture)
        try:
            start_idx = chunk_idx * self._chunk_size
            end_idx = min(len(self.data), start_idx + self._chunk_size)
            for idx in range(start_idx, end_idx):
                if self._x is not None and self._open is not None and self._high is not None and self._low is not None and self._close is not None:
                    x_val = float(self._x[idx])
                    open_price = float(self._open[idx])
                    high = float(self._high[idx])
                    low = float(self._low[idx])
                    close = float(self._close[idx])
                else:
                    candle = self.data[idx]
                    if len(candle) < 5:
                        continue
                    try:
                        x_val = float(candle[0])
                        open_price = float(candle[1])
                        high = float(candle[2])
                        low = float(candle[3])
                        close = float(candle[4])
                    except (ValueError, TypeError):
                        continue
                if low <= 0 or high <= 0 or open_price <= 0 or close <= 0:
                    continue
                if not (np.isfinite(low) and np.isfinite(high) and np.isfinite(open_price) and np.isfinite(close)):
                    continue
                if high < low:
                    high, low = low, high
                price_avg = (open_price + close) / 2.0
                if price_avg <= 0 or not np.isfinite(price_avg):
                    continue
                price_range = high - low
                if price_range > price_avg * 10:
                    continue
                if low < price_avg * 0.1 or high > price_avg * 10:
                    continue

                is_bear = close < open_price
                if len(self.bar_colors) > 0 and idx < len(self.bar_colors) and self.bar_colors[idx] is not None:
                    current_color = self.bar_colors[idx]
                else:
                    current_color = self.down_color if is_bear else self.base_color

                wick_pen = self._get_pen(current_color)
                if line_mode:
                    painter.setPen(wick_pen)
                    painter.drawLine(QPointF(x_val, low), QPointF(x_val, high))
                else:
                    body_pen = self._get_pen(current_color)
                    painter.setBrush(self._get_brush(current_color))
                    if high != low:
                        painter.setPen(wick_pen)
                        painter.drawLine(QPointF(x_val, low), QPointF(x_val, high))
                    painter.setPen(body_pen)
                    body_top = max(open_price, close)
                    body_bottom = min(open_price, close)
                    body_height = body_top - body_bottom
                    if body_height > 0:
                        painter.drawRect(QRectF(x_val - w, body_bottom, w * 2, body_height))
                    else:
                        painter.drawLine(QPointF(x_val - w, close), QPointF(x_val + w, close))
        finally:
            painter.end()
        return picture


class CandlestickChart:
    def __init__(self, plot_widget: pg.PlotWidget, up_color: str, down_color: str) -> None:
        self.plot_widget = plot_widget
        self.base_color = QColor(up_color)
        self.down_color = QColor(down_color)
        self.candles: List[List[float]] = []
        self.bar_colors: List[Optional[QColor]] = []
        self.volume_item: Optional[object] = None
        self.volume_max: float = 0.0
        self._volume_worker: Optional[VolumePrepWorker] = None
        self._pending_volume_data: Optional[List[Iterable[float]]] = None
        self._volume_worker_seq = 0
        self._volume_worker_last_ms = 0
        self._volume_data_key: Optional[Tuple[int, int]] = None
        self._volume_live_interval_ms = 2000
        self._volume_live_updates_enabled = True
        self._last_volume_live_update_ms = 0
        self._volume_view_timer = QTimer()
        self._volume_view_timer.setSingleShot(True)
        self._volume_view_timer.timeout.connect(self._flush_volume_view)
        self._volume_view_delay_ms = 180
        self._bulk_update = False
        self.price_line: Optional[pg.InfiniteLine] = None
        self.price_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None
        self.price_label_bg: Optional[pg.QtWidgets.QGraphicsPathItem] = None
        self.cursor_price_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None
        self.cursor_price_label_bg: Optional[pg.QtWidgets.QGraphicsPathItem] = None
        self.cursor_time_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None
        self.cursor_time_label_bg: Optional[pg.QtWidgets.QGraphicsPathItem] = None
        self.header_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None
        self.hover_band: Optional[pg.QtWidgets.QGraphicsRectItem] = None
        self.price_tick: Optional[pg.QtWidgets.QGraphicsLineItem] = None
        self.session_lines: List[pg.InfiniteLine] = []
        self.volume_baseline: Optional[pg.InfiniteLine] = None
        self._volume_baseline_y: Optional[float] = None
        self._hover_index: Optional[int] = None
        self.timeframe_ms: Optional[int] = None
        self.last_kline_ts_ms: Optional[int] = None
        self.last_close_ms: Optional[int] = None
        self.last_event_ms: Optional[int] = None
        self.time_offset_ms: int = 0
        self._countdown_timer = QTimer()
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._refresh_countdown)
        self._last_trade_update_ms: int = 0
        self._live_price: Optional[float] = None
        self._live_open: Optional[float] = None
        self._last_live_snapshot: Optional[Tuple[int, float, float, float, float, float]] = None
        self._live_redraw_timer = QTimer()
        self._live_redraw_timer.setSingleShot(True)
        self._live_redraw_timer.timeout.connect(self._flush_live_redraw)
        self._live_redraw_delay_ms = 40
        self.hover_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None
        self.hover_label_bg: Optional[pg.QtWidgets.QGraphicsPathItem] = None
        self.hover_outline: Optional[pg.QtWidgets.QGraphicsRectItem] = None
        self.crosshair_v: Optional[pg.InfiniteLine] = None
        self.crosshair_h: Optional[pg.InfiniteLine] = None
        self.cursor_dot: Optional[pg.QtWidgets.QGraphicsEllipseItem] = None
        self.cursor_time_tick: Optional[pg.QtWidgets.QGraphicsLineItem] = None
        self.history_end_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None
        self.empty_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None
        self.history_end_reached = False
        self._ts_cache: List[float] = []
        self._candle_width_ms = 60_000 * 0.8
        self._render_count = 0
        self._render_last_ts = time.time()
        self._render_fps = 0.0
        self._last_render_ms = 0
        self._view_redraw_timer = QTimer()
        self._view_redraw_timer.setSingleShot(True)
        self._view_redraw_timer.timeout.connect(self._flush_view_redraw)
        self._fast_mode = False
        self._fast_mode_timer = QTimer()
        self._fast_mode_timer.setSingleShot(True)
        self._fast_mode_timer.timeout.connect(self._disable_fast_mode)
        self._mouse_move_timer = QTimer()
        self._mouse_move_timer.setSingleShot(True)
        self._mouse_move_timer.timeout.connect(self._flush_mouse_move)
        self._mouse_move_delay_ms = 30
        self._pending_mouse_pos = None
        self._session_signature: Optional[Tuple[int, int]] = None

        self.plot_widget.setClipToView(True)
        try:
            self.plot_widget.setCursor(Qt.CursorShape.CrossCursor)
        except Exception:
            pass
        try:
            view_box = self.plot_widget.getViewBox()
            if view_box:
                view_box.enableAutoRange('x', False)
                view_box.enableAutoRange('y', False)
                view_box.sigRangeChanged.connect(self._on_view_changed)
                self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        except Exception:
            pass

        self.item = CandlestickItem([], self.base_color, self.down_color, render_callback=self._on_render)
        self.item.candle_width_ms = self._candle_width_ms
        self.plot_widget.addItem(self.item)

        self._setup_price_axis()
        self._setup_date_index_axis()
        self._countdown_timer.start()

    def _setup_price_axis(self) -> None:
        chart = self

        class PriceAxis(pg.AxisItem):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._chart = chart

            def tickStrings(self, values, scale, spacing):
                out = []
                for v in values:
                    try:
                        if not np.isfinite(v) or v <= 0:
                            out.append('')
                            continue
                        out.append(self._chart._format_price_value(float(v)))
                    except Exception:
                        out.append('')
                return out

            def generateDrawSpecs(self, p):
                axis_spec, tick_specs, text_specs = super().generateDrawSpecs(p)
                if axis_spec is not None:
                    axis_spec = (pg.mkPen(QColor(0, 0, 0, 0)), axis_spec[1], axis_spec[2])
                return (axis_spec, tick_specs, text_specs)

            def tickValues(self, minVal, maxVal, size):
                try:
                    span = float(maxVal) - float(minVal)
                except Exception:
                    return []
                if span <= 0:
                    return []
                target_ticks = max(2, int(size / 50))
                raw_step = span / target_ticks
                if raw_step <= 0:
                    return []
                exp = math.floor(math.log10(raw_step))
                base = 10 ** exp
                for mult in (1, 2, 5, 10):
                    step = base * mult
                    if step >= raw_step:
                        break
                start = math.floor(float(minVal) / step) * step
                values = []
                current = start
                max_val = float(maxVal)
                while current <= max_val:
                    values.append(current)
                    current += step
                return [(step, values)]

            def mouseDragEvent(self, ev) -> None:
                if ev.button() != Qt.MouseButton.LeftButton:
                    ev.ignore()
                    return
                ev.accept()
                view = self.linkedView()
                if view is None:
                    return
                dy = ev.pos().y() - ev.lastPos().y()
                scale = 1.01 ** dy
                try:
                    center = view.mapSceneToView(ev.scenePos())
                except Exception:
                    center = None
                if center is not None:
                    view.scaleBy((1.0, scale), center=center)
                else:
                    view.scaleBy((1.0, scale))

        try:
            price_axis = PriceAxis(orientation='right')
            font = QFont()
            font.setPointSize(7)
            price_axis.setTickFont(font)
            self.plot_widget.setAxisItems({'right': price_axis})
            self.plot_widget.showAxis('right')
            self.plot_widget.hideAxis('left')
            try:
                price_axis.setStyle(showValues=True)
            except Exception:
                pass
            price_axis.setWidth(60)
        except Exception:
            pass

    def _setup_date_index_axis(self) -> None:
        class DateIndexAxis(pg.AxisItem):
            def __init__(self, parent_chart, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.parent_chart = parent_chart
                self._day_rollover_timestamps: List[float] = []
                self._last_step_ms: Optional[int] = None
                self._update_day_rollovers()

            def _update_day_rollovers(self):
                self._day_rollover_timestamps = []
                candles = self.parent_chart.candles
                if not candles:
                    return
                last_day = None
                for idx, candle in enumerate(candles):
                    try:
                        ts_ms = float(candle[0])
                        dt = datetime.fromtimestamp(ts_ms / 1000.0)
                        current_day = dt.date()
                        if last_day is not None and current_day != last_day:
                            self._day_rollover_timestamps.append(ts_ms)
                        last_day = current_day
                    except Exception:
                        continue

            def _pick_step_ms(self, span_ms: float, target_ticks: int) -> int:
                if span_ms <= 0:
                    return 60_000
                steps = [
                    1_000,
                    2_000,
                    5_000,
                    10_000,
                    15_000,
                    30_000,
                    60_000,
                    120_000,
                    300_000,
                    600_000,
                    900_000,
                    1_800_000,
                    3_600_000,
                    7_200_000,
                    14_400_000,
                    21_600_000,
                    43_200_000,
                    86_400_000,
                    172_800_000,
                    604_800_000,
                    2_592_000_000,
                ]
                for step in steps:
                    if span_ms / step <= target_ticks:
                        return step
                return steps[-1]

            def _snap_step_ms(self, step_ms: int) -> int:
                base = self.parent_chart.timeframe_ms or 60_000
                if step_ms < base:
                    return base
                mult = int(max(1, round(step_ms / base)))
                return base * mult

            def tickValues(self, minVal, maxVal, size):
                try:
                    span_ms = float(maxVal) - float(minVal)
                except Exception:
                    return []
                if span_ms <= 0:
                    return []
                target_ticks = max(2, int(size / 120))
                step_ms = self._pick_step_ms(span_ms, target_ticks)
                step_ms = self._snap_step_ms(step_ms)
                self._last_step_ms = step_ms
                try:
                    start = math.floor(float(minVal) / step_ms) * step_ms
                except Exception:
                    start = float(minVal)
                values = []
                current = start
                max_val = float(maxVal)
                while current <= max_val:
                    values.append(current)
                    current += step_ms
                return [(1, values)]

            def tickStrings(self, values, scale, spacing):
                out = []
                step_ms = self._last_step_ms or 60_000
                span_ms = 0.0
                if values:
                    try:
                        span_ms = float(max(values)) - float(min(values))
                    except Exception:
                        span_ms = 0.0
                if step_ms < 60_000:
                    fmt = '%H:%M:%S'
                elif step_ms < 3_600_000 and span_ms <= 2 * 86_400_000:
                    fmt = '%H:%M'
                elif step_ms < 86_400_000:
                    fmt = '%b %d %H:%M'
                elif step_ms < 2_592_000_000:
                    fmt = '%b %d'
                else:
                    fmt = '%Y-%m'
                for v in values:
                    try:
                        ts_ms = float(v)
                    except Exception:
                        out.append('')
                        continue
                    try:
                        dt = datetime.fromtimestamp(ts_ms / 1000.0)
                        date_str = dt.strftime(fmt)
                    except Exception:
                        out.append('')
                        continue
                    out.append(date_str)
                return out

            def generateDrawSpecs(self, p):
                axis_spec, tick_specs, text_specs = super().generateDrawSpecs(p)
                if axis_spec is not None:
                    axis_spec = (pg.mkPen(QColor(0, 0, 0, 0)), axis_spec[1], axis_spec[2])
                return (axis_spec, tick_specs, text_specs)

            def mouseDragEvent(self, ev) -> None:
                if ev.button() != Qt.MouseButton.LeftButton:
                    ev.ignore()
                    return
                ev.accept()
                view = self.linkedView()
                if view is None:
                    return
                dx = ev.pos().x() - ev.lastPos().x()
                scale = 1.01 ** dx
                try:
                    center = view.mapSceneToView(ev.scenePos())
                except Exception:
                    center = None
                if center is not None:
                    view.scaleBy((scale, 1.0), center=center)
                else:
                    view.scaleBy((scale, 1.0))

        bottom_axis = DateIndexAxis(self, orientation='bottom')
        font = QFont()
        font.setPointSize(7)
        bottom_axis.setTickFont(font)
        bottom_axis.setStyle(autoExpandTextSpace=False, tickTextOffset=2)
        bottom_axis.setHeight(38)
        try:
            bottom_axis.setStyle(showValues=True)
        except Exception:
            pass
        self.plot_widget.setAxisItems({'bottom': bottom_axis})
        left_axis = self.plot_widget.getAxis('left')
        if left_axis:
            left_axis.setTickFont(font)
        self._date_index_axis = bottom_axis

    def _update_candle_width_from_view(self) -> None:
        if self.timeframe_ms is None:
            return
        try:
            view_box = self.plot_widget.getViewBox()
            x_range, _ = view_box.viewRange()
            span = float(x_range[1]) - float(x_range[0])
        except Exception:
            return
        if span <= 0:
            return
        visible_bars = span / float(self.timeframe_ms)
        if visible_bars <= 0:
            return
        base_width = float(self.timeframe_ms) * 0.8
        scale = min(1.0, max(0.3, 240.0 / visible_bars))
        new_width = base_width * scale
        if abs(new_width - self._candle_width_ms) >= 1.0:
            self._candle_width_ms = new_width
            self.item.set_candle_width(new_width)

    def _on_view_changed(self) -> None:
        self._enable_fast_mode()
        if self._view_redraw_timer.isActive():
            return
        self._view_redraw_timer.start(50)
        self._schedule_volume_view_update()

    def _enable_fast_mode(self) -> None:
        if self._fast_mode:
            self._fast_mode_timer.start(150)
            return
        self._fast_mode = True
        if self.hover_band is not None:
            self.hover_band.hide()
        if self.cursor_dot is not None:
            self.cursor_dot.hide()
        if self.hover_outline is not None:
            self.hover_outline.hide()
        self._fast_mode_timer.start(150)

    def _disable_fast_mode(self) -> None:
        self._fast_mode = False
        try:
            self._refresh_hover_if_needed()
        except Exception:
            pass

    def _flush_view_redraw(self) -> None:
        try:
            self._update_candle_width_from_view()
            self.item.generate_picture()
            self.item.update()
            self._update_price_line()
            self._update_history_end_label()
            self._update_header_position()
            if not self._fast_mode:
                self._refresh_hover_if_needed()
            if not self.candles:
                self._show_empty_state()
        except Exception:
            pass

    def _schedule_volume_view_update(self) -> None:
        if self.volume_item is None:
            return
        if self._volume_view_timer.isActive():
            return
        self._volume_view_timer.start(self._volume_view_delay_ms)

    def _flush_volume_view(self) -> None:
        if self.volume_item is None:
            return
        try:
            view_box = self.plot_widget.getViewBox()
            if view_box is None:
                return
            x_range, y_range = view_box.viewRange()
            x_min, x_max = x_range
            y_min, y_max = y_range
        except Exception:
            return
        if isinstance(self.volume_item, VolumeHistogramItem):
            try:
                self.volume_item.set_view_bounds(x_min, x_max, y_min, y_max)
            except Exception:
                pass
            try:
                start_idx = max(0, bisect_left(self._ts_cache, x_min) - 10)
                end_idx = min(len(self._ts_cache), bisect_right(self._ts_cache, x_max) + 10)
                visible_count = max(0, end_idx - start_idx)
                step = calculate_lod_step(visible_count, MAX_VISIBLE_BARS_DENSE)
                if visible_count > 0:
                    self.volume_item.set_view_hint(x_min, x_max, start_idx, end_idx, step)
            except Exception:
                pass
            self.volume_item.update()

    def _update_volume_histogram(self, candles: List[Iterable[float]]) -> None:
        if not candles:
            volume_color = QColor(self.base_color)
            bar_width = (self.timeframe_ms or 60_000) * 0.8
            self.volume_item, self.volume_max = update_volume_histogram(
                plot_widget=self.plot_widget,
                volume_item=self.volume_item if isinstance(self.volume_item, VolumeHistogramItem) else None,
                base_color=volume_color,
                data=[],
                extract_volume=lambda *_: 0.0,
                extract_x=lambda *_: 0.0,
            )
            if isinstance(self.volume_item, VolumeHistogramItem):
                self.volume_item.clear_tail()
            self._update_volume_baseline()
            return
        try:
            last_ts = int(candles[-1][0])
        except Exception:
            last_ts = 0
        data_key = (len(candles), last_ts)
        if self._volume_data_key == data_key:
            return
        self._volume_data_key = data_key
        if self._volume_worker and self._volume_worker.isRunning():
            self._pending_volume_data = candles
            return
        self._start_volume_worker(candles)

    def _update_volume_tail(self) -> None:
        if not isinstance(self.volume_item, VolumeHistogramItem):
            return
        if not self.candles:
            self.volume_item.clear_tail()
            return
        try:
            last = self.candles[-1]
            ts = float(last[0])
            vol = float(last[5]) if len(last) > 5 else 0.0
            is_up = float(last[4]) >= float(last[1])
        except Exception:
            return
        self.volume_item.set_tail(len(self.candles) - 1, ts, vol, is_up)

    def _start_volume_worker(self, candles: List[Iterable[float]]) -> None:
        x_min = None
        x_max = None
        try:
            view_box = self.plot_widget.getViewBox()
            x_range, _ = view_box.viewRange()
            x_min, x_max = x_range
        except Exception:
            pass
        self._volume_worker_seq += 1
        seq = self._volume_worker_seq
        self._volume_worker_last_start = time.time()
        self._volume_worker = VolumePrepWorker(candles, x_min, x_max, seq)
        self._volume_worker.ready.connect(self._on_volume_ready)
        self._volume_worker.error.connect(self._on_volume_error)
        self._volume_worker.finished.connect(self._on_volume_finished)
        self._volume_worker.start()

    def _on_volume_ready(self, seq, x_vals, vol_vals, is_up, view_hint) -> None:
        if seq != self._volume_worker_seq:
            return
        if hasattr(self, "_volume_worker_last_start"):
            self._volume_worker_last_ms = int((time.time() - self._volume_worker_last_start) * 1000)
        self._last_volume_live_update_ms = int(time.time() * 1000)
        volume_color = QColor(self.base_color)
        bar_width = (self.timeframe_ms or 60_000) * 0.8
        self.volume_item, self.volume_max = update_volume_histogram_arrays(
            plot_widget=self.plot_widget,
            volume_item=self.volume_item if isinstance(self.volume_item, VolumeHistogramItem) else None,
            base_color=volume_color,
            x_vals=x_vals,
            vol_vals=vol_vals,
            is_up=is_up,
            up_color=self.base_color,
            down_color=self.down_color,
            volume_height_ratio=0.15,
            bar_width=bar_width,
        )
        if isinstance(self.volume_item, VolumeHistogramItem) and view_hint:
            start_idx, end_idx, step, x_min, x_max = view_hint
            self.volume_item.set_view_hint(x_min, x_max, start_idx, end_idx, step)
        self._update_volume_tail()
        try:
            view_box = self.plot_widget.getViewBox()
            (x_range, y_range) = view_box.viewRange()
            if isinstance(self.volume_item, VolumeHistogramItem):
                self.volume_item.set_view_bounds(x_range[0], x_range[1], y_range[0], y_range[1])
        except Exception:
            pass
        self._update_volume_baseline()

    def _on_volume_error(self, message: str) -> None:
        _ = message

    def _on_volume_finished(self) -> None:
        if self._pending_volume_data is not None:
            pending = self._pending_volume_data
            self._pending_volume_data = None
            self._start_volume_worker(pending)

    def _update_volume_baseline(self) -> None:
        try:
            view_box = self.plot_widget.getViewBox()
            if view_box is None:
                return
            (_, y_range) = view_box.viewRange()
            visible_y_min = y_range[0]
        except Exception:
            return
        if self._volume_baseline_y is not None and abs(visible_y_min - self._volume_baseline_y) < 1e-6:
            return
        self._volume_baseline_y = visible_y_min
        pen = pg.mkPen(QColor(25, 29, 38, 200), width=1)
        if self.volume_baseline is None:
            self.volume_baseline = pg.InfiniteLine(pos=visible_y_min, angle=0, pen=pen)
            self.volume_baseline.setZValue(9)
            self.plot_widget.addItem(self.volume_baseline)
        else:
            self.volume_baseline.setPen(pen)
            self.volume_baseline.setValue(visible_y_min)

    def _show_empty_state(self) -> None:
        if self.empty_label is None:
            self.empty_label = pg.QtWidgets.QGraphicsTextItem()
            self.empty_label.setDefaultTextColor(QColor('#6B7280'))
            self.empty_label.setZValue(20)
            self.empty_label.setPlainText('No data')
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.empty_label)
        plot_item = self.plot_widget.getPlotItem()
        scene_rect = plot_item.sceneBoundingRect()
        label_rect = self.empty_label.boundingRect()
        x = scene_rect.center().x() - (label_rect.width() / 2.0)
        y = scene_rect.center().y() - (label_rect.height() / 2.0)
        self.empty_label.setPos(x, y)
        self.empty_label.show()

    def _hide_empty_state(self) -> None:
        if self.empty_label is not None:
            self.empty_label.hide()

    def set_historical_data(self, data: List[Iterable[float]], auto_range: bool = True, normalized: bool = False) -> None:
        normalized_data = []
        if normalized:
            normalized_data = data
        else:
            for c in data:
                if not isinstance(c, (list, tuple)) or len(c) < 5:
                    continue
                ts, o, h, l, cl = c[0], c[1], c[2], c[3], c[4]
                vol = c[5] if len(c) > 5 else 0.0
                try:
                    o, h, l, cl = float(o), float(h), float(l), float(cl)
                    if o <= 0 or h <= 0 or l <= 0 or cl <= 0:
                        continue
                    if not (np.isfinite(o) and np.isfinite(h) and np.isfinite(l) and np.isfinite(cl)):
                        continue
                except (ValueError, TypeError):
                    continue
                normalized_data.append([ts, o, h, l, cl, vol])
        if not normalized_data:
            self.candles = []
            self._ts_cache = []
            self.item.set_data([])
            self._update_volume_histogram([])
            self._show_empty_state()
            return
        self.candles = normalized_data
        self._ts_cache = [float(c[0]) for c in self.candles]
        self.item.candle_width_ms = self._candle_width_ms
        self.item.set_data(self.candles, bar_colors=self.bar_colors)
        if self._bulk_update:
            self._hide_empty_state()
            return
        self._update_volume_histogram(self.candles)
        self._update_volume_tail()
        self._update_price_line()
        self._update_history_end_label()
        self._update_session_lines_if_needed()
        self._refresh_hover_if_needed()
        self._hide_empty_state()
        if auto_range:
            self._auto_range()

    def begin_bulk_update(self) -> None:
        self._bulk_update = True

    def end_bulk_update(self, auto_range: bool = False) -> None:
        self._bulk_update = False
        if not self.candles:
            self._show_empty_state()
            return
        self._update_volume_histogram(self.candles)
        self._update_volume_tail()
        self._update_price_line()
        self._update_history_end_label()
        self._update_session_lines_if_needed()
        self._refresh_hover_if_needed()
        self._hide_empty_state()
        if auto_range:
            self._auto_range()

    def set_timeframe(self, timeframe: str) -> None:
        self.timeframe_ms = self._parse_timeframe_ms(timeframe)
        self._candle_width_ms = (self.timeframe_ms or 60_000) * 0.8
        self.item.set_candle_width(self._candle_width_ms)

    def update_live_kline(self, kline: dict) -> None:
        try:
            ts_ms = int(kline.get('ts_ms', 0))
            if ts_ms <= 0:
                return
            o = float(kline.get('open', 0))
            h = float(kline.get('high', 0))
            l = float(kline.get('low', 0))
            c = float(kline.get('close', 0))
            v = float(kline.get('volume', 0))
        except (ValueError, TypeError):
            return
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            return

        self._live_price = c
        self._live_open = o

        if not self.candles:
            self.candles = [[ts_ms, o, h, l, c, v]]
        else:
            last_ts = int(self.candles[-1][0])
            if ts_ms == last_ts:
                self.candles[-1] = [ts_ms, o, h, l, c, v]
            elif ts_ms > last_ts:
                self.candles.append([ts_ms, o, h, l, c, v])
            else:
                return
        self._ts_cache = [float(c[0]) for c in self.candles]

        self.last_kline_ts_ms = ts_ms
        self.last_close_ms = int(kline.get('close_ms', 0)) or None
        self.last_event_ms = int(kline.get('event_ms', 0)) or None
        try:
            self.time_offset_ms = int(kline.get('time_offset_ms', 0))
        except Exception:
            self.time_offset_ms = 0
        self._queue_live_redraw()

    def update_live_trade(self, trade: dict) -> None:
        if not self.candles:
            return
        if self.timeframe_ms is None:
            return
        try:
            ts_ms = int(trade.get('ts_ms', 0))
            price = float(trade.get('price', 0))
            qty = float(trade.get('qty', 0))
        except (ValueError, TypeError):
            return
        if ts_ms <= 0 or price <= 0:
            return

        self._live_price = price
        try:
            self._live_open = float(self.candles[-1][1])
        except Exception:
            pass

        last = self.candles[-1]
        if len(last) < 6:
            return
        last_ts = int(last[0])
        if ts_ms < last_ts or ts_ms >= last_ts + self.timeframe_ms:
            return

        now_ms = int(datetime.now().timestamp() * 1000)
        if now_ms - self._last_trade_update_ms < 100:
            return
        self._last_trade_update_ms = now_ms

        o, h, l, _, v = float(last[1]), float(last[2]), float(last[3]), float(last[4]), float(last[5])
        h = max(h, price)
        l = min(l, price)
        v = v + max(0.0, qty)
        self.candles[-1] = [last_ts, o, h, l, price, v]

        self._queue_live_redraw()

    def _queue_live_redraw(self) -> None:
        if not self.candles:
            return
        last = self.candles[-1]
        if len(last) < 6:
            return
        snapshot = (
            int(last[0]),
            float(last[1]),
            float(last[2]),
            float(last[3]),
            float(last[4]),
            float(last[5]),
        )
        if self._last_live_snapshot == snapshot:
            return
        self._last_live_snapshot = snapshot
        if not self._live_redraw_timer.isActive():
            self._live_redraw_timer.start(self._live_redraw_delay_ms)

    def set_volume_live_updates_enabled(self, enabled: bool) -> None:
        if self._volume_live_updates_enabled == enabled:
            return
        self._volume_live_updates_enabled = enabled

    def _flush_live_redraw(self) -> None:
        if not self.candles:
            return
        tail_idx = max(0, len(self.candles) - 2)
        self.item.set_data(self.candles, bar_colors=self.bar_colors, invalidate_from_idx=tail_idx)
        now_ms = int(time.time() * 1000)
        try:
            last_ts = int(self.candles[-1][0])
        except Exception:
            last_ts = 0
        if self._volume_data_key is None or self._volume_data_key[0] != len(self.candles):
            self._update_volume_histogram(self.candles)
        elif self._volume_live_updates_enabled and (now_ms - self._last_volume_live_update_ms) >= self._volume_live_interval_ms:
            self._volume_data_key = None
            self._update_volume_histogram(self.candles)
        self._update_volume_tail()
        self._update_price_line()
        self._update_history_end_label()
        self._update_session_lines_if_needed()
        if not self._fast_mode:
            self._refresh_hover_if_needed()
        self._hide_empty_state()

    def _auto_range(self) -> None:
        if not self.candles:
            return
        lows = [c[3] for c in self.candles if c[3] > 0]
        highs = [c[2] for c in self.candles if c[2] > 0]
        if not lows or not highs:
            return
        y_min, y_max = min(lows), max(highs)
        price_range = y_max - y_min
        if price_range > 0:
            self.plot_widget.setYRange(y_min - price_range * 0.1, y_max + price_range * 0.1)
        n = len(self.candles)
        show_candles = min(400, n)
        if self._ts_cache:
            start_idx = max(0, n - show_candles)
            start_ts = self._ts_cache[start_idx]
            end_ts = self._ts_cache[-1]
            span = end_ts - start_ts
            if span <= 0:
                span = (self.timeframe_ms or 60_000) * show_candles
            self.plot_widget.setXRange(start_ts - span * 0.05, end_ts + span * 0.05)

    def get_view_index_range(self, margin: int = 10) -> Tuple[Optional[int], Optional[int]]:
        if not self._ts_cache:
            return None, None
        try:
            view_box = self.plot_widget.getViewBox()
            x_range, _ = view_box.viewRange()
            x_min, x_max = x_range
        except Exception:
            return None, None
        try:
            start_idx = max(0, bisect_left(self._ts_cache, x_min) - margin)
            end_idx = min(len(self._ts_cache), bisect_right(self._ts_cache, x_max) + margin)
        except Exception:
            return None, None
        return start_idx, end_idx

    def set_bar_colors(self, bar_colors: List[Optional[QColor]]) -> None:
        if bar_colors == self.bar_colors:
            return
        self.bar_colors = bar_colors
        try:
            self.item.set_data(self.candles, bar_colors=self.bar_colors)
        except RuntimeError:
            pass

    def _update_price_line(self) -> None:
        close_price = self._live_price
        open_price = self._live_open
        if close_price is None:
            if not self.candles:
                return
            last = self.candles[-1]
            if len(last) < 5:
                return
            try:
                open_price = float(last[1])
                close_price = float(last[4])
            except (ValueError, TypeError):
                return
        if open_price is None:
            open_price = close_price
        color = self.base_color if close_price >= open_price else self.down_color
        if self.price_line is None:
            self.price_line = pg.InfiniteLine(
                pos=close_price,
                angle=0,
                pen=pg.mkPen(color=color, width=1, style=Qt.PenStyle.DotLine),
            )
            self.plot_widget.addItem(self.price_line)
        else:
            self.price_line.setPen(pg.mkPen(color=color, width=1, style=Qt.PenStyle.DotLine))
            self.price_line.setValue(close_price)

        label_text = self._format_price_label(close_price)
        if self.price_label is None:
            self.price_label = pg.QtWidgets.QGraphicsTextItem()
            self.price_label.setDefaultTextColor(QColor('#FFFFFF'))
            self.price_label.document().setDocumentMargin(0)
            self.price_label.setZValue(51)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.price_label)
        self.price_label.setPlainText(label_text)

        try:
            self._update_price_label_position(close_price, color)
        except Exception:
            pass
        self._update_price_tick(close_price, color)

    def set_history_end(self, reached: bool) -> None:
        self.history_end_reached = reached
        self._update_history_end_label()

    def _update_history_end_label(self) -> None:
        if not self.history_end_reached or not self.candles:
            if self.history_end_label is not None:
                self.history_end_label.hide()
            return
        first = self.candles[0]
        if len(first) < 5:
            return
        try:
            close_price = float(first[4])
        except (ValueError, TypeError):
            return
        if self.history_end_label is None:
            self.history_end_label = pg.QtWidgets.QGraphicsTextItem()
            self.history_end_label.setDefaultTextColor(QColor('#B2B5BE'))
            self.history_end_label.setZValue(49)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.history_end_label)
        self.history_end_label.setPlainText('End of available data')
        offset = (self.timeframe_ms or 60_000) * 0.6
        x_pos = float(first[0]) + offset
        try:
            view_box = self.plot_widget.getPlotItem().getViewBox()
            scene_pos = view_box.mapViewToScene(QPointF(x_pos, close_price))
            self.history_end_label.setPos(scene_pos)
        except Exception:
            self.history_end_label.setPos(x_pos, close_price)
        self.history_end_label.show()

    def _format_price_label(self, price: float) -> str:
        remaining = ''
        if self.last_close_ms is not None:
            close_ms = self.last_close_ms
            now_ms = int(datetime.now().timestamp() * 1000) + self.time_offset_ms
            delta = max(0, close_ms - now_ms)
            total_sec = int(delta // 1000)
            hours = total_sec // 3600
            minutes = (total_sec % 3600) // 60
            seconds = total_sec % 60
            if hours > 0:
                remaining = f' {hours:02d}:{minutes:02d}:{seconds:02d}'
            else:
                remaining = f' {minutes:02d}:{seconds:02d}'
        price_text = self._format_price_value(price)
        if remaining:
            return f'{price_text}\n{remaining.strip()}'
        return f'{price_text}'

    def _format_price_value(self, price: float) -> str:
        if price >= 1000:
            return f'{price:,.2f}'
        if price >= 100:
            return f'{price:,.2f}'
        if price >= 1:
            return f'{price:,.4f}'
        if price >= 0.01:
            return f'{price:,.6f}'
        if price >= 0.0001:
            return f'{price:,.8f}'
        return f'{price:,.10f}'

    def _parse_timeframe_ms(self, timeframe: str) -> int:
        if not timeframe:
            return 60_000
        try:
            unit = timeframe[-1].lower()
            mult = int(timeframe[:-1])
        except (ValueError, IndexError):
            return 60_000
        if unit == 'm':
            return mult * 60 * 1000
        if unit == 'h':
            return mult * 60 * 60 * 1000
        if unit == 'd':
            return mult * 24 * 60 * 60 * 1000
        if unit == 'w':
            return mult * 7 * 24 * 60 * 60 * 1000
        if unit == 'M':
            return mult * 30 * 24 * 60 * 60 * 1000
        return 60 * 1000

    def _refresh_countdown(self) -> None:
        if self.last_close_ms is None:
            return
        self._update_price_line()

    def _update_price_label_position(self, price: float, color: QColor) -> None:
        if self.price_label is None:
            return
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        if not view_box:
            return
        scene_pos = view_box.mapViewToScene(QPointF(0, price))
        axis = plot_item.getAxis('right')
        axis_rect = axis.sceneBoundingRect() if axis else None

        text_rect = self.price_label.boundingRect()
        padding_x = 4
        padding_y = 2
        pill_width = text_rect.width() + padding_x * 2
        pill_height = text_rect.height() + padding_y * 2

        if axis is not None:
            axis_width = axis.width()
            if axis_width <= 0:
                axis_width = 70
            x = view_box.sceneBoundingRect().right() 
            pill_width = axis_width
        else:
            x = view_box.sceneBoundingRect().right() + 6
        y = scene_pos.y() - (pill_height / 2)

        self.price_label.setPos(x + padding_x, y + padding_y)

        if self.price_label_bg is None:
            self.price_label_bg = pg.QtWidgets.QGraphicsPathItem()
            self.price_label_bg.setZValue(self.price_label.zValue() - 1)
            plot_item.scene().addItem(self.price_label_bg)

        path = QPainterPath()
        radius = 5.0
        left = x
        right = x + pill_width
        top = y
        bottom = y + pill_height
        path.moveTo(left, top)
        path.lineTo(right - radius, top)
        path.quadTo(right, top, right, top + radius)
        path.lineTo(right, bottom - radius)
        path.quadTo(right, bottom, right - radius, bottom)
        path.lineTo(left, bottom)
        path.closeSubpath()
        bg_color = QColor(color)
        bg_color.setAlpha(255)
        self.price_label_bg.setPath(path)
        self.price_label_bg.setBrush(bg_color)
        self.price_label_bg.setPen(pg.mkPen(bg_color))

    def _update_price_tick(self, price: float, color: QColor) -> None:
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        if not view_box:
            return
        axis = plot_item.getAxis('right')
        if axis is None:
            return
        scene_pos = view_box.mapViewToScene(QPointF(0, price))
        axis_rect = axis.sceneBoundingRect()
        x0 = axis_rect.left()
        x1 = x0 + 6
        y = scene_pos.y()
        if self.price_tick is None:
            self.price_tick = pg.QtWidgets.QGraphicsLineItem()
            self.price_tick.setZValue(52)
            plot_item.scene().addItem(self.price_tick)
        pen = pg.mkPen(color, width=2)
        self.price_tick.setPen(pen)
        self.price_tick.setLine(QLineF(x0, y, x1, y))

    def set_header(self, text: str) -> None:
        if self.header_label is None:
            self.header_label = pg.QtWidgets.QGraphicsTextItem()
            self.header_label.setDefaultTextColor(QColor('#B2B5BE'))
            self.header_label.setZValue(60)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.header_label)
        self.header_label.setPlainText(text)
        self._update_header_position()

    def _update_header_position(self) -> None:
        if self.header_label is None:
            return
        plot_item = self.plot_widget.getPlotItem()
        scene_rect = plot_item.sceneBoundingRect()
        self.header_label.setPos(scene_rect.left() + 8, scene_rect.top() + 6)

    def _ensure_hover_label(self) -> None:
        if self.hover_label is None:
            self.hover_label = pg.QtWidgets.QGraphicsTextItem()
            font = QFont()
            font.setPointSize(8)
            font.setWeight(QFont.Weight.Light)
            self.hover_label.setFont(font)
            self.hover_label.setDefaultTextColor(QColor('#B2B5BE'))
            self.hover_label.document().setDocumentMargin(0)
            self.hover_label.setZValue(60)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.hover_label)

    def _update_hover_label_layout(self) -> None:
        if self.hover_label is None:
            return
        plot_item = self.plot_widget.getPlotItem()
        scene_rect = plot_item.sceneBoundingRect()
        padding_x = 6
        padding_y = 3
        text_rect = self.hover_label.boundingRect()
        pill_width = text_rect.width() + padding_x * 2
        pill_height = text_rect.height() + padding_y * 2
        x = scene_rect.left() + 8
        y = scene_rect.top() + 28
        self.hover_label.setPos(x + padding_x, y + padding_y)

        if self.hover_label_bg is None:
            self.hover_label_bg = pg.QtWidgets.QGraphicsPathItem()
            self.hover_label_bg.setZValue(self.hover_label.zValue() - 1)
            plot_item.scene().addItem(self.hover_label_bg)
        path = QPainterPath()
        radius = 6.0
        path.addRoundedRect(QRectF(x, y, pill_width, pill_height), radius, radius)
        bg_color = QColor('#0F141E')
        bg_color.setAlpha(140)
        border_color = QColor(0, 0, 0, 0)
        self.hover_label_bg.setPath(path)
        self.hover_label_bg.setBrush(bg_color)
        self.hover_label_bg.setPen(pg.mkPen(border_color, width=0))
        self.hover_label_bg.show()

    def _update_hover_outline(self, idx: int, open_price: float, close_price: float) -> None:
        if idx < 0 or idx >= len(self.candles):
            return
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        if view_box is None:
            return
        x_val = float(self.candles[idx][0])
        width = self._candle_width_ms or 60_000
        x_left = x_val - (width / 2.0)
        x_right = x_val + (width / 2.0)
        body_top = max(open_price, close_price)
        body_bottom = min(open_price, close_price)
        if body_top == body_bottom:
            body_top = body_bottom + (self._candle_width_ms or 60_000) * 0.000001
        try:
            top_left = view_box.mapViewToScene(QPointF(x_left, body_top))
            bottom_right = view_box.mapViewToScene(QPointF(x_right, body_bottom))
        except Exception:
            return
        rect = QRectF(top_left, bottom_right).normalized()
        if self.hover_outline is None:
            self.hover_outline = pg.QtWidgets.QGraphicsRectItem()
            self.hover_outline.setZValue(56)
            plot_item.scene().addItem(self.hover_outline)
        pen = pg.mkPen(QColor(180, 190, 205, 180), width=1)
        self.hover_outline.setPen(pen)
        self.hover_outline.setBrush(pg.mkBrush(QColor(0, 0, 0, 0)))
        self.hover_outline.setRect(rect)
        self.hover_outline.show()

    def _ensure_crosshair(self) -> None:
        if self.crosshair_v is None:
            pen = pg.mkPen(QColor(70, 75, 90, 110), width=1)
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([6, 8])
            self.crosshair_v = pg.InfiniteLine(angle=90, pen=pen)
            self.crosshair_v.setZValue(55)
            self.plot_widget.addItem(self.crosshair_v)
        if self.crosshair_h is None:
            pen = pg.mkPen(QColor(70, 75, 90, 110), width=1)
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([6, 8])
            self.crosshair_h = pg.InfiniteLine(angle=0, pen=pen)
            self.crosshair_h.setZValue(55)
            self.plot_widget.addItem(self.crosshair_h)
        if self.hover_band is None:
            self.hover_band = pg.QtWidgets.QGraphicsRectItem()
            self.hover_band.setZValue(54)
            band_color = QColor('#2A2E39')
            band_color.setAlpha(60)
            self.hover_band.setBrush(band_color)
            self.hover_band.setPen(pg.mkPen(QColor(0, 0, 0, 0), width=0))
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.hover_band)
        if self.cursor_dot is None:
            self.cursor_dot = pg.QtWidgets.QGraphicsEllipseItem()
            self.cursor_dot.setZValue(57)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.cursor_dot)

    def _update_crosshair(self, x: float, y: float) -> None:
        self._ensure_crosshair()
        snapped_x = x
        if self._ts_cache:
            min_ts = self._ts_cache[0]
            max_ts = self._ts_cache[-1]
            if min_ts <= x <= max_ts:
                idx = self._index_for_time(x)
                if idx is not None and 0 <= idx < len(self._ts_cache):
                    try:
                        snapped_x = float(self._ts_cache[idx])
                    except Exception:
                        snapped_x = x
        if self.crosshair_v is not None:
            self.crosshair_v.setValue(snapped_x)
            self.crosshair_v.show()
        if self.crosshair_h is not None:
            self.crosshair_h.setValue(y)
            self.crosshair_h.show()
        if self.hover_band is not None:
            view_box = self.plot_widget.getPlotItem().getViewBox()
            if view_box is not None:
                try:
                    (x_range, y_range) = view_box.viewRange()
                    y_min, y_max = y_range
                    width = self._candle_width_ms or 60_000
                    rect = QRectF(snapped_x - (width / 2.0), y_min, width, y_max - y_min)
                    self.hover_band.setRect(rect)
                    self.hover_band.show()
                except Exception:
                    pass
        self._update_cursor_dot(snapped_x, y)
        self._update_cursor_price_label(y)
        self._update_cursor_time_label(snapped_x)

    def _hide_crosshair(self) -> None:
        if self.crosshair_v is not None:
            self.crosshair_v.hide()
        if self.crosshair_h is not None:
            self.crosshair_h.hide()
        if self.cursor_price_label is not None:
            self.cursor_price_label.hide()
        if self.cursor_price_label_bg is not None:
            self.cursor_price_label_bg.hide()
        if self.cursor_time_label is not None:
            self.cursor_time_label.hide()
        if self.cursor_time_label_bg is not None:
            self.cursor_time_label_bg.hide()
        if self.hover_band is not None:
            self.hover_band.hide()
        if self.cursor_dot is not None:
            self.cursor_dot.hide()
        if self.cursor_time_tick is not None:
            self.cursor_time_tick.hide()
        if self.hover_outline is not None:
            self.hover_outline.hide()

    def _on_render(self) -> None:
        now = time.time()
        self._render_count += 1
        self._last_render_ms = int(now * 1000)
        delta = now - self._render_last_ts
        if delta >= 1.0:
            self._render_fps = self._render_count / delta
            self._render_count = 0
            self._render_last_ts = now

    def get_render_stats(self) -> tuple[float, int]:
        return self._render_fps, self._last_render_ms

    def get_volume_worker_ms(self) -> int:
        return int(self._volume_worker_last_ms)

    def _index_for_time(self, ts_ms: float) -> Optional[int]:
        if not self._ts_cache:
            return None
        pos = bisect_left(self._ts_cache, ts_ms)
        if pos <= 0:
            return 0
        if pos >= len(self._ts_cache):
            return len(self._ts_cache) - 1
        before = self._ts_cache[pos - 1]
        after = self._ts_cache[pos]
        if abs(ts_ms - before) <= abs(after - ts_ms):
            return pos - 1
        return pos

    def _on_mouse_moved(self, scene_pos) -> None:
        self._pending_mouse_pos = scene_pos
        if self._mouse_move_timer.isActive():
            return
        self._mouse_move_timer.start(self._mouse_move_delay_ms)

    def _flush_mouse_move(self) -> None:
        scene_pos = self._pending_mouse_pos
        if scene_pos is None:
            return
        if not self.candles:
            return
        if self._fast_mode:
            self._hide_crosshair()
            return
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        if not view_box:
            return
        try:
            if not view_box.sceneBoundingRect().contains(scene_pos):
                self._hide_crosshair()
                return
        except Exception:
            pass
        try:
            view_pos = view_box.mapSceneToView(scene_pos)
        except Exception:
            return
        self._update_crosshair(view_pos.x(), view_pos.y())
        idx = self._index_for_time(view_pos.x())
        if idx is None or idx < 0 or idx >= len(self.candles):
            return
        self._hover_index = idx
        candle = self.candles[idx]
        if len(candle) < 5:
            return
        try:
            o = float(candle[1])
            h = float(candle[2])
            l = float(candle[3])
            c = float(candle[4])
        except (ValueError, TypeError):
            return
        if o == 0:
            return
        def fmt_price(val: float) -> str:
            return self._format_price_value(val)

        change = c - o
        pct = (change / o) * 100.0
        sign = '+' if change >= 0 else ''
        text = (
            f'O {fmt_price(o)}  H {fmt_price(h)}  L {fmt_price(l)}  C {fmt_price(c)}  '
            f'{sign}{fmt_price(change)} ({sign}{pct:.2f}%)'
        )

        self._ensure_hover_label()
        if self.hover_label is None:
            return
        color = QColor('#22C55E') if change >= 0 else QColor('#EF5350')
        self.hover_label.setDefaultTextColor(color)
        self.hover_label.setPlainText(text)
        self._update_hover_label_layout()
        self._update_hover_outline(idx, o, c)

    def _refresh_hover_if_needed(self) -> None:
        if self._hover_index is None or not self.candles:
            return
        idx = min(self._hover_index, len(self.candles) - 1)
        candle = self.candles[idx]
        if len(candle) < 5:
            return
        try:
            o = float(candle[1])
            h = float(candle[2])
            l = float(candle[3])
            c = float(candle[4])
        except (ValueError, TypeError):
            return
        if o == 0:
            return
        def fmt_price(val: float) -> str:
            return self._format_price_value(val)
        change = c - o
        pct = (change / o) * 100.0
        sign = '+' if change >= 0 else ''
        text = (
            f'O {fmt_price(o)}  H {fmt_price(h)}  L {fmt_price(l)}  C {fmt_price(c)}  '
            f'{sign}{fmt_price(change)} ({sign}{pct:.2f}%)'
        )
        color = QColor('#22C55E') if change >= 0 else QColor('#EF5350')
        self._ensure_hover_label()
        if self.hover_label is None:
            return
        self.hover_label.setDefaultTextColor(color)
        self.hover_label.setPlainText(text)
        self._update_hover_label_layout()
        self._update_hover_outline(idx, o, c)

    def _clear_session_lines(self) -> None:
        for line in self.session_lines:
            try:
                self.plot_widget.removeItem(line)
            except Exception:
                pass
        self.session_lines = []

    def _update_session_lines_if_needed(self) -> None:
        if not self.candles:
            self._clear_session_lines()
            self._session_signature = None
            return
        try:
            first_day = int(self.candles[0][0] // 86_400_000)
            last_day = int(self.candles[-1][0] // 86_400_000)
        except Exception:
            first_day = last_day = 0
        sig = (first_day, last_day)
        if sig != self._session_signature:
            self._session_signature = sig
            self._clear_session_lines()

    def _ensure_cursor_price_label(self) -> None:
        if self.cursor_price_label is None:
            self.cursor_price_label = pg.QtWidgets.QGraphicsTextItem()
            self.cursor_price_label.setDefaultTextColor(QColor('#FFFFFF'))
            self.cursor_price_label.document().setDocumentMargin(0)
            self.cursor_price_label.setZValue(62)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.cursor_price_label)

    def _update_cursor_price_label(self, price: float) -> None:
        self._ensure_cursor_price_label()
        if self.cursor_price_label is None:
            return
        price_text = self._format_price_value(price)
        self.cursor_price_label.setPlainText(price_text)
        self.cursor_price_label.show()
        self._update_cursor_label_position(price)

    def _update_cursor_label_position(self, price: float) -> None:
        if self.cursor_price_label is None:
            return
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        if not view_box:
            return
        scene_pos = view_box.mapViewToScene(QPointF(0, price))
        axis = plot_item.getAxis('right')

        text_rect = self.cursor_price_label.boundingRect()
        padding_x = 4
        padding_y = 2
        pill_width = text_rect.width() + padding_x * 2
        pill_height = text_rect.height() + padding_y * 2

        if axis is not None:
            axis_width = axis.width()
            if axis_width <= 0:
                axis_width = 70
            x = view_box.sceneBoundingRect().right()
            pill_width = axis_width
        else:
            x = view_box.sceneBoundingRect().right() + 6
        y = scene_pos.y() - (pill_height / 2)

        self.cursor_price_label.setPos(x + padding_x, y + padding_y)

        if self.cursor_price_label_bg is None:
            self.cursor_price_label_bg = pg.QtWidgets.QGraphicsPathItem()
            self.cursor_price_label_bg.setZValue(61)
            plot_item.scene().addItem(self.cursor_price_label_bg)

        path = QPainterPath()
        radius = 5.0
        left = x
        right = x + pill_width
        top = y
        bottom = y + pill_height
        mid = y + (pill_height / 2.0)
        notch = 6.0
        path.moveTo(left, top)
        path.lineTo(right - radius, top)
        path.quadTo(right, top, right, top + radius)
        path.lineTo(right, bottom - radius)
        path.quadTo(right, bottom, right - radius, bottom)
        path.lineTo(left, bottom)
        path.lineTo(left, mid + 4)
        path.lineTo(left - notch, mid)
        path.lineTo(left, mid - 4)
        path.closeSubpath()
        bg_color = QColor('#2A2E39')
        bg_color.setAlpha(255)
        self.cursor_price_label_bg.setPath(path)
        self.cursor_price_label_bg.setBrush(bg_color)
        self.cursor_price_label_bg.setPen(pg.mkPen(bg_color))
        self.cursor_price_label_bg.show()

    def _ensure_cursor_time_label(self) -> None:
        if self.cursor_time_label is None:
            self.cursor_time_label = pg.QtWidgets.QGraphicsTextItem()
            self.cursor_time_label.setDefaultTextColor(QColor('#FFFFFF'))
            self.cursor_time_label.document().setDocumentMargin(0)
            self.cursor_time_label.setZValue(51)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.cursor_time_label)

    def _format_cursor_time(self, ts_ms: float) -> str:
        try:
            dt = datetime.fromtimestamp(ts_ms / 1000.0)
        except Exception:
            return ''
        tf_ms = self.timeframe_ms or 60_000
        if tf_ms >= 86_400_000:
            return dt.strftime('%Y-%m-%d')
        if tf_ms >= 3_600_000:
            return dt.strftime('%Y-%m-%d %H:%M')
        return dt.strftime('%Y-%m-%d %H:%M')

    def _update_cursor_time_label(self, ts_ms: float) -> None:
        self._ensure_cursor_time_label()
        if self.cursor_time_label is None:
            return
        text = self._format_cursor_time(ts_ms)
        if not text:
            return
        self.cursor_time_label.setPlainText(text)
        self.cursor_time_label.show()
        self._update_cursor_time_label_position(ts_ms)

    def _update_cursor_time_label_position(self, ts_ms: float) -> None:
        if self.cursor_time_label is None:
            return
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        axis = plot_item.getAxis('bottom')
        if view_box is None or axis is None:
            return
        scene_pos = view_box.mapViewToScene(QPointF(ts_ms, 0))
        axis_rect = axis.sceneBoundingRect()

        text_rect = self.cursor_time_label.boundingRect()
        padding_x = 4
        padding_y = 2
        pill_width = text_rect.width() + padding_x * 2
        pill_height = text_rect.height() + padding_y * 2

        x = scene_pos.x() - (pill_width / 2.0)
        min_x = axis_rect.left()
        max_x = axis_rect.right() - pill_width
        if x < min_x:
            x = min_x
        if x > max_x:
            x = max_x
        axis_height = axis.height()
        if axis_height <= 0:
            axis_height = 28
        y = axis_rect.bottom() - axis_height + 1

        self.cursor_time_label.setPos(x + padding_x, y + padding_y)

        if self.cursor_time_label_bg is None:
            self.cursor_time_label_bg = pg.QtWidgets.QGraphicsPathItem()
            self.cursor_time_label_bg.setZValue(self.cursor_time_label.zValue() - 1)
            plot_item.scene().addItem(self.cursor_time_label_bg)

        path = QPainterPath()
        radius = 4.0
        notch = 6.0
        left = x
        right = x + pill_width
        top = y
        bottom = y + pill_height
        mid_x = x + (pill_width / 2.0)
        path.moveTo(left + radius, top)
        path.lineTo(mid_x - 6, top)
        path.lineTo(mid_x, top - notch)
        path.lineTo(mid_x + 6, top)
        path.lineTo(right - radius, top)
        path.quadTo(right, top, right, top + radius)
        path.lineTo(right, bottom - radius)
        path.quadTo(right, bottom, right - radius, bottom)
        path.lineTo(left + radius, bottom)
        path.quadTo(left, bottom, left, bottom - radius)
        path.lineTo(left, top + radius)
        path.quadTo(left, top, left + radius, top)
        path.closeSubpath()

        bg_color = QColor('#2A2E39')
        bg_color.setAlpha(255)
        self.cursor_time_label_bg.setPath(path)
        self.cursor_time_label_bg.setBrush(bg_color)
        self.cursor_time_label_bg.setPen(pg.mkPen(bg_color))
        self.cursor_time_label_bg.show()

        if self.cursor_time_tick is None:
            self.cursor_time_tick = pg.QtWidgets.QGraphicsLineItem()
            self.cursor_time_tick.setZValue(50)
            plot_item.scene().addItem(self.cursor_time_tick)
        tick_pen = pg.mkPen(QColor(70, 75, 90, 120), width=1)
        self.cursor_time_tick.setPen(tick_pen)
        self.cursor_time_tick.setLine(QLineF(scene_pos.x(), axis_rect.top(), scene_pos.x(), axis_rect.top() + 6))
        self.cursor_time_tick.show()

    def _update_cursor_dot(self, x: float, y: float) -> None:
        if self.cursor_dot is None:
            return
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        if view_box is None:
            return
        try:
            scene_pos = view_box.mapViewToScene(QPointF(x, y))
        except Exception:
            return
        radius = 2.5
        self.cursor_dot.setRect(
            scene_pos.x() - radius,
            scene_pos.y() - radius,
            radius * 2,
            radius * 2,
        )
        dot_color = QColor(180, 190, 205, 220)
        self.cursor_dot.setPen(pg.mkPen(dot_color))
        self.cursor_dot.setBrush(pg.mkBrush(dot_color))
        self.cursor_dot.show()
