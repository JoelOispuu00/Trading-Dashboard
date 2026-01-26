import json
import os
import time
import uuid
from datetime import datetime
from typing import Optional, List, Dict
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel, QCompleter, QButtonGroup, QTabBar, QStyle, QLineEdit, QMenu
from PyQt6.QtGui import QFont, QColor, QLinearGradient, QBrush, QIcon
from PyQt6.QtCore import QThread, pyqtSignal, QSortFilterProxyModel, Qt, QTimer, QSettings, QSize

from core.data_store import DataStore
from core.data_fetch import load_recent_bars, load_symbols, load_more_history, load_cached_bars, load_cached_full, load_window_bars, timeframe_to_ms, ensure_history_floor
from core.indicator_registry import discover_indicators, IndicatorInfo
from core.hot_reload import start_watcher, IndicatorHotReloadWorker
from .theme import theme
from .charts.candlestick_chart import CandlestickChart
from indicators.runtime import run_compute
from indicators.renderer import IndicatorRenderer


class TimeScaleViewBox(pg.ViewBox):
    def wheelEvent(self, ev) -> None:
        if ev is None:
            return
        try:
            delta = ev.angleDelta().y()
        except Exception:
            delta = ev.delta() if hasattr(ev, "delta") else 0
        if delta == 0:
            return
        scale = 1.06 ** (delta / 120.0)
        self.scaleBy((1.0 / scale, 1.0))
        ev.accept()


class DataFetchWorker(QThread):
    data_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(
        self,
        mode: str,
        store: DataStore,
        exchange: str,
        symbol: str,
        timeframe: str,
        bar_count: int,
        current_min_ts: Optional[int] = None,
        current_max_ts: Optional[int] = None,
        window_start_ms: Optional[int] = None,
        window_end_ms: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.mode = mode
        self.store = store
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe
        self.bar_count = bar_count
        self.current_min_ts = current_min_ts
        self.current_max_ts = current_max_ts
        self.window_start_ms = window_start_ms
        self.window_end_ms = window_end_ms

    def run(self) -> None:
        try:
            if self.mode == 'load':
                bars = load_recent_bars(self.store, self.exchange, self.symbol, self.timeframe, self.bar_count)
            elif self.mode == 'load_cached':
                bars = load_cached_bars(self.store, self.exchange, self.symbol, self.timeframe, self.bar_count)
            elif self.mode == 'load_cached_full':
                bars = load_cached_full(self.store, self.exchange, self.symbol, self.timeframe)
            elif self.mode == 'backfill':
                bars = load_more_history(
                    self.store,
                    self.exchange,
                    self.symbol,
                    self.timeframe,
                    self.bar_count,
                    self.current_min_ts,
                    self.current_max_ts,
                )
            elif self.mode == 'window':
                if self.window_start_ms is None or self.window_end_ms is None:
                    raise ValueError('Missing window range for window load')
                bars = load_window_bars(
                    self.store,
                    self.exchange,
                    self.symbol,
                    self.timeframe,
                    int(self.window_start_ms),
                    int(self.window_end_ms),
                )
            else:
                raise ValueError(f'Unknown fetch mode: {self.mode}')
            self.data_ready.emit(bars)
        except Exception as exc:
            self.error.emit(str(exc))


class SymbolFetchWorker(QThread):
    data_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, store: DataStore, exchange: str) -> None:
        super().__init__()
        self.store = store
        self.exchange = exchange

    def run(self) -> None:
        try:
            symbols = load_symbols(self.store, self.exchange)
            self.data_ready.emit(symbols)
        except Exception as exc:
            self.error.emit(str(exc))


class HistoryProbeWorker(QThread):
    result = pyqtSignal(str, str, object)
    error = pyqtSignal(str)

    def __init__(self, store: DataStore, exchange: str, symbol: str, timeframe: str) -> None:
        super().__init__()
        self.store = store
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe

    def run(self) -> None:
        try:
            earliest = ensure_history_floor(self.store, self.exchange, self.symbol, self.timeframe)
            self.result.emit(self.symbol, self.timeframe, earliest)
        except Exception as exc:
            self.error.emit(str(exc))


class LiveKlineWorker(QThread):
    kline = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, symbol: str, timeframe: str) -> None:
        super().__init__()
        self.symbol = symbol
        self.timeframe = timeframe
        self._stop = False
        self._ws = None
        self._time_offset_ms = 0
        self._last_sync_ms = 0

    def stop(self) -> None:
        self._stop = True
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def run(self) -> None:
        try:
            import websocket
            import json
        except Exception as exc:
            self.error.emit(f'WebSocket dependency missing: {exc}')
            return

        stream = f"{self.symbol.lower()}@kline_{self.timeframe}"
        url = f"wss://stream.binance.com:9443/ws/{stream}"

        def sync_time_offset():
            try:
                import requests
                resp = requests.get('https://api.binance.com/api/v3/time', timeout=10)
                resp.raise_for_status()
                server_ms = int(resp.json().get('serverTime', 0))
                local_ms = int(time.time() * 1000)
                self._time_offset_ms = server_ms - local_ms
                self._last_sync_ms = local_ms
            except Exception:
                self._time_offset_ms = 0

        sync_time_offset()

        def on_message(ws, message):
            if self._stop:
                return
            try:
                local_ms = int(time.time() * 1000)
                if local_ms - self._last_sync_ms > 300_000:
                    sync_time_offset()
                payload = json.loads(message)
                k = payload.get('k', {})
                kline = {
                    'ts_ms': int(k.get('t', 0)),
                    'close_ms': int(k.get('T', 0)),
                    'event_ms': int(payload.get('E', 0)),
                    'open': float(k.get('o', 0)),
                    'high': float(k.get('h', 0)),
                    'low': float(k.get('l', 0)),
                    'close': float(k.get('c', 0)),
                    'volume': float(k.get('v', 0)),
                    'closed': bool(k.get('x', False)),
                    'time_offset_ms': self._time_offset_ms,
                }
                self.kline.emit(kline)
            except Exception as exc:
                self.error.emit(str(exc))

        def on_error(ws, err):
            if not self._stop:
                self.error.emit(str(err))

        def on_close(ws, code, msg):
            _ = (code, msg)

        self._ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close)
        while not self._stop:
            self._ws.run_forever(ping_interval=20, ping_timeout=10)


class LiveTradeWorker(QThread):
    trade = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, symbol: str) -> None:
        super().__init__()
        self.symbol = symbol
        self._stop = False
        self._ws = None

    def stop(self) -> None:
        self._stop = True
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def run(self) -> None:
        try:
            import websocket
            import json
        except Exception as exc:
            self.error.emit(f'WebSocket dependency missing: {exc}')
            return

        stream = f"{self.symbol.lower()}@aggTrade"
        url = f"wss://stream.binance.com:9443/ws/{stream}"

        def on_message(ws, message):
            if self._stop:
                return
            try:
                payload = json.loads(message)
                trade = {
                    'ts_ms': int(payload.get('T', 0)),
                    'price': float(payload.get('p', 0)),
                    'qty': float(payload.get('q', 0)),
                }
                self.trade.emit(trade)
            except Exception as exc:
                self.error.emit(str(exc))

        def on_error(ws, err):
            if not self._stop:
                self.error.emit(str(err))

        self._ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error)
        while not self._stop:
            self._ws.run_forever(ping_interval=20, ping_timeout=10)


