from datetime import datetime
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui import QFont, QPainter, QPicture, QColor, QPainterPath

from .performance import calculate_visible_range, calculate_lod_step, MAX_VISIBLE_BARS_DENSE
from .volume_histogram import update_volume_histogram


class CandlestickItem(pg.GraphicsObject):
    def __init__(
        self,
        data: List[Iterable[float]],
        up_color: QColor,
        down_color: QColor,
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
        self.generate_picture()

    def generate_picture(self) -> None:
        if self._is_painting:
            return
        self._is_painting = True
        try:
            self.picture = QPicture()
            painter = QPainter(self.picture)
            try:
                if len(self.data) == 0:
                    self._cached_bounds = QRectF(0, 0, 1, 1)
                    return
                w = 0.3
                try:
                    vb = self.getViewBox()
                except Exception:
                    vb = None
                start_idx, end_idx = calculate_visible_range(vb, len(self.data), margin=10)
                step = 1
                y_min = float('inf')
                y_max = float('-inf')
                x_min = float('inf')
                x_max = float('-inf')

                for idx in range(start_idx, end_idx, step):
                    candle = self.data[idx]
                    if len(candle) < 5:
                        continue
                    try:
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
                    y_min = min(y_min, low)
                    y_max = max(y_max, high)
                    x_min = min(x_min, idx - w)
                    x_max = max(x_max, idx + w)

                    is_bear = close < open_price
                    if len(self.bar_colors) > 0 and idx < len(self.bar_colors) and self.bar_colors[idx] is not None:
                        current_color = self.bar_colors[idx]
                    else:
                        current_color = self.down_color if is_bear else self.base_color

                    painter.setPen(pg.mkPen(current_color))
                    painter.setBrush(pg.mkBrush(current_color))
                    if high != low:
                        painter.drawLine(QPointF(idx, low), QPointF(idx, high))
                    body_top = max(open_price, close)
                    body_bottom = min(open_price, close)
                    body_height = body_top - body_bottom
                    if body_height > 0:
                        painter.drawRect(QRectF(idx - w, body_bottom, w * 2, body_height))
                    else:
                        painter.drawLine(QPointF(idx - w, close), QPointF(idx + w, close))

                if y_min != float('inf') and y_max != float('-inf'):
                    self._cached_bounds = QRectF(x_min, y_min, x_max - x_min, y_max - y_min)
                else:
                    self._cached_bounds = QRectF(self.picture.boundingRect())
            finally:
                painter.end()
        finally:
            self._is_painting = False

    def paint(self, painter: QPainter, option, widget) -> None:
        try:
            painter.drawPicture(0, 0, self.picture)
        except RuntimeError:
            pass

    def boundingRect(self) -> QRectF:
        if self._cached_bounds is not None and self._cached_bounds.isValid():
            return self._cached_bounds
        return QRectF(self.picture.boundingRect())

    def set_data(self, data: List[Iterable[float]], bar_colors: Optional[List[Optional[QColor]]] = None) -> None:
        if bar_colors is not None:
            self.bar_colors = bar_colors
        self.data = data
        self.generate_picture()
        try:
            self.informViewBoundsChanged()
        except RuntimeError:
            pass
        try:
            self.update()
        except RuntimeError:
            pass


class CandlestickChart:
    def __init__(self, plot_widget: pg.PlotWidget, up_color: str, down_color: str) -> None:
        self.plot_widget = plot_widget
        self.base_color = QColor(up_color)
        self.down_color = QColor(down_color)
        self.candles: List[List[float]] = []
        self._day_gridlines = []
        self.bar_colors: List[Optional[QColor]] = []
        self.volume_item: Optional[pg.BarGraphItem] = None
        self.volume_max: float = 0.0
        self.price_line: Optional[pg.InfiniteLine] = None
        self.price_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None
        self.price_label_bg: Optional[pg.QtWidgets.QGraphicsPathItem] = None
        self.timeframe_ms: Optional[int] = None
        self.last_kline_ts_ms: Optional[int] = None
        self.last_close_ms: Optional[int] = None
        self.last_event_ms: Optional[int] = None
        self.time_offset_ms: int = 0
        self._countdown_timer = QTimer()
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._refresh_countdown)
        self.hover_label: Optional[pg.QtWidgets.QGraphicsTextItem] = None

        self.plot_widget.setClipToView(True)
        try:
            view_box = self.plot_widget.getViewBox()
            if view_box:
                view_box.enableAutoRange('x', False)
                view_box.enableAutoRange('y', False)
                view_box.sigRangeChanged.connect(self._on_view_changed)
                self.plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        except Exception:
            pass

        self.item = CandlestickItem([], self.base_color, self.down_color)
        self.plot_widget.addItem(self.item)

        self._setup_price_axis()
        self._setup_date_index_axis()
        self._countdown_timer.start()

    def _setup_price_axis(self) -> None:
        class PriceAxis(pg.AxisItem):
            def tickStrings(self, values, scale, spacing):
                out = []
                for v in values:
                    try:
                        if not np.isfinite(v) or v <= 0:
                            out.append('')
                            continue
                        if v >= 1000:
                            out.append(f'{v:,.0f}')
                        elif v >= 100:
                            out.append(f'{v:,.1f}')
                        elif v >= 10:
                            out.append(f'{v:,.2f}')
                        elif v >= 1:
                            out.append(f'{v:,.3f}')
                        elif v >= 0.01:
                            out.append(f'{v:.4f}')
                        elif v >= 0.0001:
                            out.append(f'{v:.6f}')
                        elif v >= 0.000001:
                            out.append(f'{v:.8f}')
                        else:
                            out.append(f'{v:.10f}'.rstrip('0').rstrip('.'))
                    except Exception:
                        out.append('')
                return out

        try:
            price_axis = PriceAxis(orientation='right')
            font = QFont()
            font.setPointSize(8)
            price_axis.setTickFont(font)
            self.plot_widget.setAxisItems({'right': price_axis})
            self.plot_widget.showAxis('right')
            self.plot_widget.hideAxis('left')
            price_axis.setWidth(70)
        except Exception:
            pass

    def _setup_date_index_axis(self) -> None:
        class DateIndexAxis(pg.AxisItem):
            def __init__(self, parent_chart, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.parent_chart = parent_chart
                self._day_rollover_indices = []
                self._update_day_rollovers()

            def _update_day_rollovers(self):
                self._day_rollover_indices = []
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
                            self._day_rollover_indices.append(idx)
                        last_day = current_day
                    except Exception:
                        continue

            def tickValues(self, minVal, maxVal, size):
                if not self._day_rollover_indices:
                    self._update_day_rollovers()
                visible_indices = [idx for idx in self._day_rollover_indices if minVal <= idx <= maxVal]
                if not visible_indices:
                    return []
                spacing = max(1, int((maxVal - minVal) / 10))
                step = max(1, len(visible_indices) // 10)
                selected = visible_indices[::max(1, step)]
                return [(1, selected)]

            def tickStrings(self, values, scale, spacing):
                out = []
                candles = self.parent_chart.candles
                for v in values:
                    try:
                        idx = int(round(v))
                    except Exception:
                        out.append('')
                        continue
                    if idx < 0 or idx >= len(candles):
                        out.append('')
                        continue
                    ts_ms = candles[idx][0]
                    try:
                        dt = datetime.fromtimestamp(ts_ms / 1000.0)
                        date_str = dt.strftime('%Y-%m-%d')
                    except Exception:
                        out.append('')
                        continue
                    out.append(date_str)
                return out

        bottom_axis = DateIndexAxis(self, orientation='bottom')
        font = QFont()
        font.setPointSize(8)
        bottom_axis.setTickFont(font)
        bottom_axis.setStyle(autoExpandTextSpace=False, tickTextOffset=2)
        bottom_axis.setHeight(38)
        self.plot_widget.setAxisItems({'bottom': bottom_axis})
        left_axis = self.plot_widget.getAxis('left')
        if left_axis:
            left_axis.setTickFont(font)
        self._date_index_axis = bottom_axis
        self._update_day_gridlines()

    def _update_day_gridlines(self) -> None:
        for line in self._day_gridlines:
            try:
                self.plot_widget.removeItem(line)
            except Exception:
                pass
        self._day_gridlines = []
        if not self.candles:
            if hasattr(self, '_date_index_axis') and self._date_index_axis:
                self._date_index_axis._update_day_rollovers()
            return
        last_day = None
        gridline_color = QColor(self.base_color)
        gridline_color.setAlpha(20)
        pen = pg.mkPen(gridline_color, width=1)
        for idx, candle in enumerate(self.candles):
            try:
                ts_ms = float(candle[0])
                dt = datetime.fromtimestamp(ts_ms / 1000.0)
                current_day = dt.date()
                if last_day is not None and current_day != last_day:
                    gridline = pg.InfiniteLine(pos=float(idx), angle=90, pen=pen)
                    self.plot_widget.addItem(gridline)
                    self._day_gridlines.append(gridline)
                last_day = current_day
            except Exception:
                continue
        if hasattr(self, '_date_index_axis') and self._date_index_axis:
            self._date_index_axis._update_day_rollovers()

    def _on_view_changed(self) -> None:
        try:
            self.item.generate_picture()
            self.item.update()
            if self.volume_item and self.candles:
                self._update_volume_histogram(self.candles)
            self._update_price_line()
        except Exception:
            pass

    def _update_volume_histogram(self, candles: List[Iterable[float]]) -> None:
        def extract_volume(candle: Iterable[float], idx: int) -> float:
            if len(candle) > 5:
                return float(candle[5]) if candle[5] is not None else 0.0
            return 0.0

        def extract_x(candle: Iterable[float], idx: int) -> float:
            return float(idx)

        volume_color = QColor('#22C55E')
        self.volume_item, self.volume_max = update_volume_histogram(
            plot_widget=self.plot_widget,
            volume_item=self.volume_item,
            base_color=volume_color,
            data=candles,
            extract_volume=extract_volume,
            extract_x=extract_x,
            volume_height_ratio=0.15,
            bar_width=0.8,
            flush_bottom=True,
        )

    def set_historical_data(self, data: List[Iterable[float]]) -> None:
        normalized = []
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
            normalized.append([ts, o, h, l, cl, vol])
        if not normalized:
            return
        self.candles = normalized
        self.item.set_data(self.candles, bar_colors=self.bar_colors)
        self._update_volume_histogram(self.candles)
        self._update_price_line()
        self._update_day_gridlines()
        self._auto_range()

    def set_timeframe(self, timeframe: str) -> None:
        self.timeframe_ms = self._parse_timeframe_ms(timeframe)

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

        self.last_kline_ts_ms = ts_ms
        self.last_close_ms = int(kline.get('close_ms', 0)) or None
        self.last_event_ms = int(kline.get('event_ms', 0)) or None
        try:
            self.time_offset_ms = int(kline.get('time_offset_ms', 0))
        except Exception:
            self.time_offset_ms = 0
        self.item.set_data(self.candles, bar_colors=self.bar_colors)
        self._update_volume_histogram(self.candles)
        self._update_price_line()
        self._update_day_gridlines()

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

        last = self.candles[-1]
        if len(last) < 6:
            return
        last_ts = int(last[0])
        if ts_ms < last_ts or ts_ms >= last_ts + self.timeframe_ms:
            return

        o, h, l, _, v = float(last[1]), float(last[2]), float(last[3]), float(last[4]), float(last[5])
        h = max(h, price)
        l = min(l, price)
        v = v + max(0.0, qty)
        self.candles[-1] = [last_ts, o, h, l, price, v]

        self.item.set_data(self.candles, bar_colors=self.bar_colors)
        self._update_volume_histogram(self.candles)
        self._update_price_line()

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
        self.plot_widget.setXRange(max(0, n - show_candles - 5), n + 5)

    def set_bar_colors(self, bar_colors: List[Optional[QColor]]) -> None:
        self.bar_colors = bar_colors
        try:
            self.item.set_data(self.candles, bar_colors=self.bar_colors)
        except RuntimeError:
            pass

    def _update_price_line(self) -> None:
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
            self.price_label.setZValue(51)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.price_label)
        self.price_label.setPlainText(label_text)

        try:
            self._update_price_label_position(close_price, color)
        except Exception:
            pass

    def _format_price_label(self, price: float) -> str:
        remaining = ''
        if self.last_close_ms is not None:
            close_ms = self.last_close_ms
            if self.last_event_ms:
                now_ms = self.last_event_ms
            else:
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
        price_text = f'{price:.6f}'.rstrip('0').rstrip('.')
        if remaining:
            return f'{price_text}\n{remaining.strip()}'
        return f'{price_text}'

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
        padding_x = 6
        padding_y = 4
        pill_width = text_rect.width() + padding_x * 2
        pill_height = text_rect.height() + padding_y * 2

        if axis_rect is not None:
            x = axis_rect.left() + 2
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
        path.addRoundedRect(QRectF(x, y, pill_width, pill_height), radius, radius)
        bg_color = QColor(color)
        bg_color.setAlpha(230)
        self.price_label_bg.setPath(path)
        self.price_label_bg.setBrush(bg_color)
        self.price_label_bg.setPen(pg.mkPen(bg_color))

    def _ensure_hover_label(self) -> None:
        if self.hover_label is None:
            self.hover_label = pg.QtWidgets.QGraphicsTextItem()
            self.hover_label.setDefaultTextColor(QColor('#B2B5BE'))
            self.hover_label.setZValue(60)
            plot_item = self.plot_widget.getPlotItem()
            plot_item.scene().addItem(self.hover_label)

    def _on_mouse_moved(self, scene_pos) -> None:
        if not self.candles:
            return
        plot_item = self.plot_widget.getPlotItem()
        view_box = plot_item.getViewBox()
        if not view_box:
            return
        try:
            view_pos = view_box.mapSceneToView(scene_pos)
        except Exception:
            return
        idx = int(round(view_pos.x()))
        if idx < 0 or idx >= len(self.candles):
            return
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
            return f'{val:.6f}'.rstrip('0').rstrip('.')

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
        self.hover_label.setPlainText(text)

        scene_rect = plot_item.sceneBoundingRect()
        self.hover_label.setPos(scene_rect.left() + 8, scene_rect.top() + 6)
