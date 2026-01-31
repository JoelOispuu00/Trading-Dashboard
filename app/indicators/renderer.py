from __future__ import annotations

import math
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
    if isinstance(values, np.ndarray):
        arr = np.asarray(values, dtype=np.float64)
    else:
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
        self._times_cache_key: Optional[Tuple[int, float, float]] = None
        self._times_cache: Optional[np.ndarray] = None
        self._series_cache: Dict[Tuple[str, str], Dict[str, np.ndarray]] = {}
        self._max_points = 1500

    def _downsample(self, times: np.ndarray, values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if times.size <= self._max_points:
            return times, values
        step = max(1, int(math.ceil(times.size / float(self._max_points))))
        return times[::step], values[::step]

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

    def render(self, bars: Any, output: Dict[str, Any], namespace: str = "") -> None:
        if not bars:
            return
        if isinstance(bars, tuple) and len(bars) == 2:
            bars_data, times_in = bars
            times = np.asarray(times_in, dtype=np.float64)
        else:
            bars_data = bars
            times = self._get_times(bars_data)
        length = len(times)
        ns = f"{namespace}:" if namespace else ""
        tail_len = int(output.get("_tail_len") or 0)

        for series in output.get("series", []):
            self._render_series(series, times, length, ns, tail_len)

        for band in output.get("bands", []):
            self._render_band(band, times, length, ns, tail_len)

        for hist in output.get("hist", []):
            self._render_hist(hist, times, length, ns, tail_len)

        for marker in output.get("markers", []):
            self._render_marker(marker, ns)

        for region in output.get("regions", []):
            self._render_region(region, times, length, ns)

        for level in output.get("levels", []):
            self._render_level(level, ns)

    def _get_times(self, bars: Any) -> np.ndarray:
        if isinstance(bars, np.ndarray):
            times = np.asarray(bars[:, 0], dtype=np.float64)
        else:
            times = np.asarray([float(b[0]) for b in bars], dtype=np.float64)
        if times.size == 0:
            return times
        key = (int(times.size), float(times[0]), float(times[-1]))
        if self._times_cache_key == key and self._times_cache is not None:
            return self._times_cache
        self._times_cache_key = key
        self._times_cache = times
        return times

    def _render_series(self, spec: Dict[str, Any], times: np.ndarray, length: int, ns: str, tail_len: int) -> None:
        series_id = ns + str(spec.get("id", "series"))
        kind = spec.get("type", "line")
        key = ("series", series_id)
        cache = self._series_cache.get(key)
        values = spec.get("values", [])
        if tail_len > 0 and cache is not None and cache.get("y") is not None and cache["y"].size == length:
            y_cache = cache["y"]
            try:
                tail_vals = np.asarray(values[-tail_len:], dtype=np.float64)
                if tail_vals.size:
                    y_cache[-tail_vals.size:] = tail_vals
                values_arr = y_cache
            except Exception:
                values_arr = _as_array(values, length)
                cache["y"] = values_arr
        else:
            values_arr = _as_array(values, length)
            if cache is None:
                cache = {}
                self._series_cache[key] = cache
            cache["y"] = values_arr
        cache["x"] = times
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
            ds_times, ds_values = self._downsample(times, values_arr)
            item.setData(ds_times, ds_values, pen=pen, brush=_color(spec.get("color", "#FFFFFF")))
            return

        item = self._items.get(key)
        if item is None:
            item = pg.PlotDataItem()
            self._items[key] = item
            self.plot_item.addItem(item)
        ds_times, ds_values = self._downsample(times, values_arr)
        item.setData(ds_times, ds_values, pen=pen, connect="finite")

    def _render_band(self, spec: Dict[str, Any], times: np.ndarray, length: int, ns: str, tail_len: int) -> None:
        band_id = ns + str(spec.get("id", "band"))
        cache = self._series_cache.get(("band", band_id))
        upper_vals = spec.get("upper", [])
        lower_vals = spec.get("lower", [])
        upper = _as_array(upper_vals, length)
        lower = _as_array(lower_vals, length)
        if tail_len > 0 and cache is not None and cache.get("upper") is not None and cache.get("lower") is not None:
            try:
                upper_tail = np.asarray(upper_vals[-tail_len:], dtype=np.float64)
                lower_tail = np.asarray(lower_vals[-tail_len:], dtype=np.float64)
                if upper_tail.size:
                    cache["upper"][-upper_tail.size:] = upper_tail
                if lower_tail.size:
                    cache["lower"][-lower_tail.size:] = lower_tail
                upper = cache["upper"]
                lower = cache["lower"]
            except Exception:
                pass
        else:
            if cache is None:
                cache = {}
                self._series_cache[("band", band_id)] = cache
            cache["upper"] = upper
            cache["lower"] = lower
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
        ds_times, ds_upper = self._downsample(times, upper)
        upper_item.setData(ds_times, ds_upper, pen=pg.mkPen(edge_color, width=edge_width), connect="finite")

        lower_item = self._items.get(lower_key)
        if lower_item is None:
            lower_item = pg.PlotDataItem()
            self._items[lower_key] = lower_item
            self.plot_item.addItem(lower_item)
        _, ds_lower = self._downsample(times, lower)
        lower_item.setData(ds_times, ds_lower, pen=pg.mkPen(edge_color, width=edge_width), connect="finite")

        if fill:
            fill_item = self._items.get(fill_key)
            if fill_item is None:
                fill_item = pg.FillBetweenItem(upper_item, lower_item, brush=_color(fill))
                self._items[fill_key] = fill_item
                self.plot_item.addItem(fill_item)
            else:
                fill_item.setBrush(_color(fill))

    def _render_hist(self, spec: Dict[str, Any], times: np.ndarray, length: int, ns: str, tail_len: int) -> None:
        hist_id = ns + str(spec.get("id", "hist"))
        key_pos = ("hist_pos", hist_id)
        key_neg = ("hist_neg", hist_id)
        cache = self._series_cache.get(("hist", hist_id))
        values = spec.get("values", [])
        if tail_len > 0 and cache is not None and cache.get("y") is not None and cache["y"].size == length:
            y_cache = cache["y"]
            try:
                tail_vals = np.asarray(values[-tail_len:], dtype=np.float64)
                if tail_vals.size:
                    y_cache[-tail_vals.size:] = tail_vals
                values_arr = y_cache
            except Exception:
                values_arr = _as_array(values, length)
                cache["y"] = values_arr
        else:
            values_arr = _as_array(values, length)
            if cache is None:
                cache = {}
                self._series_cache[("hist", hist_id)] = cache
            cache["y"] = values_arr
        base = float(spec.get("base", 0.0))
        color_up = _color(spec.get("color_up", "#00C853"))
        color_down = _color(spec.get("color_down", "#EF5350"))
        ds_times, ds_values = self._downsample(times, values_arr)
        if ds_times.size > 1:
            width = max(1.0, float(np.median(np.diff(ds_times))) * 0.8)
        else:
            width = 1.0

        pos_vals = np.where(ds_values >= base, ds_values, base)
        neg_vals = np.where(ds_values < base, ds_values, base)

        pos_item = self._items.get(key_pos)
        if pos_item is None:
            pos_item = pg.BarGraphItem(
                x=ds_times,
                height=pos_vals - base,
                y0=base,
                width=width,
                brush=color_up,
                pen=pg.mkPen(color_up),
            )
            self._items[key_pos] = pos_item
            self.plot_item.addItem(pos_item)
        else:
            pos_item.setOpts(x=ds_times, height=pos_vals - base, y0=base, width=width, brush=color_up, pen=pg.mkPen(color_up))

        neg_item = self._items.get(key_neg)
        if neg_item is None:
            neg_item = pg.BarGraphItem(
                x=ds_times,
                height=neg_vals - base,
                y0=base,
                width=width,
                brush=color_down,
                pen=pg.mkPen(color_down),
            )
            self._items[key_neg] = neg_item
            self.plot_item.addItem(neg_item)
        else:
            neg_item.setOpts(x=ds_times, height=neg_vals - base, y0=base, width=width, brush=color_down, pen=pg.mkPen(color_down))

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