class ChartView(QWidget):
    def __init__(self, error_sink=None, debug_sink=None, indicator_panel=None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.toolbar = QWidget()
        self.toolbar.setObjectName('TopToolbar')
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(6, 6, 6, 4)
        toolbar_layout.setSpacing(6)

        self.symbol_box = QComboBox()
        self.symbol_box.setEditable(True)
        self.symbol_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.symbol_box.setMaxVisibleItems(20)
        self.symbol_box.setMinimumWidth(240)
        self.symbol_box.setMinimumHeight(22)
        toolbar_layout.addWidget(self.symbol_box)

        self.timeframe_buttons: dict[str, QPushButton] = {}
        self.timeframe_group = QButtonGroup(self)
        self.timeframe_group.setExclusive(True)
        self.current_timeframe = '1m'
        for tf in ['1m', '5m', '15m', '1h', '4h', '1d', '1w', '1M']:
            button = QPushButton(tf)
            button.setCheckable(True)
            button.setMinimumHeight(22)
            button.clicked.connect(lambda _checked, val=tf: self._set_timeframe(val))
            self.timeframe_buttons[tf] = button
            self.timeframe_group.addButton(button)
            toolbar_layout.addWidget(button)
        self.timeframe_buttons[self.current_timeframe].setChecked(True)

        self.load_button = QPushButton('Reset Cache')
        self.load_button.setToolTip('Reset Cache')
        try:
            icon_path = os.path.join(os.path.dirname(__file__), 'theme', 'icon_refresh.svg')
            icon = QIcon(icon_path)
            if icon.isNull():
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
            self.load_button.setIcon(icon)
            self.load_button.setIconSize(QSize(14, 14))
            self.load_button.setText('')
            self.load_button.setFixedSize(28, 28)
            self.load_button.setStyleSheet('padding: 0px;')
        except Exception:
            pass
        toolbar_layout.addWidget(self.load_button)

        self.status_label = QLabel('')
        toolbar_layout.addWidget(self.status_label)
        toolbar_layout.addStretch(1)

        layout.addWidget(self.toolbar)

        self.tab_bar = QTabBar()
        self.tab_bar.setObjectName('SymbolTabs')
        self.tab_bar.setExpanding(False)
        self.tab_bar.setMovable(True)
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tab_bar.tabMoved.connect(self._on_tab_moved)
        self.tab_bar.customContextMenuRequested.connect(self._on_tab_context_menu)
        layout.addWidget(self.tab_bar)

        self._chart_container = QWidget()
        self._chart_layout = QVBoxLayout(self._chart_container)
        self._chart_layout.setContentsMargins(0, 0, 0, 0)
        self._chart_layout.setSpacing(0)

        view_box = TimeScaleViewBox()
        self.plot_widget = pg.PlotWidget(viewBox=view_box)
        gradient = QLinearGradient(0, 0, 0, 1)
        gradient.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
        gradient.setColorAt(0.0, QColor('#141A26'))
        gradient.setColorAt(1.0, QColor('#101520'))
        self.plot_widget.setBackground(QBrush(gradient))
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
        self.plot_widget.setClipToView(True)
        self.plot_widget.setStyleSheet("border: 0px;")
        try:
            plot_item = self.plot_widget.getPlotItem()
            plot_item.setBorder(None)
            plot_item.getViewBox().setBorder(None)
        except Exception:
            pass
        try:
            self.plot_widget.getPlotItem().ctrl.gridAlphaSlider.setValue(20)
        except Exception:
            pass

        self._apply_axis_style()
        self._ensure_grid_visible()

        self._chart_layout.addWidget(self.plot_widget)
        layout.addWidget(self._chart_container)
        self.plot_widget.getViewBox().sigRangeChanged.connect(self._on_view_range_changed)

        self.load_button.clicked.connect(self._on_load_clicked)
        self.error_sink = error_sink
        self.debug_sink = debug_sink
        self.indicator_panel = indicator_panel

        self.candles = CandlestickChart(self.plot_widget, theme.UP, theme.DOWN)
        self._setup_data_store()
        self._setup_indicator_system()
        self._load_symbols()
        self._debug_last_update = 0.0
        self._tab_syncing = False
        self._skip_next_plus = False
        self._settings = QSettings('TradingDashboard', 'TradingDashboard')
        self.symbol_box.currentIndexChanged.connect(self._on_symbol_changed)
        self._add_symbol_search_icon()

    def _add_symbol_search_icon(self) -> None:
        line_edit = self.symbol_box.lineEdit()
        if line_edit is None:
            return
        try:
            icon_path = os.path.join(os.path.dirname(__file__), 'theme', 'icon_search.svg')
            icon = QIcon(icon_path)
            if icon.isNull():
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
            action = line_edit.addAction(icon, QLineEdit.ActionPosition.LeadingPosition)
            action.setIcon(icon)
            line_edit.setTextMargins(0, 0, 4, 0)
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_grid_visible()

    def _ensure_grid_visible(self) -> None:
        try:
            self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
            axis_bottom = self.plot_widget.getAxis('bottom')
            axis_right = self.plot_widget.getAxis('right')
            if axis_bottom:
                axis_bottom.setGrid(0.12)
            if axis_right:
                axis_right.setGrid(0.28)
        except Exception:
            pass

    def _apply_axis_style(self) -> None:
        axis_pen = pg.mkPen(theme.GRID)
        text_pen = pg.mkPen(theme.TEXT)
        font = QFont()
        font.setPointSize(8)

        for axis_name in ('left', 'bottom', 'right'):
            axis = self.plot_widget.getAxis(axis_name)
            axis.setPen(axis_pen)
            try:
                axis.setTickPen(axis_pen)
                axis.setStyle(tickPen=axis_pen)
            except Exception:
                pass
            axis.setTextPen(text_pen)
            axis.setTickFont(font)

    def _setup_data_store(self) -> None:
        db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'ohlcv.sqlite')
        db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.store = DataStore(db_path)
        self.exchange = 'binance'
        self._worker: Optional[DataFetchWorker] = None
        self._symbol_worker: Optional[SymbolFetchWorker] = None
        self._history_probe_worker: Optional[HistoryProbeWorker] = None
        self._history_probe_inflight: set[tuple[str, str]] = set()
        self._history_probe_queue: List[tuple[str, str]] = []
        self._kline_worker: Optional[LiveKlineWorker] = None
        self._trade_worker: Optional[LiveTradeWorker] = None
        self._symbol_filter = None
        self._auto_backfill_last = 0.0
        self._last_fetch_mode = 'load'
        self._backfill_pending = False
        self._backfill_timer = QTimer(self)
        self._backfill_timer.setSingleShot(True)
        self._backfill_timer.timeout.connect(self._trigger_window_load)
        self._backfill_debounce_timer = QTimer(self)
        self._backfill_debounce_timer.setSingleShot(True)
        self._backfill_debounce_timer.timeout.connect(self._evaluate_backfill)
        self._pending_backfill_view: Optional[tuple[float, float]] = None
        self._window_bars = 2000
        self._window_buffer_bars = 500
        self._window_start_ms: Optional[int] = None
        self._window_end_ms: Optional[int] = None
        self._ignore_view_range = False
        self._max_visible_bars = 5000
        self._clamp_in_progress = False
        self._fetch_start_ms: Optional[int] = None
        self._last_fetch_duration_ms: Optional[int] = None
        self._stale_cache_bars_threshold = 10
        self._initial_load_pending = False
        self._pending_kline: Optional[dict] = None
        self._pending_trade: Optional[dict] = None
        self._indicator_paths = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "indicators", "builtins")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "indicators", "custom")),
        ]
        self._indicator_defs: Dict[str, IndicatorInfo] = {}
        self._indicator_instances: List[Dict[str, object]] = []
        self._indicator_renderers: Dict[str, IndicatorRenderer] = {
            "price": IndicatorRenderer(self.plot_widget.getPlotItem())
        }
        self._indicator_panes: Dict[str, pg.PlotWidget] = {"price": self.plot_widget}
        self._indicator_hot_reload: Optional[IndicatorHotReloadWorker] = None
        self._indicator_recompute_pending = False
        self._indicator_next_pane_index = 1

    def _setup_indicator_system(self) -> None:
        self._load_indicator_definitions()
        self._load_indicator_instances()
        self._wire_indicator_panel()
        self._start_indicator_hot_reload()
        self._recompute_indicators()

    def _load_indicator_definitions(self) -> None:
        indicators = discover_indicators(self._indicator_paths)
        self._indicator_defs = {info.indicator_id: info for info in indicators}
        self._update_indicator_panel()

    def _start_indicator_hot_reload(self) -> None:
        if self._indicator_hot_reload is not None:
            return
        self._indicator_hot_reload = start_watcher(
            self._indicator_paths,
            self._on_indicators_updated,
            self._on_indicator_error,
            poll_interval=1.0,
        )

    def _on_indicators_updated(self, indicators: List[IndicatorInfo]) -> None:
        self._indicator_defs = {info.indicator_id: info for info in indicators}
        for instance in self._indicator_instances:
            indicator_id = instance.get("indicator_id")
            info = self._indicator_defs.get(indicator_id)
            if info:
                instance["info"] = info
                instance["schema"] = self._build_schema(info)
        self._update_indicator_panel()
        self._recompute_indicators()

    def _on_indicator_error(self, message: str) -> None:
        self._report_error(f'Indicator reload failed: {message}')

    def _wire_indicator_panel(self) -> None:
        if self.indicator_panel is None:
            return
        self.indicator_panel.indicator_add_requested.connect(self._add_indicator_instance)
        self.indicator_panel.indicator_instance_selected.connect(self._select_indicator_instance)
        self.indicator_panel.indicator_remove_requested.connect(self._remove_indicator_instance)
        self.indicator_panel.indicator_visibility_toggled.connect(self._toggle_indicator_visibility)
        self.indicator_panel.indicator_params_changed.connect(self._update_indicator_params)
        self.indicator_panel.indicator_pane_changed.connect(self._move_indicator_instance)
        self.indicator_panel.indicator_reset_requested.connect(self._reset_indicator_defaults)
        self._update_indicator_panel()

    def _update_indicator_panel(self) -> None:
        if self.indicator_panel is None:
            return
        available = []
        for info in self._indicator_defs.values():
            available.append(
                {
                    "indicator_id": info.indicator_id,
                    "name": info.name,
                    "inputs": info.inputs,
                    "pane": info.pane,
                }
            )
        available.sort(key=lambda item: item["name"].lower())
        self.indicator_panel.set_available_indicators(available)
        pane_ids = self._current_pane_ids()
        instances = []
        for instance in self._indicator_instances:
            instances.append(
                {
                    "instance_id": instance.get("instance_id"),
                    "indicator_id": instance.get("indicator_id"),
                    "name": instance.get("name"),
                    "pane_id": instance.get("pane_id"),
                    "params": instance.get("params"),
                    "schema": instance.get("schema"),
                    "visible": instance.get("visible", True),
                }
            )
        self.indicator_panel.set_indicator_instances(instances, pane_ids)

    def _load_indicator_instances(self) -> None:
        if self.store is None:
            return
        rows = self.store.get_indicator_instances()
        instances: List[Dict[str, object]] = []
        for instance_id, indicator_id, pane_id, params_json, visible, sort_index in rows:
            info = self._indicator_defs.get(indicator_id)
            if info is None:
                continue
            schema = self._build_schema(info)
            params = self._merge_params(schema.get("inputs", {}), params_json)
            pane_id = self._normalize_pane_id(schema, pane_id)
            self._ensure_indicator_pane(pane_id)
            instances.append(
                {
                    "instance_id": instance_id,
                    "indicator_id": indicator_id,
                    "name": schema.get("name", indicator_id),
                    "pane_id": pane_id,
                    "params": params,
                    "visible": visible,
                    "sort_index": sort_index,
                    "schema": schema,
                    "info": info,
                    "last_output": None,
                }
            )
        self._indicator_instances = instances

    def _build_schema(self, info: IndicatorInfo) -> Dict[str, object]:
        try:
            schema_fn = getattr(info.module, "schema", None)
            if schema_fn is not None:
                schema = schema_fn()
                if isinstance(schema, dict):
                    return schema
        except Exception:
            pass
        return {"id": info.indicator_id, "name": info.name, "inputs": info.inputs, "pane": info.pane}

    def _merge_params(self, inputs: Dict[str, dict], params_json: str) -> Dict[str, object]:
        params: Dict[str, object] = {}
        try:
            params = json.loads(params_json) if params_json else {}
        except Exception:
            params = {}
        for key, spec in inputs.items():
            if key not in params and "default" in spec:
                params[key] = spec["default"]
        return params

    def _normalize_pane_id(self, schema: Dict[str, object], pane_id: str) -> str:
        pane = str(schema.get("pane") or "price")
        if pane in ("price", "overlay"):
            return "price"
        if pane in ("new", "pane") and (not pane_id or pane_id == "new"):
            return self._allocate_pane_id()
        if pane_id:
            return pane_id
        return self._allocate_pane_id()

    def _allocate_pane_id(self) -> str:
        while True:
            pane_id = f"pane-{self._indicator_next_pane_index}"
            self._indicator_next_pane_index += 1
            if pane_id not in self._indicator_panes:
                return pane_id

    def _ensure_indicator_pane(self, pane_id: str) -> None:
        if pane_id == "price":
            return
        if pane_id in self._indicator_panes:
            return
        view_box = pg.ViewBox()
        pane_plot = pg.PlotWidget(viewBox=view_box)
        gradient = QLinearGradient(0, 0, 0, 1)
        gradient.setCoordinateMode(QLinearGradient.CoordinateMode.ObjectBoundingMode)
        gradient.setColorAt(0.0, QColor('#141A26'))
        gradient.setColorAt(1.0, QColor('#101520'))
        pane_plot.setBackground(QBrush(gradient))
        pane_plot.showGrid(x=True, y=True, alpha=0.2)
        pane_plot.setClipToView(True)
        pane_plot.setStyleSheet("border: 0px;")
        pane_plot.setXLink(self.plot_widget)
        try:
            pane_plot.hideAxis('left')
            pane_plot.hideAxis('bottom')
        except Exception:
            pass
        self._apply_axis_style_to_plot(pane_plot)
        self._chart_layout.addWidget(pane_plot)
        self._indicator_panes[pane_id] = pane_plot
        self._indicator_renderers[pane_id] = IndicatorRenderer(pane_plot.getPlotItem())

    def _apply_axis_style_to_plot(self, plot_widget: pg.PlotWidget) -> None:
        axis_pen = pg.mkPen(theme.GRID)
        text_pen = pg.mkPen(theme.TEXT)
        font = QFont()
        font.setPointSize(8)
        for axis_name in ('left', 'bottom', 'right'):
            axis = plot_widget.getAxis(axis_name)
            axis.setPen(axis_pen)
            try:
                axis.setTickPen(axis_pen)
                axis.setStyle(tickPen=axis_pen)
            except Exception:
                pass
            axis.setTextPen(text_pen)
            axis.setTickFont(font)

    def _current_pane_ids(self) -> List[str]:
        return list(self._indicator_panes.keys())

    def _add_indicator_instance(self, indicator_id: str) -> None:
        info = self._indicator_defs.get(indicator_id)
        if info is None:
            self._report_error(f'Indicator not found: {indicator_id}')
            return
        schema = self._build_schema(info)
        pane_id = self._normalize_pane_id(schema, "")
        self._ensure_indicator_pane(pane_id)
        params = self._merge_params(schema.get("inputs", {}), "")
        instance_id = uuid.uuid4().hex
        sort_index = len(self._indicator_instances)
        instance = {
            "instance_id": instance_id,
            "indicator_id": indicator_id,
            "name": schema.get("name", indicator_id),
            "pane_id": pane_id,
            "params": params,
            "visible": True,
            "sort_index": sort_index,
            "schema": schema,
            "info": info,
            "last_output": None,
        }
        self._indicator_instances.append(instance)
        self._persist_indicator_instance(instance)
        self._update_indicator_panel()
        self._recompute_indicators()

    def _select_indicator_instance(self, instance_id: str) -> None:
        _ = instance_id

    def _remove_indicator_instance(self, instance_id: str) -> None:
        instance = self._find_indicator_instance(instance_id)
        if instance is None:
            return
        pane_id = instance.get("pane_id", "price")
        renderer = self._indicator_renderers.get(pane_id)
        if renderer:
            renderer.clear_namespace(instance_id)
        self._indicator_instances = [inst for inst in self._indicator_instances if inst.get("instance_id") != instance_id]
        self.store.delete_indicator_instance(instance_id)
        self._cleanup_empty_panes()
        self._update_indicator_panel()

    def _toggle_indicator_visibility(self, instance_id: str, visible: bool) -> None:
        instance = self._find_indicator_instance(instance_id)
        if instance is None:
            return
        instance["visible"] = visible
        self._persist_indicator_instance(instance)
        if not visible:
            pane_id = instance.get("pane_id", "price")
            renderer = self._indicator_renderers.get(pane_id)
            if renderer:
                renderer.clear_namespace(instance_id)
        self._update_indicator_panel()
        if visible:
            self._recompute_indicators()

    def _update_indicator_params(self, instance_id: str, params: dict) -> None:
        instance = self._find_indicator_instance(instance_id)
        if instance is None:
            return
        instance["params"] = params
        self._persist_indicator_instance(instance)
        self._recompute_indicators()

    def _move_indicator_instance(self, instance_id: str, pane_id: str) -> None:
        instance = self._find_indicator_instance(instance_id)
        if instance is None:
            return
        old_pane = instance.get("pane_id", "price")
        if pane_id == old_pane:
            return
        self._ensure_indicator_pane(pane_id)
        instance["pane_id"] = pane_id
        self._persist_indicator_instance(instance)
        renderer = self._indicator_renderers.get(old_pane)
        if renderer:
            renderer.clear_namespace(instance_id)
        self._cleanup_empty_panes()
        self._update_indicator_panel()
        self._recompute_indicators()

    def _reset_indicator_defaults(self, instance_id: str) -> None:
        instance = self._find_indicator_instance(instance_id)
        if instance is None:
            return
        schema = instance.get("schema") or {}
        params = self._merge_params(schema.get("inputs", {}), "")
        instance["params"] = params
        self._persist_indicator_instance(instance)
        self._update_indicator_panel()
        self._recompute_indicators()

    def _find_indicator_instance(self, instance_id: str) -> Optional[Dict[str, object]]:
        for instance in self._indicator_instances:
            if instance.get("instance_id") == instance_id:
                return instance
        return None

    def _persist_indicator_instance(self, instance: Dict[str, object]) -> None:
        try:
            params_json = json.dumps(instance.get("params", {}))
            self.store.upsert_indicator_instance(
                instance_id=str(instance.get("instance_id")),
                indicator_id=str(instance.get("indicator_id")),
                pane_id=str(instance.get("pane_id")),
                params_json=params_json,
                visible=bool(instance.get("visible", True)),
                sort_index=int(instance.get("sort_index", 0)),
            )
        except Exception as exc:
            self._report_error(f'Indicator persistence failed: {exc}')

    def _cleanup_empty_panes(self) -> None:
        used_panes = {inst.get("pane_id", "price") for inst in self._indicator_instances}
        for pane_id in list(self._indicator_panes.keys()):
            if pane_id == "price":
                continue
            if pane_id not in used_panes:
                plot_widget = self._indicator_panes.pop(pane_id, None)
                renderer = self._indicator_renderers.pop(pane_id, None)
                if renderer:
                    renderer.clear()
                if plot_widget:
                    try:
                        self._chart_layout.removeWidget(plot_widget)
                        plot_widget.deleteLater()
                    except Exception:
                        pass

    def _recompute_indicators(self) -> None:
        if self._indicator_recompute_pending:
            return
        self._indicator_recompute_pending = True
        QTimer.singleShot(0, self._do_recompute_indicators)

    def _do_recompute_indicators(self) -> None:
        self._indicator_recompute_pending = False
        if self._initial_load_pending:
            return
        bars = getattr(self.candles, "candles", [])
        if not bars:
            return
        for instance in self._indicator_instances:
            if not instance.get("visible", True):
                continue
            info = instance.get("info")
            if info is None:
                continue
            compute_fn = getattr(info.module, "compute", None)
            if compute_fn is None:
                continue
            params = instance.get("params", {})
            try:
                output, required = run_compute(bars, params, compute_fn)
                instance["required_lookback"] = required
                if required and len(bars) < required:
                    continue
            except Exception as exc:
                self._report_error(f'Indicator {instance.get("indicator_id")} failed: {exc}')
                continue
            pane_id = instance.get("pane_id", "price")
            renderer = self._indicator_renderers.get(pane_id)
            if renderer:
                renderer.render(bars, output or {}, namespace=str(instance.get("instance_id")))


    def _load_symbols(self) -> None:
        if self._symbol_worker and self._symbol_worker.isRunning():
            return
        self._set_loading(True, 'Loading symbols...')
        self._symbol_worker = SymbolFetchWorker(self.store, self.exchange)
        self._symbol_worker.data_ready.connect(self._on_symbols_ready)
        self._symbol_worker.error.connect(self._on_symbol_error)
        self._symbol_worker.finished.connect(self._on_symbol_fetch_finished)
        self._symbol_worker.start()

    def _on_symbols_ready(self, symbols: List[str]) -> None:
        if symbols:
            self.symbol_box.blockSignals(True)
            self.symbol_box.clear()
            self.symbol_box.addItems(symbols)
            self.symbol_box.blockSignals(False)
            self._setup_symbol_search()
        self._init_symbol_tabs()
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        self._update_chart_header(symbol, timeframe)
        self._enqueue_history_probe_for_symbol(symbol)
        cached_range = self.store.get_cached_range(self.exchange, symbol, timeframe)
        self._load_initial_data(use_cache_only=bool(cached_range))

    def _on_symbol_error(self, message: str) -> None:
        self._report_error(f'Symbol list fetch failed: {message}')
        self._load_initial_data()

    def _on_symbol_fetch_finished(self) -> None:
        self._set_loading(False, '')

    def _setup_symbol_search(self) -> None:
        model = self.symbol_box.model()
        if model is None:
            return
        proxy = QSortFilterProxyModel(self)
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        proxy.setFilterKeyColumn(0)
        self._symbol_filter = proxy

        completer = QCompleter(proxy, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        popup = completer.popup()
        popup.setObjectName('SymbolCompleterPopup')
        self.symbol_box.setCompleter(completer)
        self.symbol_box.lineEdit().textEdited.connect(proxy.setFilterFixedString)

    def _load_initial_data(self, use_cache_only: bool = False) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        bar_count = 500
        self.candles.set_timeframe(timeframe)
        mode = 'load_cached' if use_cache_only else 'load'
        cached_range = self.store.get_cached_range(self.exchange, symbol, timeframe)
        self._stop_live_stream()
        self._initial_load_pending = bool(not use_cache_only and cached_range is None)
        self._pending_kline = None
        self._pending_trade = None
        if cached_range is not None:
            _, cached_max = cached_range
            interval_ms = timeframe_to_ms(timeframe)
            now_ms = int(time.time() * 1000)
            if interval_ms > 0:
                missing_bars = max(0, (now_ms - cached_max) // interval_ms)
                if missing_bars >= self._stale_cache_bars_threshold:
                    mode = 'load'
        self._start_fetch(mode, symbol, timeframe, bar_count)

    def _on_load_clicked(self) -> None:
        self._load_initial_data()

    def _on_symbol_changed(self) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        self._update_active_tab_symbol(symbol)
        timeframe = self.current_timeframe
        self._update_chart_header(symbol, timeframe)
        self._enqueue_history_probe_for_symbol(symbol)
        cached_range = self.store.get_cached_range(self.exchange, symbol, timeframe)
        self._load_initial_data(use_cache_only=bool(cached_range))

    def _init_symbol_tabs(self) -> None:
        saved = self._settings.value('symbolTabs')
        if isinstance(saved, list):
            entries = [str(s) for s in saved if s]
        elif isinstance(saved, str) and saved:
            entries = [s for s in saved.split(',') if s]
        else:
            entries = []
        if not entries:
            entries = ['BTCUSDT|1m']
        active_index = self._settings.value('symbolTabIndex')
        try:
            active_index = int(active_index)
        except Exception:
            active_index = 0
        active_index = max(0, min(active_index, len(entries) - 1))

        self._tab_syncing = True
        self.tab_bar.blockSignals(True)
        self._clear_tab_bar()
        for entry in entries:
            symbol, tf = self._parse_tab_entry(entry)
            idx = self.tab_bar.addTab(symbol)
            self.tab_bar.setTabData(idx, tf)
        plus_index = self.tab_bar.addTab('+')
        self._ensure_plus_tab(plus_index)
        for idx in range(self.tab_bar.count() - 1):
            self.tab_bar.setTabEnabled(idx, True)
        self.tab_bar.blockSignals(False)
        self._tab_syncing = False

        self._set_active_tab(active_index)

    def _clear_tab_bar(self) -> None:
        while self.tab_bar.count() > 0:
            self.tab_bar.removeTab(0)

    def _set_active_tab(self, index: int) -> None:
        if self.tab_bar.count() == 0:
            return
        index = max(0, min(index, self.tab_bar.count() - 2))
        self._tab_syncing = True
        self.tab_bar.setCurrentIndex(index)
        symbol = self.tab_bar.tabText(index)
        self._set_symbol_from_tab(symbol)
        tf = self._get_tab_timeframe(index)
        if tf:
            self._apply_timeframe_from_tab(tf)
        self._tab_syncing = False

    def _set_symbol_from_tab(self, symbol: str) -> None:
        self.symbol_box.blockSignals(True)
        idx = self.symbol_box.findText(symbol)
        if idx >= 0:
            self.symbol_box.setCurrentIndex(idx)
        else:
            self.symbol_box.setCurrentText(symbol)
        self.symbol_box.blockSignals(False)

    def _update_active_tab_symbol(self, symbol: str) -> None:
        if self._tab_syncing:
            return
        idx = self.tab_bar.currentIndex()
        if idx < 0 or idx >= self.tab_bar.count() - 1:
            return
        if self.tab_bar.tabText(idx) != symbol:
            self.tab_bar.setTabText(idx, symbol)
        self._persist_tabs()

    def _persist_tabs(self) -> None:
        entries = []
        for i in range(self.tab_bar.count() - 1):
            symbol = self.tab_bar.tabText(i)
            tf = self._get_tab_timeframe(i) or self.current_timeframe
            entries.append(f'{symbol}|{tf}')
        self._settings.setValue('symbolTabs', entries)
        self._settings.setValue('symbolTabIndex', self.tab_bar.currentIndex())

    def _on_tab_changed(self, index: int) -> None:
        if self._tab_syncing:
            return
        if self._skip_next_plus:
            self._skip_next_plus = False
            return
        if index == self.tab_bar.count() - 1 and self.tab_bar.tabText(index) == '+':
            self._add_symbol_tab()
            return
        symbol = self.tab_bar.tabText(index)
        tf = self._get_tab_timeframe(index) or self.current_timeframe
        self._tab_syncing = True
        self._apply_timeframe_from_tab(tf)
        self._set_symbol_from_tab(symbol)
        self._tab_syncing = False
        self._persist_tabs()
        self._on_symbol_changed()

    def _on_tab_close_requested(self, index: int) -> None:
        if index < 0 or index >= self.tab_bar.count() - 1:
            return
        if self.tab_bar.count() <= 2:
            return
        self.tab_bar.removeTab(index)
        if self.tab_bar.currentIndex() == self.tab_bar.count() - 1:
            self.tab_bar.setCurrentIndex(max(0, self.tab_bar.count() - 2))
        self._persist_tabs()

    def _on_tab_context_menu(self, pos) -> None:
        index = self.tab_bar.tabAt(pos)
        if index < 0 or index >= self.tab_bar.count() - 1:
            return
        menu = QMenu(self)
        close_action = menu.addAction('Close')
        close_others = menu.addAction('Close Others')
        close_all = menu.addAction('Close All')
        action = menu.exec(self.tab_bar.mapToGlobal(pos))
        if action == close_action:
            self._on_tab_close_requested(index)
        elif action == close_others:
            self._close_other_tabs(index)
        elif action == close_all:
            self._close_all_tabs()

    def _close_other_tabs(self, keep_index: int) -> None:
        for idx in range(self.tab_bar.count() - 2, -1, -1):
            if idx == keep_index:
                continue
            self.tab_bar.removeTab(idx)
        self.tab_bar.setCurrentIndex(min(keep_index, self.tab_bar.count() - 2))
        self._persist_tabs()

    def _close_all_tabs(self) -> None:
        while self.tab_bar.count() > 1:
            self.tab_bar.removeTab(0)
        self.tab_bar.setCurrentIndex(0)
        self._persist_tabs()

    def _add_symbol_tab(self) -> None:
        default_symbol = 'BTCUSDT'
        insert_index = max(0, self.tab_bar.count() - 1)
        self.tab_bar.insertTab(insert_index, default_symbol)
        self.tab_bar.setTabData(insert_index, self.current_timeframe)
        self.tab_bar.setCurrentIndex(insert_index)
        self._set_symbol_from_tab(default_symbol)
        self._ensure_plus_tab(self.tab_bar.count() - 1)
        self._persist_tabs()

    def _on_tab_moved(self, from_index: int, to_index: int) -> None:
        plus_index = self.tab_bar.count() - 1
        moved_plus = self.tab_bar.tabText(to_index) == '+'
        if moved_plus or from_index == plus_index or to_index == plus_index:
            if moved_plus:
                symbol = self.tab_bar.tabText(to_index)
                tf = self._get_tab_timeframe(to_index) or self.current_timeframe
                prev_last = plus_index if plus_index != to_index else from_index
                if prev_last >= 0 and prev_last < self.tab_bar.count():
                    prev_symbol = self.tab_bar.tabText(prev_last)
                    prev_tf = self._get_tab_timeframe(prev_last) or self.current_timeframe
                    self.tab_bar.blockSignals(True)
                    self.tab_bar.setTabText(to_index, prev_symbol)
                    self.tab_bar.setTabData(to_index, prev_tf)
                    self.tab_bar.setTabText(prev_last, '+')
                    self.tab_bar.setTabData(prev_last, None)
                    self.tab_bar.blockSignals(False)
            if self.tab_bar.tabText(self.tab_bar.count() - 1) != '+':
                plus_pos = None
                for idx in range(self.tab_bar.count()):
                    if self.tab_bar.tabText(idx) == '+':
                        plus_pos = idx
                        break
                if plus_pos is not None:
                    self.tab_bar.blockSignals(True)
                    self.tab_bar.moveTab(plus_pos, self.tab_bar.count() - 1)
                    self.tab_bar.blockSignals(False)
            self._ensure_plus_tab(self.tab_bar.count() - 1)
            self._skip_next_plus = True
        self._persist_tabs()

    def _ensure_plus_tab(self, index: int) -> None:
        if index < 0 or index >= self.tab_bar.count():
            return
        self.tab_bar.setTabText(index, '+')
        self.tab_bar.setTabData(index, None)
        self.tab_bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, None)

    def _parse_tab_entry(self, entry: str) -> tuple[str, str]:
        if '|' not in entry:
            return entry, self.current_timeframe
        symbol, tf = entry.split('|', 1)
        symbol = symbol.strip() or 'BTCUSDT'
        tf = tf.strip() or self.current_timeframe
        return symbol, tf

    def _get_tab_timeframe(self, index: int) -> Optional[str]:
        try:
            tf = self.tab_bar.tabData(index)
            if isinstance(tf, str) and tf:
                return tf
        except Exception:
            pass
        return None

    def _apply_timeframe_from_tab(self, timeframe: str) -> None:
        if timeframe == self.current_timeframe:
            if timeframe in self.timeframe_buttons:
                self.timeframe_buttons[timeframe].setChecked(True)
            return
        if timeframe in self.timeframe_buttons:
            self.timeframe_buttons[self.current_timeframe].setChecked(False)
            self.timeframe_buttons[timeframe].setChecked(True)
        self.current_timeframe = timeframe

    def _start_fetch(
        self,
        mode: str,
        symbol: str,
        timeframe: str,
        bar_count: int,
        current_min_ts: Optional[int] = None,
        current_max_ts: Optional[int] = None,
        window_start_ms: Optional[int] = None,
        window_end_ms: Optional[int] = None,
    ) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._last_fetch_mode = mode
        self._fetch_start_ms = int(time.time() * 1000)
        self._set_loading(True, f'Loading {symbol} {timeframe}...')
        self._worker = DataFetchWorker(
            mode,
            self.store,
            self.exchange,
            symbol,
            timeframe,
            bar_count,
            current_min_ts=current_min_ts,
            current_max_ts=current_max_ts,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_fetch_finished)
        self._worker.start()

    def _on_data_ready(self, bars: list) -> None:
        if bars:
            try:
                auto_range = self._last_fetch_mode not in ('backfill', 'window')
                self._ignore_view_range = True
                self.candles.set_historical_data(bars, auto_range=auto_range)
                self._ignore_view_range = False
                try:
                    self._window_start_ms = int(bars[0][0])
                    self._window_end_ms = int(bars[-1][0])
                except Exception:
                    pass
                if self._last_fetch_mode in ('backfill', 'window') and self._pending_backfill_view:
                    try:
                        view_box = self.plot_widget.getViewBox()
                        view_box.setXRange(self._pending_backfill_view[0], self._pending_backfill_view[1], padding=0)
                    except Exception:
                        pass
                    self._pending_backfill_view = None
                if self._initial_load_pending:
                    self._apply_pending_live_updates()
                    self._initial_load_pending = False
            except Exception as exc:
                self._ignore_view_range = False
                self._report_error(f'Chart render failed: {exc}')
        self._recompute_indicators()
        self._refresh_history_end_status()
        self._emit_debug_state()
        self._start_live_stream()
        if self._last_fetch_mode in ('load', 'load_cached'):
            self._start_history_probe()

    def _on_error(self, message: str) -> None:
        self.status_label.setText(f'Error: {message}')
        self.status_label.setStyleSheet('color: #EF5350;')
        self._report_error(message)
        self._emit_debug_state()

    def _on_fetch_finished(self) -> None:
        self._set_loading(False, '')
        if self._fetch_start_ms is not None:
            self._last_fetch_duration_ms = int(time.time() * 1000) - self._fetch_start_ms
            self._fetch_start_ms = None
        self._emit_debug_state()

    def _set_loading(self, is_loading: bool, message: str) -> None:
        self.load_button.setEnabled(not is_loading)
        if is_loading:
            self.status_label.setText(message)
            self.status_label.setStyleSheet('color: #B2B5BE;')
        else:
            if not self.status_label.text().startswith('Error:'):
                self.status_label.setText('')

    def _report_error(self, message: str) -> None:
        if self.error_sink is not None:
            try:
                self.error_sink.append_error(message)
            except Exception:
                pass

    def _enqueue_history_probe_for_symbol(self, symbol: str) -> None:
        for timeframe in self.timeframe_buttons.keys():
            self._enqueue_history_probe(symbol, timeframe)
        self._start_next_history_probe()

    def _enqueue_history_probe(self, symbol: str, timeframe: str) -> None:
        key = (symbol, timeframe)
        if key in self._history_probe_inflight:
            return
        if key in self._history_probe_queue:
            return
        self._history_probe_queue.append(key)

    def _start_history_probe(self) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        self._enqueue_history_probe(symbol, timeframe)
        self._start_next_history_probe()

    def _start_next_history_probe(self) -> None:
        if self._history_probe_worker and self._history_probe_worker.isRunning():
            return
        if not self._history_probe_queue:
            return
        symbol, timeframe = self._history_probe_queue.pop(0)
        key = (symbol, timeframe)
        if key in self._history_probe_inflight:
            return
        self._history_probe_inflight.add(key)
        self._report_error(f'[history] Probing earliest {symbol} {timeframe}...')
        self._history_probe_worker = HistoryProbeWorker(self.store, self.exchange, symbol, timeframe)
        self._history_probe_worker.result.connect(self._on_history_probe_result)
        self._history_probe_worker.error.connect(self._on_history_probe_error)
        self._history_probe_worker.finished.connect(self._on_history_probe_finished)
        self._history_probe_worker.start()

    def _on_history_probe_result(self, symbol: str, timeframe: str, earliest) -> None:
        if earliest is None:
            self._report_error(f'[history] No earliest candle found for {symbol} {timeframe}.')
        else:
            try:
                ts_str = datetime.fromtimestamp(int(earliest) / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                ts_str = str(earliest)
            self._report_error(f'[history] Earliest {symbol} {timeframe}: {ts_str}')
        self._emit_debug_state()

    def _on_history_probe_error(self, message: str) -> None:
        self._report_error(f'[history] Probe failed: {message}')

    def _on_history_probe_finished(self) -> None:
        worker = self._history_probe_worker
        if worker is not None:
            self._history_probe_inflight.discard((worker.symbol, worker.timeframe))
        self._start_next_history_probe()

    def _start_live_stream(self) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        self.candles.set_timeframe(timeframe)
        self._stop_live_stream()
        self._kline_worker = LiveKlineWorker(symbol, timeframe)
        self._kline_worker.kline.connect(self._on_kline)
        self._kline_worker.error.connect(lambda msg: self._report_error(f'Live stream error: {msg}'))
        self._kline_worker.start()
        self._trade_worker = LiveTradeWorker(symbol)
        self._trade_worker.trade.connect(self._on_trade)
        self._trade_worker.error.connect(lambda msg: self._report_error(f'Trade stream error: {msg}'))
        self._trade_worker.start()

    def _stop_live_stream(self) -> None:
        if self._kline_worker is not None:
            self._kline_worker.stop()
            self._kline_worker.wait(500)
            self._kline_worker = None
        if self._trade_worker is not None:
            self._trade_worker.stop()
            self._trade_worker.wait(500)
            self._trade_worker = None

    def shutdown(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(1500)
        if self._symbol_worker and self._symbol_worker.isRunning():
            self._symbol_worker.quit()
            self._symbol_worker.wait(1500)
        if self._indicator_hot_reload is not None:
            try:
                self._indicator_hot_reload.stop()
                self._indicator_hot_reload.wait(1500)
            except Exception:
                pass
            self._indicator_hot_reload = None
        if self._kline_worker is not None:
            self._kline_worker.stop()
            self._kline_worker.wait(1500)
            self._kline_worker = None
        if self._trade_worker is not None:
            self._trade_worker.stop()
            self._trade_worker.wait(1500)
            self._trade_worker = None

    def export_chart_png(self, path: str) -> None:
        pixmap = self.plot_widget.grab()
        pixmap.save(path, 'PNG')

    def _on_kline(self, kline: dict) -> None:
        if self._initial_load_pending:
            self._pending_kline = kline
            return
        try:
            self.candles.update_live_kline(kline)
        except Exception as exc:
            self._report_error(f'Live candle update failed: {exc}')
            return
        if kline.get('closed'):
            try:
                ts = int(kline.get('ts_ms', 0))
                o = float(kline.get('open', 0))
                h = float(kline.get('high', 0))
                l = float(kline.get('low', 0))
                c = float(kline.get('close', 0))
                v = float(kline.get('volume', 0))
                if ts > 0 and o > 0 and h > 0 and l > 0 and c > 0:
                    symbol = self.symbol_box.currentText() or 'BTCUSDT'
                    timeframe = self.current_timeframe
                    self.store.store_bars(self.exchange, symbol, timeframe, [[ts, o, h, l, c, v]])
            except Exception as exc:
                self._report_error(f'Cache update failed: {exc}')
            self._recompute_indicators()
        self._emit_debug_state()

    def _on_trade(self, trade: dict) -> None:
        if self._initial_load_pending:
            self._pending_trade = trade
            return
        try:
            self.candles.update_live_trade(trade)
        except Exception as exc:
            self._report_error(f'Live trade update failed: {exc}')
        self._emit_debug_state()

    def _apply_pending_live_updates(self) -> None:
        if self._pending_kline is not None:
            kline = self._pending_kline
            self._pending_kline = None
            try:
                self.candles.update_live_kline(kline)
                if kline.get('closed'):
                    ts = int(kline.get('ts_ms', 0))
                    o = float(kline.get('open', 0))
                    h = float(kline.get('high', 0))
                    l = float(kline.get('low', 0))
                    c = float(kline.get('close', 0))
                    v = float(kline.get('volume', 0))
                    if ts > 0 and o > 0 and h > 0 and l > 0 and c > 0:
                        symbol = self.symbol_box.currentText() or 'BTCUSDT'
                        timeframe = self.current_timeframe
                        self.store.store_bars(self.exchange, symbol, timeframe, [[ts, o, h, l, c, v]])
            except Exception as exc:
                self._report_error(f'Live candle update failed: {exc}')
        if self._pending_trade is not None:
            trade = self._pending_trade
            self._pending_trade = None
            try:
                self.candles.update_live_trade(trade)
            except Exception as exc:
                self._report_error(f'Live trade update failed: {exc}')
        self._recompute_indicators()

    def _set_timeframe(self, timeframe: str) -> None:
        if timeframe == self.current_timeframe:
            if timeframe in self.timeframe_buttons:
                self.timeframe_buttons[timeframe].setChecked(True)
            return
        if timeframe in self.timeframe_buttons:
            self.timeframe_buttons[self.current_timeframe].setChecked(False)
            self.timeframe_buttons[timeframe].setChecked(True)
        self.current_timeframe = timeframe
        idx = self.tab_bar.currentIndex()
        if idx >= 0 and idx < self.tab_bar.count() - 1:
            self.tab_bar.setTabData(idx, timeframe)
            self._persist_tabs()
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        self._update_chart_header(symbol, timeframe)
        cached_range = self.store.get_cached_range(self.exchange, symbol, timeframe)
        self._load_initial_data(use_cache_only=bool(cached_range))

    def _update_chart_header(self, symbol: str, timeframe: str) -> None:
        try:
            self.candles.set_header(f'{symbol} {timeframe}')
        except Exception:
            pass

    def _on_view_range_changed(self) -> None:
        if self._ignore_view_range:
            return
        if self._clamp_in_progress:
            return
        try:
            view_box = self.plot_widget.getViewBox()
            x_range, _ = view_box.viewRange()
            x_min = x_range[0]
            x_max = x_range[1]
        except Exception:
            return
        tf_ms = self.candles.timeframe_ms or 60_000
        span = x_max - x_min
        span_bars = span / tf_ms if tf_ms > 0 else span
        if span_bars > self._max_visible_bars:
            center = (x_min + x_max) / 2.0
            clamp_span = self._max_visible_bars * tf_ms
            new_min = center - (clamp_span / 2.0)
            new_max = center + (clamp_span / 2.0)
            self._clamp_in_progress = True
            try:
                view_box.setXRange(new_min, new_max, padding=0)
            finally:
                self._clamp_in_progress = False
            return
        self._pending_backfill_view = (x_min, x_max)
        self._backfill_debounce_timer.start(250)
        self._emit_debug_state()

    def _evaluate_backfill(self) -> None:
        if self._backfill_pending or (self._worker and self._worker.isRunning()):
            return
        if self._current_loaded_range()[0] is None:
            return
        try:
            view_box = self.plot_widget.getViewBox()
            x_range, _ = view_box.viewRange()
            x_min, x_max = x_range
        except Exception:
            if not self._pending_backfill_view:
                return
            x_min, x_max = self._pending_backfill_view
        tf_ms = self.candles.timeframe_ms or 60_000
        visible_span = max(1.0, x_max - x_min)
        edge_threshold = max(5 * tf_ms, visible_span * 0.08)
        current_min_ts, current_max_ts = self._current_loaded_range()
        if current_min_ts is None or current_max_ts is None:
            return
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        oldest_ts, oldest_reached = self.store.get_history_limit(self.exchange, symbol, timeframe)
        left_at_end = bool(oldest_reached and oldest_ts is not None and current_min_ts <= oldest_ts)
        now_ms = int(time.time() * 1000)
        right_at_end = (now_ms - current_max_ts) <= edge_threshold
        left_near = (x_min - current_min_ts) <= edge_threshold
        right_near = (x_max >= current_max_ts - edge_threshold)
        left_beyond = (x_min <= current_min_ts - edge_threshold)
        right_beyond = (x_max >= current_max_ts + edge_threshold)
        if (left_near or left_beyond) and not left_at_end:
            self._backfill_pending = True
            self._backfill_timer.start(200)
            return
        if (right_near or right_beyond) and not right_at_end:
            self._backfill_pending = True
            self._backfill_timer.start(200)

    def _trigger_window_load(self) -> None:
        if self._worker and self._worker.isRunning():
            self._backfill_pending = False
            return
        if not self._pending_backfill_view:
            self._backfill_pending = False
            return
        x_min, x_max = self._pending_backfill_view
        tf_ms = self.candles.timeframe_ms or 60_000
        visible_span = max(1.0, x_max - x_min)
        visible_bars = max(1.0, visible_span / float(tf_ms))
        window_bars = max(self._window_bars, int(visible_bars * 1.5))
        buffer_bars = max(self._window_buffer_bars, int(visible_bars * 0.25))
        buffer_ms = int(buffer_bars * tf_ms)
        desired_start = int(x_min - buffer_ms)
        desired_end = int(x_max + buffer_ms)
        desired_span = desired_end - desired_start
        window_span = int(window_bars * tf_ms)
        if desired_span < window_span:
            center = (desired_start + desired_end) / 2.0
            desired_start = int(center - (window_span / 2.0))
            desired_end = int(center + (window_span / 2.0))
        desired_start = max(0, desired_start)
        if self._window_start_ms is not None and self._window_end_ms is not None:
            if desired_start >= self._window_start_ms and desired_end <= self._window_end_ms:
                self._backfill_pending = False
                return
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        self._start_fetch(
            'window',
            symbol,
            timeframe,
            0,
            window_start_ms=desired_start,
            window_end_ms=desired_end,
        )
        self._backfill_pending = False

    def _current_loaded_range(self) -> tuple[Optional[int], Optional[int]]:
        candles = getattr(self.candles, 'candles', [])
        if not candles:
            return None, None
        try:
            return int(candles[0][0]), int(candles[-1][0])
        except Exception:
            return None, None

    def _refresh_history_end_status(self) -> None:
        try:
            symbol = self.symbol_box.currentText() or 'BTCUSDT'
            timeframe = self.current_timeframe
            oldest_ts, oldest_reached = self.store.get_history_limit(self.exchange, symbol, timeframe)
            current_min_ts, _ = self._current_loaded_range()
            reached = bool(oldest_reached and oldest_ts is not None and current_min_ts is not None and current_min_ts <= oldest_ts)
            self.candles.set_history_end(reached)
        except Exception:
            pass

    def clear_history_end(self) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        try:
            self.store.clear_history_limit(self.exchange, symbol, timeframe)
        except Exception as exc:
            self._report_error(f'History limit reset failed: {exc}')
            return
        try:
            self.candles.set_history_end(False)
        except Exception:
            pass
        self._emit_debug_state()

    def _emit_debug_state(self) -> None:
        if self.debug_sink is None:
            return
        now = time.time()
        if now - self._debug_last_update < 0.5:
            return
        self._debug_last_update = now

        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        bars_loaded = len(getattr(self.candles, 'candles', []))
        tf_ms = self.candles.timeframe_ms or 60_000
        cache_range = self.store.get_cached_range(self.exchange, symbol, timeframe)
        oldest_ts, oldest_reached = self.store.get_history_limit(self.exchange, symbol, timeframe)

        view_range = None
        visible_bars = None
        try:
            view_box = self.plot_widget.getViewBox()
            x_range, _ = view_box.viewRange()
            view_range = x_range
            span = x_range[1] - x_range[0]
            visible_bars = span / tf_ms if tf_ms > 0 else None
        except Exception:
            pass

        def fmt_ts(ts: Optional[int]) -> str:
            if ts is None:
                return 'n/a'
            try:
                return datetime.fromtimestamp(ts / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                return str(ts)

        lines = [
            f'Symbol: {symbol}',
            f'Timeframe: {timeframe} ({int(tf_ms / 1000)}s)',
            f'Bars loaded: {bars_loaded}',
        ]
        fps, last_render_ms = self.candles.get_render_stats()
        lines.append(f'Render FPS: {fps:.1f}')
        if last_render_ms:
            lines.append(f'Last render: {fmt_ts(last_render_ms)}')
        if view_range:
            lines.append(f'View range: {int(view_range[0])} .. {int(view_range[1])}')
        if visible_bars is not None:
            lines.append(f'Visible bars: {visible_bars:.0f}')
        if cache_range:
            lines.append(f'Cache range: {fmt_ts(cache_range[0])} .. {fmt_ts(cache_range[1])}')
        else:
            lines.append('Cache range: n/a')
        lines.append(f'Window range: {fmt_ts(self._window_start_ms)} .. {fmt_ts(self._window_end_ms)}')
        lines.append(f'History end: {oldest_reached} (oldest {fmt_ts(oldest_ts)})')
        lines.append(f'Fetch mode: {self._last_fetch_mode}')
        if self._last_fetch_duration_ms is not None:
            lines.append(f'Last fetch: {self._last_fetch_duration_ms} ms')
        lines.append(f'Worker running: {bool(self._worker and self._worker.isRunning())}')
        lines.append(f'Window pending: {self._backfill_pending}')
        lines.append(f'Live kline: {bool(self._kline_worker and self._kline_worker.isRunning())}')
        lines.append(f'Live trades: {bool(self._trade_worker and self._trade_worker.isRunning())}')

        try:
            self.debug_sink.set_metrics(lines)
        except Exception:
            pass
