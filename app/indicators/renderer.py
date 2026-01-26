from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6.QtGui import QColor


def _color(value: str | QColor) -> QColor:
    if isinstance(value, QColor):
        return value
    try:
        return QColor(value)
    except Exception:
        return QColor("#FFFFFF")


def _as_array(values: Iterable[float], length: int) -> np.ndarray:
    arr = np.asarray(list(values), dtype=np.float64)
    if arr.size < length:
        pad = np.full(length - arr.size, np.nan, dtype=np.float64)
        arr = np.concatenate((pad, arr))
    elif arr.size > length:
        arr = arr[-length:]
    return arr


class IndicatorRenderer:
    def __init__(self, plot_item: pg.PlotItem) -> None:
        self.plot_item = plot_item
        self._items: Dict[Tuple[str, str], Any] = {}

    def clear(self) -> None:
        for item in list(self._items.values()):
            try:
                self.plot_item.removeItem(item)
            except Exception:
                pass
        self._items.clear()

    def clear_namespace(self, namespace: str) -> None:
        if not namespace:
            return
        prefix = f"{namespace}:"
        to_remove = [key for key in self._items.keys() if key[1].startswith(prefix)]
        for key in to_remove:
            item = self._items.pop(key, None)
            if item is None:
                continue
            try:
                self.plot_item.removeItem(item)
            except Exception:
                pass

    def render(self, bars: List[Iterable[float]], output: Dict[str, Any], namespace: str = "") -> None:
        if not bars:
            return
        times = np.asarray([float(b[0]) for b in bars], dtype=np.float64)
        length = len(times)
        ns = f"{namespace}:" if namespace else ""

        for series in output.get("series", []):
            self._render_series(series, times, length, ns)

        for band in output.get("bands", []):
            self._render_band(band, times, length, ns)

        for hist in output.get("hist", []):
            self._render_hist(hist, times, length, ns)

        for marker in output.get("markers", []):
            self._render_marker(marker, ns)

        for region in output.get("regions", []):
            self._render_region(region, times, length, ns)

        for level in output.get("levels", []):
            self._render_level(level, ns)

    def _render_series(self, spec: Dict[str, Any], times: np.ndarray, length: int, ns: str) -> None:
        series_id = ns + str(spec.get("id", "series"))
        kind = spec.get("type", "line")
        key = ("series", series_id)
        values = _as_array(spec.get("values", []), length)
        color = _color(spec.get("color", "#FFFFFF"))
        width = int(spec.get("width", 1))
        style = spec.get("style", "solid")
        pen = pg.mkPen(color, width=width, style=_pen_style(style))

        if kind == "scatter":
            item = self._items.get(key)
            if item is None:
                item = pg.ScatterPlotItem()
                self._items[key] = item
                self.plot_item.addItem(item)
            item.setData(times, values, pen=pen, brush=_color(spec.get("color", "#FFFFFF")))
            return

        item = self._items.get(key)
        if item is None:
            item = pg.PlotDataItem()
            self._items[key] = item
            self.plot_item.addItem(item)
        item.setData(times, values, pen=pen, connect="finite")

    def _render_band(self, spec: Dict[str, Any], times: np.ndarray, length: int, ns: str) -> None:
        band_id = ns + str(spec.get("id", "band"))
        upper = _as_array(spec.get("upper", []), length)
        lower = _as_array(spec.get("lower", []), length)
        edge_color = _color(spec.get("edge_color", "#8A8F9B"))
        edge_width = int(spec.get("edge_width", 1))
        fill = spec.get("fill")

        upper_key = ("band_upper", band_id)
        lower_key = ("band_lower", band_id)
        fill_key = ("band_fill", band_id)

        upper_item = self._items.get(upper_key)
        if upper_item is None:
            upper_item = pg.PlotDataItem()
            self._items[upper_key] = upper_item
            self.plot_item.addItem(upper_item)
        upper_item.setData(times, upper, pen=pg.mkPen(edge_color, width=edge_width), connect="finite")

        lower_item = self._items.get(lower_key)
        if lower_item is None:
            lower_item = pg.PlotDataItem()
            self._items[lower_key] = lower_item
            self.plot_item.addItem(lower_item)
        lower_item.setData(times, lower, pen=pg.mkPen(edge_color, width=edge_width), connect="finite")

        if fill:
            fill_item = self._items.get(fill_key)
            if fill_item is None:
                fill_item = pg.FillBetweenItem(upper_item, lower_item, brush=_color(fill))
                self._items[fill_key] = fill_item
                self.plot_item.addItem(fill_item)
            else:
                fill_item.setBrush(_color(fill))

    def _render_hist(self, spec: Dict[str, Any], times: np.ndarray, length: int, ns: str) -> None:
        hist_id = ns + str(spec.get("id", "hist"))
        key_pos = ("hist_pos", hist_id)
        key_neg = ("hist_neg", hist_id)
        values = _as_array(spec.get("values", []), length)
        base = float(spec.get("base", 0.0))
        color_up = _color(spec.get("color_up", "#00C853"))
        color_down = _color(spec.get("color_down", "#EF5350"))
        if length > 1:
            width = max(1.0, float(np.median(np.diff(times))) * 0.8)
        else:
            width = 1.0

        pos_vals = np.where(values >= base, values, base)
        neg_vals = np.where(values < base, values, base)

        pos_item = self._items.get(key_pos)
        if pos_item is None:
            pos_item = pg.BarGraphItem(
                x=times,
                height=pos_vals - base,
                y0=base,
                width=width,
                brush=color_up,
                pen=pg.mkPen(color_up),
            )
            self._items[key_pos] = pos_item
            self.plot_item.addItem(pos_item)
        else:
            pos_item.setOpts(x=times, height=pos_vals - base, y0=base, width=width, brush=color_up, pen=pg.mkPen(color_up))

        neg_item = self._items.get(key_neg)
        if neg_item is None:
            neg_item = pg.BarGraphItem(
                x=times,
                height=neg_vals - base,
                y0=base,
                width=width,
                brush=color_down,
                pen=pg.mkPen(color_down),
            )
            self._items[key_neg] = neg_item
            self.plot_item.addItem(neg_item)
        else:
            neg_item.setOpts(x=times, height=neg_vals - base, y0=base, width=width, brush=color_down, pen=pg.mkPen(color_down))

    def _render_marker(self, spec: Dict[str, Any], ns: str) -> None:
        marker_id = ns + str(spec.get("id", spec.get("shape", "marker")))
        key = ("marker", marker_id)
        item = self._items.get(key)
        if item is None:
            item = pg.ScatterPlotItem()
            self._items[key] = item
            self.plot_item.addItem(item)
        times = spec.get("time")
        prices = spec.get("price", spec.get("value"))
        if times is None:
            return
        if not isinstance(times, (list, tuple, np.ndarray)):
            times = [times]
        if not isinstance(prices, (list, tuple, np.ndarray)):
            prices = [prices] * len(times)
        color = _color(spec.get("color", "#FFFFFF"))
        size = int(spec.get("size", 6))
        points = []
        for x_val, y_val in zip(times, prices):
            try:
                x = float(x_val)
                y = float(y_val)
            except Exception:
                continue
            points.append({"pos": (x, y), "brush": color, "pen": color, "size": size})
        if points:
            item.setData(points)

    def _render_region(self, spec: Dict[str, Any], times: np.ndarray, length: int, ns: str) -> None:
        region_id = ns + str(spec.get("id", "region"))
        key = ("region", region_id)
        item = self._items.get(key)
        if item is None:
            item = pg.LinearRegionItem(orientation="vertical")
            self._items[key] = item
            self.plot_item.addItem(item)
        try:
            start_ts = float(spec.get("start_ts"))
            end_ts = float(spec.get("end_ts"))
        except Exception:
            return
        color = _color(spec.get("color", "#FFFFFF"))
        item.setRegion((start_ts, end_ts))
        item.setBrush(color)
        item.setZValue(-10)

    def _render_level(self, spec: Dict[str, Any], ns: str) -> None:
        level_id = ns + str(spec.get("id", spec.get("value", "level")))
        key = ("level", level_id)
        try:
            value = float(spec.get("value"))
        except Exception:
            return
        color = _color(spec.get("color", "#8A8F9B"))
        width = int(spec.get("width", 1))
        style = spec.get("style", "solid")
        pen = pg.mkPen(color, width=width, style=_pen_style(style))
        item = self._items.get(key)
        if item is None:
            item = pg.InfiniteLine(pos=value, angle=0, pen=pen)
            self._items[key] = item
            self.plot_item.addItem(item)
        else:
            item.setValue(value)
            item.setPen(pen)


def _pen_style(style: str):
    if style == "dash":
        return pg.QtCore.Qt.PenStyle.DashLine
    if style == "dot":
        return pg.QtCore.Qt.PenStyle.DotLine
    return pg.QtCore.Qt.PenStyle.SolidLine
