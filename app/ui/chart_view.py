import json
import os
import time
import uuid
from bisect import bisect_left, bisect_right
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple, Callable
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel, QCompleter, QButtonGroup, QTabBar, QStyle, QLineEdit, QMenu
from PyQt6.QtGui import QFont, QColor, QLinearGradient, QBrush, QIcon
from PyQt6.QtCore import QThread, pyqtSignal, QSortFilterProxyModel, Qt, QTimer, QSettings, QSize

from core.data_store import DataStore
from core.data_fetch import load_recent_bars, load_symbols, load_more_history, load_cached_bars, load_cached_full, load_window_bars, load_range_bars, timeframe_to_ms, ensure_history_floor
from core.indicator_registry import discover_indicators, IndicatorInfo
from core.hot_reload import start_fs_watcher, QtFsHotReload
from core.strategies.registry import discover_strategies, StrategyInfo
from core.strategies.schema import validate_schema, resolve_params
from core.strategies.backtest import run_backtest
from core.strategies.report import build_report
from core.strategies.store import StrategyStore
from core.strategies.models import RunConfig
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


class IndicatorComputeWorker(QThread):
    result = pyqtSignal(int, list)
    error = pyqtSignal(str)

    def __init__(self, tasks: list, reason: str, seq: int) -> None:
        super().__init__()
        self._tasks = tasks
        self._reason = reason
        self._seq = seq

    def run(self) -> None:
        results = []
        try:
            for task in self._tasks:
                instance_id = task.get("instance_id")
                compute_fn = task.get("compute_fn")
                bars = task.get("compute_bars") or task.get("bars")
                params = task.get("params", {})
                if compute_fn is None or not bars:
                    continue
                output, required = run_compute(bars, params, compute_fn)
                output = self._prep_output_arrays(output or {})
                results.append({
                    "instance_id": instance_id,
                    "output": output or {},
                    "required": required,
                    "pane_id": task.get("pane_id", "price"),
                    "view_key": task.get("view_key"),
                    "view_idx_key": task.get("view_idx_key"),
                    "bars": task.get("render_bars") or bars,
                    "merge": bool(task.get("merge")),
                    "tail_len": int(task.get("tail_len") or 0),
                    "bars_key": task.get("bars_key"),
                    "compute_start_idx": task.get("compute_start_idx"),
                    "compute_end_idx": task.get("compute_end_idx"),
                    "reason": self._reason,
                })
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.result.emit(self._seq, results)

    @staticmethod
    def _prep_output_arrays(output: Dict[str, Any]) -> Dict[str, Any]:
        if not output:
            return output
        try:
            series = output.get("series")
            if isinstance(series, list):
                for spec in series:
                    if isinstance(spec, dict) and "values" in spec:
                        spec["values"] = np.asarray(spec["values"], dtype=np.float64)
            bands = output.get("bands")
            if isinstance(bands, list):
                for spec in bands:
                    if not isinstance(spec, dict):
                        continue
                    if "upper" in spec:
                        spec["upper"] = np.asarray(spec["upper"], dtype=np.float64)
                    if "lower" in spec:
                        spec["lower"] = np.asarray(spec["lower"], dtype=np.float64)
            hist = output.get("hist")
            if isinstance(hist, list):
                for spec in hist:
                    if isinstance(spec, dict) and "values" in spec:
                        spec["values"] = np.asarray(spec["values"], dtype=np.float64)
        except Exception:
            return output
        return output


class StrategyBacktestWorker(QThread):
    finished = pyqtSignal(str, object, object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    stage = pyqtSignal(str)

    def __init__(
        self,
        store: DataStore,
        strategy_info: StrategyInfo,
        params: dict,
        run_config: RunConfig,
        exchange: str,
        cancel_flag: Callable[[], bool],
    ) -> None:
        super().__init__()
        self._store = store
        self._strategy_info = strategy_info
        self._params = params
        self._run_config = run_config
        self._exchange = exchange
        self._cancel_flag = cancel_flag

    def run(self) -> None:
        try:
            self.stage.emit("Loading bars...")
            bars = load_range_bars(
                self._store,
                self._exchange,
                self._run_config.symbol,
                self._run_config.timeframe,
                self._run_config.start_ts - (self._run_config.warmup_bars * timeframe_to_ms(self._run_config.timeframe)),
                self._run_config.end_ts,
                allow_fetch=bool(getattr(self._run_config, "allow_fetch", True)),
            )
            if not bars:
                raise ValueError("No bars returned for strategy backtest")
            bars_np = np.asarray(bars, dtype=np.float64)
            self.stage.emit("Running backtest...")
            result, status = run_backtest(
                bars_np,
                self._strategy_info.module,
                self._params,
                self._run_config,
                cancel_flag=self._cancel_flag,
                progress_cb=lambda i, n: self.progress.emit(i, n),
            )
            self.stage.emit("Finishing...")
            self.finished.emit(status, result, bars_np)
        except Exception as exc:
            self.error.emit(str(exc))

class CandleNormalizeWorker(QThread):
    result = pyqtSignal(int, list, list, int)
    error = pyqtSignal(str)

    def __init__(self, data: list, auto_range: bool, seq: int) -> None:
        super().__init__()
        self._data = data
        self._auto_range = auto_range
        self._seq = seq

    def run(self) -> None:
        normalized = []
        try:
            for c in self._data:
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
            ts_cache = [float(c[0]) for c in normalized]
            self.result.emit(self._seq, normalized, ts_cache, int(self._auto_range))
        except Exception as exc:
            self.error.emit(str(exc))


class BackfillDecisionWorker(QThread):
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(
        self,
        x_min: float,
        x_max: float,
        tf_ms: int,
        current_min_ts: int,
        current_max_ts: int,
        oldest_ts: Optional[int],
        oldest_reached: bool,
        now_ms: int,
    ) -> None:
        super().__init__()
        self._x_min = x_min
        self._x_max = x_max
        self._tf_ms = tf_ms
        self._current_min_ts = current_min_ts
        self._current_max_ts = current_max_ts
        self._oldest_ts = oldest_ts
        self._oldest_reached = bool(oldest_reached)
        self._now_ms = now_ms

    def run(self) -> None:
        try:
            visible_span = max(1.0, self._x_max - self._x_min)
            edge_threshold = max(5 * self._tf_ms, visible_span * 0.08)
            left_at_end = bool(self._oldest_reached and self._oldest_ts is not None and self._current_min_ts <= self._oldest_ts)
            right_at_end = (self._now_ms - self._current_max_ts) <= edge_threshold
            left_near = (self._x_min - self._current_min_ts) <= edge_threshold
            right_near = (self._x_max >= self._current_max_ts - edge_threshold)
            left_beyond = (self._x_min <= self._current_min_ts - edge_threshold)
            right_beyond = (self._x_max >= self._current_max_ts + edge_threshold)
            action = "none"
            if (left_near or left_beyond) and not left_at_end:
                action = "left"
            elif (right_near or right_beyond) and not right_at_end:
                action = "right"
            self.result.emit({
                "action": action,
                "edge_threshold": edge_threshold,
            })
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
    visible_ts_range_changed = pyqtSignal(int, int)
    def __init__(self, error_sink=None, debug_sink=None, indicator_panel=None, strategy_panel=None, strategy_report=None) -> None:
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
        self.strategy_panel = strategy_panel
        self.strategy_report = strategy_report

        self.candles = CandlestickChart(self.plot_widget, theme.UP, theme.DOWN)
        self._setup_data_store()
        self._setup_indicator_system()
        self._setup_strategy_system()
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
        self._backfill_debounce_ms_normal = 250
        self._backfill_debounce_ms_zoomed_out = 400
        self._view_idle_timer = QTimer(self)
        self._view_idle_timer.setSingleShot(True)
        self._view_idle_timer.timeout.connect(self._on_view_idle)
        self._apply_idle_delay_ms = 200
        self._pending_apply_bars: Optional[list] = None
        self._pending_apply_auto_range = False
        self._pending_backfill_view: Optional[tuple[float, float]] = None
        self._last_visible_ts_range: Optional[tuple[int, int]] = None
        self._emitting_visible_range = False
        self._window_bars = 2000
        self._window_buffer_bars = 500
        self._window_start_ms: Optional[int] = None
        self._window_end_ms: Optional[int] = None
        self._ignore_view_range = False
        self._max_visible_bars = 20000
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
        self._indicator_hot_reload: Optional[QtFsHotReload] = None
        self._indicator_recompute_pending = False
        self._indicator_recompute_timer = QTimer(self)
        self._indicator_recompute_timer.setSingleShot(True)
        self._indicator_recompute_timer.timeout.connect(self._do_recompute_indicators)
        self._indicator_recompute_debounce_ms = 100
        self._indicator_max_compute_bars = 2000
        self._indicator_times_cache: Dict[tuple, np.ndarray] = {}
        self._last_indicator_view_idx_key: Optional[Tuple[Optional[int], Optional[int]]] = None
        self._indicator_freeze_visible_bars = 1500
        self._indicator_idle_ms = 200
        self._indicator_idle_timer = QTimer(self)
        self._indicator_idle_timer.setSingleShot(True)
        self._indicator_idle_timer.timeout.connect(self._on_indicator_idle)
        self._indicator_compute_worker: Optional[IndicatorComputeWorker] = None
        self._indicator_compute_pending = False
        self._indicator_last_output: Dict[str, Dict[str, object]] = {}
        self._indicator_cache: Dict[str, Dict[str, Any]] = {}
        self._indicator_compute_seq = 0
        self._indicator_compute_last_ms = 0
        self._candle_normalize_worker: Optional[CandleNormalizeWorker] = None
        self._pending_normalize: Optional[tuple[list, bool]] = None
        self._candle_normalize_seq = 0
        self._candle_normalize_last_ms = 0
        self._candle_normalize_merge: Dict[int, Dict[str, object]] = {}
        self._backfill_decision_worker: Optional[BackfillDecisionWorker] = None
        self._backfill_decision_last_ms = 0
        self._indicator_next_pane_index = 1
        self._last_live_indicator_ms = 0
        self._strategy_paths = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "strategies", "builtins")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "strategies", "custom")),
        ]
        self._strategy_defs: Dict[str, StrategyInfo] = {}
        self._strategy_hot_reload: Optional[QtFsHotReload] = None
        self._strategy_worker: Optional[StrategyBacktestWorker] = None
        self._strategy_cancel_requested = False
        self._strategy_store: Optional[StrategyStore] = None
        self._active_strategy_report = None
        self._strategy_finish_in_progress = False
        self._last_visible_bars = 0

        # Rolling perf window (event-based) for debug dock budgeting.
        self._perf_window_s = 5.0
        self._perf_samples: Dict[str, List[Tuple[float, int]]] = {}
        self._indicator_compute_last_ts: Optional[float] = None
        self._candle_normalize_last_ts: Optional[float] = None
        self._backfill_decision_last_ts: Optional[float] = None
        self._volume_prep_last_seen_ts: Optional[float] = None

    def _setup_indicator_system(self) -> None:
        self._load_indicator_definitions()
        self._load_indicator_instances()
        self._wire_indicator_panel()
        if os.environ.get("PYSUPERCHART_NO_RELOAD") != "1":
            self._start_indicator_hot_reload()
        self._recompute_indicators(immediate=True, reason="view")

    def _setup_strategy_system(self) -> None:
        self._load_strategy_definitions()
        if os.environ.get("PYSUPERCHART_NO_RELOAD") != "1":
            self._start_strategy_hot_reload()
        if self.strategy_panel is not None:
            try:
                self.strategy_panel.set_strategies(self._build_strategy_panel_items())
                self.strategy_panel.run_requested.connect(self._on_strategy_run_requested)
                self.strategy_panel.stop_requested.connect(self._on_strategy_stop_requested)
            except Exception:
                pass
        if self.strategy_report is not None:
            try:
                self.strategy_report.trade_selected.connect(self.jump_to_ts)
                try:
                    self.strategy_report.set_error_sink(self.error_sink)
                except Exception:
                    pass
            except Exception:
                pass

    def _load_indicator_definitions(self) -> None:
        indicators = discover_indicators(self._indicator_paths)
        self._indicator_defs = {info.indicator_id: info for info in indicators}
        self._update_indicator_panel()

    def _start_indicator_hot_reload(self) -> None:
        if self._indicator_hot_reload is not None:
            return
        self._indicator_hot_reload = start_fs_watcher(
            self._indicator_paths,
            on_change=self._reload_indicators_now,
            on_error=self._on_indicator_error,
            debounce_ms=80,
        )

    def _reload_indicators_now(self) -> None:
        try:
            indicators = discover_indicators(self._indicator_paths)
        except Exception as exc:
            self._on_indicator_error(str(exc))
            return
        self._on_indicators_updated(indicators)

    def _start_indicator_compute_worker(self, tasks: list, reason: str) -> None:
        self._indicator_compute_seq += 1
        seq = self._indicator_compute_seq
        self._indicator_compute_last_start = time.time()
        worker = IndicatorComputeWorker(tasks, reason, seq)
        self._indicator_compute_worker = worker
        worker.result.connect(self._on_indicator_compute_result)
        worker.error.connect(self._on_indicator_error)
        worker.finished.connect(self._on_indicator_compute_finished)
        worker.start()

    def _on_indicator_compute_result(self, seq: int, results: list) -> None:
        if seq != self._indicator_compute_seq:
            return
        if hasattr(self, "_indicator_compute_last_start"):
            self._indicator_compute_last_ms = int((time.time() - self._indicator_compute_last_start) * 1000)
            self._indicator_compute_last_ts = time.time()
            self._perf_note("indicator_compute", self._indicator_compute_last_ms)
        for result in results:
            instance_id = str(result.get("instance_id"))
            output = result.get("output") or {}
            pane_id = result.get("pane_id", "price")
            view_key = result.get("view_key")
            view_idx_key = result.get("view_idx_key")
            bars = result.get("bars") or []
            merge = bool(result.get("merge"))
            tail_len = int(result.get("tail_len") or 0)
            bars_key = result.get("bars_key")
            compute_start = result.get("compute_start_idx")
            compute_end = result.get("compute_end_idx")
            instance = self._find_indicator_instance(instance_id)
            if instance is None:
                continue
            instance["required_lookback"] = result.get("required", 0)
            instance["last_view_key"] = view_key
            instance["last_view_idx_key"] = view_idx_key
            if merge:
                prev = self._indicator_last_output.get(instance_id)
                output = self._merge_indicator_output(prev, output, tail_len)
                if tail_len > 0:
                    output = dict(output)
                    output["_tail_len"] = tail_len
            if bars_key and compute_start is not None and compute_end is not None:
                cache = self._ensure_indicator_cache(instance_id, bars_key, len(bars))
                self._apply_output_to_cache(cache, output, compute_start, compute_end)
            elif bars_key:
                self._ensure_indicator_cache(instance_id, bars_key, len(bars))
            self._indicator_last_output[instance_id] = output
            renderer = self._indicator_renderers.get(pane_id)
            if renderer:
                times = None
                if view_key is not None:
                    cached = self._indicator_times_cache.get(view_key)
                    if cached is not None and cached.size == len(bars):
                        times = cached
                if times is None:
                    try:
                        times = np.asarray([float(b[0]) for b in bars], dtype=np.float64)
                    except Exception:
                        times = None
                    if times is not None and view_key is not None:
                        self._indicator_times_cache[view_key] = times
                if times is not None:
                    renderer.render((bars, times), output or {}, namespace=instance_id)
                else:
                    renderer.render(bars, output or {}, namespace=instance_id)

    def _on_indicator_compute_finished(self) -> None:
        if self._indicator_compute_pending:
            self._indicator_compute_pending = False
            self._recompute_indicators(immediate=True, reason="view")

    def _merge_indicator_output(self, prev: Optional[Dict[str, Any]], new: Dict[str, Any], tail_len: int) -> Dict[str, Any]:
        if not prev or tail_len <= 0:
            return new
        if any(prev.get(key) for key in ("markers", "regions", "levels")):
            return new
        if any(new.get(key) for key in ("markers", "regions", "levels")):
            return new

        def merge_values(prev_vals, new_vals):
            prev_arr = np.asarray(prev_vals, dtype=np.float64)
            new_arr = np.asarray(new_vals, dtype=np.float64)
            if prev_arr.size == 0 or new_arr.size == 0:
                return prev_vals
            if prev_arr.size < tail_len:
                return new_vals
            new_tail = new_arr[-tail_len:]
            merged = prev_arr.copy()
            merged[-tail_len:] = new_tail
            return merged.tolist()

        merged = dict(prev)
        merged["series"] = []
        for spec in prev.get("series", []):
            spec_id = spec.get("id")
            new_spec = next((s for s in new.get("series", []) if s.get("id") == spec_id), None)
            if new_spec and "values" in new_spec:
                spec = dict(spec)
                spec["values"] = merge_values(spec.get("values", []), new_spec.get("values", []))
            merged["series"].append(spec)

        merged["bands"] = []
        for spec in prev.get("bands", []):
            spec_id = spec.get("id")
            new_spec = next((s for s in new.get("bands", []) if s.get("id") == spec_id), None)
            if new_spec and "upper" in new_spec and "lower" in new_spec:
                spec = dict(spec)
                spec["upper"] = merge_values(spec.get("upper", []), new_spec.get("upper", []))
                spec["lower"] = merge_values(spec.get("lower", []), new_spec.get("lower", []))
            merged["bands"].append(spec)

        merged["hist"] = []
        for spec in prev.get("hist", []):
            spec_id = spec.get("id")
            new_spec = next((s for s in new.get("hist", []) if s.get("id") == spec_id), None)
            if new_spec and "values" in new_spec:
                spec = dict(spec)
                spec["values"] = merge_values(spec.get("values", []), new_spec.get("values", []))
            merged["hist"].append(spec)
        return merged

    def _ensure_indicator_cache(self, instance_id: str, bars_key: Tuple[int, float, float], length: int) -> Dict[str, Any]:
        cache = self._indicator_cache.get(instance_id)
        if cache is None or cache.get("bars_key") != bars_key:
            cache = {
                "bars_key": bars_key,
                "length": length,
                "mask": np.zeros(length, dtype=bool),
                "series": {},
                "series_meta": {},
                "bands": {},
                "bands_meta": {},
                "hist": {},
                "hist_meta": {},
                "markers": [],
                "regions": [],
                "levels": [],
            }
            self._indicator_cache[instance_id] = cache
            return cache
        current_len = int(cache.get("length", 0) or 0)
        if current_len != length:
            delta = length - current_len
            if delta > 0:
                cache["mask"] = np.concatenate([cache["mask"], np.zeros(delta, dtype=bool)])
                for key, arr in cache["series"].items():
                    cache["series"][key] = np.concatenate([arr, np.full(delta, np.nan)])
                for key, band in cache["bands"].items():
                    band["upper"] = np.concatenate([band["upper"], np.full(delta, np.nan)])
                    band["lower"] = np.concatenate([band["lower"], np.full(delta, np.nan)])
                for key, arr in cache["hist"].items():
                    cache["hist"][key] = np.concatenate([arr, np.full(delta, np.nan)])
            else:
                cache["mask"] = cache["mask"][:length]
                for key, arr in cache["series"].items():
                    cache["series"][key] = arr[:length]
                for key, band in cache["bands"].items():
                    band["upper"] = band["upper"][:length]
                    band["lower"] = band["lower"][:length]
                for key, arr in cache["hist"].items():
                    cache["hist"][key] = arr[:length]
            cache["length"] = length
        return cache

    @staticmethod
    def _is_range_cached(mask: np.ndarray, start_idx: Optional[int], end_idx: Optional[int]) -> bool:
        if start_idx is None or end_idx is None:
            return False
        if end_idx <= start_idx:
            return False
        try:
            return bool(mask[start_idx:end_idx].all())
        except Exception:
            return False

    @staticmethod
    def _ensure_segment(values: Any, length: int) -> np.ndarray:
        if isinstance(values, np.ndarray):
            arr = np.asarray(values, dtype=np.float64)
        else:
            arr = np.asarray(list(values), dtype=np.float64)
        if arr.size < length:
            pad = np.full(length - arr.size, np.nan, dtype=np.float64)
            arr = np.concatenate([pad, arr])
        elif arr.size > length:
            arr = arr[-length:]
        return arr

    def _apply_output_to_cache(self, cache: Dict[str, Any], output: Dict[str, Any], start_idx: int, end_idx: int) -> None:
        length = int(cache.get("length", 0) or 0)
        if length <= 0:
            return
        start_idx = max(0, int(start_idx))
        end_idx = min(length, int(end_idx))
        if end_idx <= start_idx:
            return
        seg_len = end_idx - start_idx
        cache["mask"][start_idx:end_idx] = True
        series = output.get("series")
        if isinstance(series, list):
            for spec in series:
                if not isinstance(spec, dict):
                    continue
                series_id = str(spec.get("id", "series"))
                values = self._ensure_segment(spec.get("values", []), seg_len)
                arr = cache["series"].get(series_id)
                if arr is None or arr.size != length:
                    arr = np.full(length, np.nan, dtype=np.float64)
                arr[start_idx:end_idx] = values
                cache["series"][series_id] = arr
                meta = dict(spec)
                meta.pop("values", None)
                meta["id"] = series_id
                cache["series_meta"][series_id] = meta
        bands = output.get("bands")
        if isinstance(bands, list):
            for spec in bands:
                if not isinstance(spec, dict):
                    continue
                band_id = str(spec.get("id", "band"))
                upper = self._ensure_segment(spec.get("upper", []), seg_len)
                lower = self._ensure_segment(spec.get("lower", []), seg_len)
                band = cache["bands"].get(band_id)
                if band is None or band.get("upper") is None or band["upper"].size != length:
                    band = {"upper": np.full(length, np.nan, dtype=np.float64), "lower": np.full(length, np.nan, dtype=np.float64)}
                band["upper"][start_idx:end_idx] = upper
                band["lower"][start_idx:end_idx] = lower
                cache["bands"][band_id] = band
                meta = dict(spec)
                meta.pop("upper", None)
                meta.pop("lower", None)
                meta["id"] = band_id
                cache["bands_meta"][band_id] = meta
        hist = output.get("hist")
        if isinstance(hist, list):
            for spec in hist:
                if not isinstance(spec, dict):
                    continue
                hist_id = str(spec.get("id", "hist"))
                values = self._ensure_segment(spec.get("values", []), seg_len)
                arr = cache["hist"].get(hist_id)
                if arr is None or arr.size != length:
                    arr = np.full(length, np.nan, dtype=np.float64)
                arr[start_idx:end_idx] = values
                cache["hist"][hist_id] = arr
                meta = dict(spec)
                meta.pop("values", None)
                meta["id"] = hist_id
                cache["hist_meta"][hist_id] = meta
        if output.get("markers") is not None:
            cache["markers"] = output.get("markers", [])
        if output.get("regions") is not None:
            cache["regions"] = output.get("regions", [])
        if output.get("levels") is not None:
            cache["levels"] = output.get("levels", [])

    def _build_output_from_cache(self, cache: Dict[str, Any], start_idx: int, end_idx: int) -> Dict[str, Any]:
        start_idx = max(0, int(start_idx))
        end_idx = max(start_idx, int(end_idx))
        series_specs = []
        for series_id, meta in cache.get("series_meta", {}).items():
            arr = cache["series"].get(series_id)
            if arr is None:
                continue
            spec = dict(meta)
            spec["values"] = arr[start_idx:end_idx]
            series_specs.append(spec)
        band_specs = []
        for band_id, meta in cache.get("bands_meta", {}).items():
            band = cache["bands"].get(band_id)
            if band is None:
                continue
            spec = dict(meta)
            spec["upper"] = band["upper"][start_idx:end_idx]
            spec["lower"] = band["lower"][start_idx:end_idx]
            band_specs.append(spec)
        hist_specs = []
        for hist_id, meta in cache.get("hist_meta", {}).items():
            arr = cache["hist"].get(hist_id)
            if arr is None:
                continue
            spec = dict(meta)
            spec["values"] = arr[start_idx:end_idx]
            hist_specs.append(spec)
        output: Dict[str, Any] = {}
        if series_specs:
            output["series"] = series_specs
        if band_specs:
            output["bands"] = band_specs
        if hist_specs:
            output["hist"] = hist_specs
        if cache.get("markers"):
            output["markers"] = cache.get("markers", [])
        if cache.get("regions"):
            output["regions"] = cache.get("regions", [])
        if cache.get("levels"):
            output["levels"] = cache.get("levels", [])
        return output

    def _on_indicators_updated(self, indicators: List[IndicatorInfo]) -> None:
        self._indicator_defs = {info.indicator_id: info for info in indicators}
        self._indicator_cache.clear()
        for instance in self._indicator_instances:
            indicator_id = instance.get("indicator_id")
            info = self._indicator_defs.get(indicator_id)
            if info:
                instance["info"] = info
                instance["schema"] = self._build_schema(info)
        self._update_indicator_panel()
        self._recompute_indicators(immediate=True, reason="params")

    def _on_indicator_error(self, message: str) -> None:
        self._report_error(f'Indicator reload failed: {message}')

    def _load_strategy_definitions(self) -> None:
        strategies = discover_strategies(self._strategy_paths)
        self._strategy_defs = {info.strategy_id: info for info in strategies}
        if self.strategy_panel is not None:
            try:
                self.strategy_panel.set_strategies(self._build_strategy_panel_items())
            except Exception:
                pass

    def _start_strategy_hot_reload(self) -> None:
        if self._strategy_hot_reload is not None:
            return
        self._strategy_hot_reload = start_fs_watcher(
            self._strategy_paths,
            on_change=self._reload_strategies_now,
            on_error=self._on_strategy_error,
            debounce_ms=80,
        )

    def _reload_strategies_now(self) -> None:
        try:
            strategies = discover_strategies(self._strategy_paths)
        except Exception as exc:
            self._on_strategy_error(str(exc))
            return
        self._on_strategies_updated(strategies)

    def _on_strategies_updated(self, strategies: List[StrategyInfo]) -> None:
        self._strategy_defs = {info.strategy_id: info for info in strategies}
        if self.strategy_panel is not None:
            try:
                self.strategy_panel.set_strategies(self._build_strategy_panel_items())
            except Exception:
                pass

    def _on_strategy_error(self, message: str) -> None:
        self._report_error(f'Strategy reload failed: {message}')

    def _build_strategy_panel_items(self) -> List[dict]:
        items: List[dict] = []
        for info in self._strategy_defs.values():
            schema_fn = getattr(info.module, "schema", None)
            if schema_fn is None:
                continue
            try:
                schema = schema_fn()
            except Exception:
                self._report_error(f"Strategy schema error: {info.strategy_id}")
                continue
            ok, err = validate_schema(schema)
            if not ok:
                self._report_error(f"Strategy schema invalid: {err}")
                continue
            params = resolve_params(schema, {})
            items.append({
                "strategy_id": info.strategy_id,
                "name": info.name,
                "schema": schema,
                "params": params,
                "load_error": getattr(info, "load_error", None),
            })
        return items

    def _ensure_strategy_store(self) -> StrategyStore:
        if self._strategy_store is None:
            path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "strategy.sqlite"))
            self._strategy_store = StrategyStore(path)
        return self._strategy_store

    def _on_strategy_run_requested(self, strategy_id: str, params: dict, run_cfg: dict) -> None:
        info = self._strategy_defs.get(strategy_id)
        if info is None:
            self._report_error(f'Strategy not found: {strategy_id}')
            return
        schema = getattr(info.module, "schema", None)
        if not schema:
            self._report_error(f'Strategy schema missing: {strategy_id}')
            return
        schema_dict = schema()
        ok, err = validate_schema(schema_dict)
        if not ok:
            self._report_error(f'Strategy schema invalid: {err}')
            return
        resolved = resolve_params(schema_dict, params)
        use_visible_range = bool(run_cfg.get("use_visible_range"))
        if use_visible_range:
            ts_min, ts_max = self.get_visible_ts_range_snapshot()
            start_ts = ts_min
            end_ts = ts_max
        else:
            start_ts = int(run_cfg.get("start_ts"))
            end_ts = int(run_cfg.get("end_ts"))

        # Important: do not clamp date-based backtests to the currently-loaded chart window.
        # In windowed mode the chart only has a slice of history; the backtest range loader is responsible
        # for validating/fetching coverage from the OHLCV cache/provider.
        if use_visible_range:
            try:
                range_min, range_max = self.candles.get_time_range()
                if range_min is not None and range_max is not None:
                    if start_ts < range_min:
                        start_ts = range_min
                    if end_ts > range_max:
                        end_ts = range_max
            except Exception:
                pass
        if start_ts >= end_ts:
            self._report_error("Invalid backtest range")
            return
        config = RunConfig(
            symbol=self.symbol_box.currentText() or "BTCUSDT",
            timeframe=self.current_timeframe,
            start_ts=start_ts,
            end_ts=end_ts,
            warmup_bars=int(run_cfg.get("warmup_bars", 200)),
            initial_cash=float(run_cfg.get("initial_cash", 1000.0)),
            leverage=float(run_cfg.get("leverage", 1.0)),
            commission_bps=float(run_cfg.get("commission_bps", 0.0)),
            slippage_bps=float(run_cfg.get("slippage_bps", 0.0)),
        )
        try:
            setattr(config, "allow_fetch", bool(run_cfg.get("allow_fetch", True)))
        except Exception:
            pass
        self._strategy_cancel_requested = False
        if self._strategy_worker is not None and self._strategy_worker.isRunning():
            self._report_error("Strategy backtest already running")
            return
        if self.strategy_panel is not None:
            try:
                self.strategy_panel.set_running(True)
                self.strategy_panel.set_status("Starting...")
                self.strategy_panel.set_progress(0, 1)
            except Exception:
                pass
        worker = StrategyBacktestWorker(
            self.store,
            info,
            resolved,
            config,
            self.exchange,
            cancel_flag=lambda: self._strategy_cancel_requested,
        )
        self._strategy_worker = worker
        worker.finished.connect(lambda status, result, bars_np: self._on_strategy_finished(status, result, info, resolved, config, bars_np))
        worker.error.connect(lambda msg: self._report_error(f"Backtest failed: {msg}"))
        worker.progress.connect(self._on_strategy_progress)
        worker.stage.connect(self._on_strategy_stage)
        worker.start()

    def _on_strategy_stop_requested(self) -> None:
        self._strategy_cancel_requested = True
        if self.strategy_panel is not None:
            try:
                self.strategy_panel.set_status("Cancel requested...")
            except Exception:
                pass

    def _on_strategy_progress(self, i: int, n: int) -> None:
        if self.strategy_panel is None:
            return
        try:
            self.strategy_panel.set_progress(i, n)
        except Exception:
            pass

    def _on_strategy_stage(self, text: str) -> None:
        if self.strategy_panel is None:
            return
        try:
            self.strategy_panel.set_status(str(text))
        except Exception:
            pass

    def _on_strategy_finished(self, status: str, result, info: StrategyInfo, params: dict, config: RunConfig, bars_np: np.ndarray) -> None:
        if self._strategy_finish_in_progress:
            return
        self._strategy_finish_in_progress = True
        run_id = f"run_{int(time.time() * 1000)}"
        report = build_report(
            run_id=run_id,
            trades=getattr(result, "trades", []),
            equity_ts=getattr(result, "equity_ts", []),
            equity=getattr(result, "equity", []),
            drawdown=getattr(result, "drawdown", []),
        )
        try:
            bar_min = int(bars_np[0][0])
            bar_max = int(bars_np[-1][0])
            self._report_error(
                f"Backtest done ({status}). Bars={len(bars_np)} range={bar_min}..{bar_max} equity_pts={len(report.equity_ts)} trades={len(report.trades)}."
            )
        except Exception:
            self._report_error(
                f"Backtest done ({status}). equity_pts={len(report.equity_ts)} trades={len(report.trades)}."
            )
        if not report.equity_ts:
            try:
                bar_min = int(bars_np[0][0])
                bar_max = int(bars_np[-1][0])
            except Exception:
                bar_min = None
                bar_max = None
            self._report_error(
                f"Backtest produced no equity points. Range {config.start_ts}..{config.end_ts}, bars {bar_min}..{bar_max}."
            )
        store = None
        try:
            store = self._ensure_strategy_store()
        except Exception:
            store = None
        if store is not None:
            try:
                if self.strategy_panel is not None:
                    try:
                        self.strategy_panel.set_status("Persisting...")
                    except Exception:
                        pass
                run_payload = {
                    "run_id": run_id,
                    "created_at": int(time.time() * 1000),
                    "strategy_id": info.strategy_id,
                    "strategy_name": info.name,
                    "strategy_path": info.path,
                    "symbol": config.symbol,
                    "timeframe": config.timeframe,
                    "start_ts": config.start_ts,
                    "end_ts": config.end_ts,
                    "warmup_bars": config.warmup_bars,
                    "initial_cash": config.initial_cash,
                    "leverage": config.leverage,
                    "commission_bps": config.commission_bps,
                    "slippage_bps": config.slippage_bps,
                    "status": status,
                    "params_json": json.dumps(params),
                    "error_text": None,
                }
                orders_payload = [
                    {
                        "submitted_ts": order.submitted_ts,
                        "fill_ts": order.fill_ts,
                        "side": order.side,
                        "size": order.size,
                        "fill_price": order.fill_price,
                        "fee": order.fee,
                        "status": order.status,
                        "reason": order.reason,
                    }
                    for order in getattr(result, "orders", [])
                ]
                trades_payload = [
                    {
                        "side": trade.side,
                        "size": trade.size,
                        "entry_ts": trade.entry_ts,
                        "entry_price": trade.entry_price,
                        "exit_ts": trade.exit_ts,
                        "exit_price": trade.exit_price,
                        "pnl": trade.pnl,
                        "fee_total": trade.fee_total,
                        "bars_held": trade.bars_held,
                    }
                    for trade in report.trades
                ]
                equity_payload = [
                    {
                        "ts": ts,
                        "equity": eq,
                        "drawdown": dd,
                        "position_size": 0.0,
                        "price": 0.0,
                    }
                    for ts, eq, dd in zip(report.equity_ts, report.equity, report.drawdown)
                ]
                messages_payload = list(getattr(result, "logs", []))

                # Atomic persistence: run row + bundle in a single transaction to avoid partial runs.
                store.insert_complete_run(
                    run=run_payload,
                    orders=orders_payload,
                    trades=trades_payload,
                    equity_points=equity_payload,
                    messages=messages_payload,
                )
                # Optional debug-only integrity verification.
                try:
                    if os.environ.get("PYSUPERCHART_VERIFY_RUN", "").strip() == "1":
                        ok, issues, stats = store.verify_run(run_id)
                        if not ok:
                            msg = f"StrategyStore.verify_run failed for {run_id}: issues={issues} stats={stats}"
                            if self.error_sink is not None:
                                try:
                                    self.error_sink.append_error(msg)
                                except Exception:
                                    pass
                except Exception:
                    pass
            except Exception:
                pass
        report.run_id = run_id
        def _apply_report():
            try:
                if self.strategy_report is not None:
                    try:
                        if store is not None:
                            self.strategy_report.set_store(store)
                            self.strategy_report.set_context(config.symbol, config.timeframe, info.strategy_id)
                    except Exception:
                        pass
                    self.strategy_report.set_report(report)
            except Exception:
                pass
            try:
                # Strategy overlay is behind a flag while we harden against re-entrancy/recursion.
                if os.environ.get("PYSUPERCHART_ENABLE_STRATEGY_OVERLAY", "").strip() == "1":
                    try:
                        self.candles.set_strategy_markers(list(getattr(report, "markers", [])))
                    except Exception:
                        pass
            except Exception:
                pass
            if self.strategy_panel is not None:
                try:
                    self.strategy_panel.set_status(f"Done ({status})")
                    self.strategy_panel.set_running(False)
                except Exception:
                    pass
            self._strategy_finish_in_progress = False
        try:
            QTimer.singleShot(0, _apply_report)
        except Exception:
            _apply_report()

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
                    "load_error": getattr(info, "load_error", None),
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
        self._clear_indicator_cache(instance_id)
        self._persist_indicator_instance(instance)
        self._update_indicator_panel()
        self._recompute_indicators(immediate=True, reason="params")

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
        self._clear_indicator_cache(instance_id)
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
            self._recompute_indicators(immediate=True, reason="params")

    def _update_indicator_params(self, instance_id: str, params: dict) -> None:
        instance = self._find_indicator_instance(instance_id)
        if instance is None:
            return
        instance["params"] = params
        self._clear_indicator_cache(instance_id)
        self._persist_indicator_instance(instance)
        self._recompute_indicators(immediate=True, reason="params")

    def _move_indicator_instance(self, instance_id: str, pane_id: str) -> None:
        instance = self._find_indicator_instance(instance_id)
        if instance is None:
            return
        old_pane = instance.get("pane_id", "price")
        if pane_id == old_pane:
            return
        self._ensure_indicator_pane(pane_id)
        instance["pane_id"] = pane_id
        self._clear_indicator_cache(instance_id)
        self._persist_indicator_instance(instance)
        renderer = self._indicator_renderers.get(old_pane)
        if renderer:
            renderer.clear_namespace(instance_id)
        self._cleanup_empty_panes()
        self._update_indicator_panel()
        self._recompute_indicators(immediate=True, reason="params")

    def _reset_indicator_defaults(self, instance_id: str) -> None:
        instance = self._find_indicator_instance(instance_id)
        if instance is None:
            return
        schema = instance.get("schema") or {}
        params = self._merge_params(schema.get("inputs", {}), "")
        instance["params"] = params
        self._clear_indicator_cache(instance_id)
        self._persist_indicator_instance(instance)
        self._update_indicator_panel()
        self._recompute_indicators(immediate=True, reason="params")

    def _find_indicator_instance(self, instance_id: str) -> Optional[Dict[str, object]]:
        for instance in self._indicator_instances:
            if instance.get("instance_id") == instance_id:
                return instance
        return None

    def _clear_indicator_cache(self, instance_id: str) -> None:
        self._indicator_cache.pop(instance_id, None)

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

    def _recompute_indicators(self, immediate: bool = True, reason: str = "view") -> None:
        if reason == "live" and self._last_visible_bars >= self._indicator_freeze_visible_bars:
            return
        if self._indicator_recompute_pending:
            return
        self._indicator_recompute_pending = True
        self._indicator_recompute_reason = reason
        if reason == "view" and self._last_visible_bars >= self._indicator_freeze_visible_bars and not immediate:
            return
        if immediate:
            self._indicator_recompute_timer.stop()
            QTimer.singleShot(0, self._do_recompute_indicators)
        else:
            self._indicator_recompute_timer.start(self._indicator_recompute_debounce_ms)

    def _do_recompute_indicators(self, force: bool = False) -> None:
        self._indicator_recompute_pending = False
        if self._initial_load_pending:
            return
        bars = getattr(self.candles, "candles", [])
        if not bars:
            return
        bars_key = None
        try:
            bars_key = (len(bars), float(bars[0][0]), float(bars[-1][0]))
        except Exception:
            bars_key = None
        view_start_idx, view_end_idx = self.candles.get_view_index_range(margin=10)
        view_idx_key = (view_start_idx, view_end_idx)
        reason = getattr(self, "_indicator_recompute_reason", "view")
        if reason == "view" and view_idx_key == self._last_indicator_view_idx_key and not force:
            return
        self._last_indicator_view_idx_key = view_idx_key
        view_key = None
        view_bars = bars
        if view_start_idx is not None and view_end_idx is not None:
            view_bars = bars[view_start_idx:view_end_idx]
            if view_bars:
                try:
                    view_key = (len(view_bars), float(view_bars[0][0]), float(view_bars[-1][0]))
                except Exception:
                    view_key = None
        tasks = []
        for instance in self._indicator_instances:
            if not instance.get("visible", True):
                continue
            info = instance.get("info")
            if info is None:
                continue
            compute_fn = getattr(info.module, "compute", None)
            if compute_fn is None:
                continue
            instance_id = str(instance.get("instance_id"))
            params = instance.get("params", {})
            required = int(instance.get("required_lookback", 0) or 0)
            if bars_key is not None:
                cache = self._ensure_indicator_cache(instance_id, bars_key, len(bars))
                if reason == "view" and view_start_idx is not None and view_end_idx is not None:
                    if self._is_range_cached(cache["mask"], view_start_idx, view_end_idx) and not force:
                        instance["last_view_key"] = view_key
                        instance["last_view_idx_key"] = view_idx_key
                        renderer = self._indicator_renderers.get(instance.get("pane_id", "price"))
                        if renderer and view_bars:
                            try:
                                times = np.asarray([float(b[0]) for b in view_bars], dtype=np.float64)
                                cached_output = self._build_output_from_cache(cache, view_start_idx, view_end_idx)
                                if cached_output:
                                    self._indicator_last_output[instance_id] = cached_output
                                    renderer.render((view_bars, times), cached_output, namespace=str(instance.get("instance_id")))
                            except Exception:
                                pass
                        continue
            if view_start_idx is None or view_end_idx is None:
                slice_bars = bars
                render_bars = bars
                compute_start_idx = 0
                compute_end_idx = len(bars)
            else:
                start_idx = max(0, view_start_idx - required)
                end_idx = max(start_idx, view_end_idx)
                slice_bars = bars[start_idx:end_idx]
                render_bars = view_bars
                compute_start_idx = start_idx
                compute_end_idx = end_idx
            merge = False
            tail_len = 0
            last_view_key = instance.get("last_view_key")
            view_changed = view_key is not None and last_view_key != view_key
            if reason == "view" and instance.get("last_view_idx_key") == view_idx_key and not force:
                continue
            if reason == "live" and last_view_key == view_key:
                prev_output = self._indicator_last_output.get(instance_id)
                if prev_output:
                    tail_len = min(len(render_bars), max(required + 2, 20)) if render_bars else 0
                    if tail_len > 0:
                        slice_bars = render_bars[-tail_len:]
                        merge = True
                        if view_end_idx is not None:
                            compute_end_idx = view_end_idx
                            compute_start_idx = max(0, compute_end_idx - len(slice_bars))
            max_compute = max(self._indicator_max_compute_bars, required + 2)
            if (reason != "view" and not view_changed) and len(slice_bars) > max_compute:
                orig_end = compute_end_idx
                slice_bars = slice_bars[-max_compute:]
                compute_end_idx = orig_end
                compute_start_idx = max(0, compute_end_idx - len(slice_bars))
            tasks.append({
                "instance_id": instance_id,
                "compute_fn": compute_fn,
                "params": params,
                "compute_bars": slice_bars,
                "render_bars": render_bars,
                "pane_id": instance.get("pane_id", "price"),
                "view_key": view_key,
                "view_idx_key": view_idx_key,
                "merge": merge,
                "tail_len": tail_len,
                "bars_key": bars_key,
                "compute_start_idx": compute_start_idx,
                "compute_end_idx": compute_end_idx,
            })

        if not tasks:
            return
        if self._indicator_compute_worker and self._indicator_compute_worker.isRunning():
            self._indicator_compute_pending = True
            return
        self._start_indicator_compute_worker(tasks, reason=reason)


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
                self._pending_apply_bars = bars
                self._pending_apply_auto_range = self._last_fetch_mode not in ('backfill', 'window')
                self._view_idle_timer.start(self._apply_idle_delay_ms)
            except Exception as exc:
                self._report_error(f'Chart render failed: {exc}')

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

    def _start_candle_normalize(self, bars: list, auto_range: bool) -> None:
        if self._candle_normalize_worker and self._candle_normalize_worker.isRunning():
            self._pending_normalize = (bars, auto_range)
            return
        if bars:
            try:
                existing = getattr(self.candles, "candles", [])
            except Exception:
                existing = []
            if existing:
                try:
                    bars_start = float(bars[0][0])
                    bars_end = float(bars[-1][0])
                    existing_start = float(existing[0][0])
                    existing_end = float(existing[-1][0])
                except Exception:
                    bars_start = bars_end = existing_start = existing_end = None
                if bars_start is not None:
                    ts_cache = getattr(self.candles, "_ts_cache", [])
                    if ts_cache and bars_start >= existing_start and bars_end <= existing_end:
                        try:
                            start_idx = bisect_left(ts_cache, bars_start)
                            end_idx = bisect_right(ts_cache, bars_end)
                            normalized = existing[start_idx:end_idx]
                        except Exception:
                            normalized = None
                        if normalized:
                            self._ignore_view_range = True
                            self._candle_normalize_seq += 1
                            seq = self._candle_normalize_seq
                            self._candle_normalize_last_ms = 0
                            self._on_candle_normalized(seq, normalized, [], int(auto_range))
                            return
                    if bars_start <= existing_start and bars_end >= existing_end:
                        prefix = []
                        suffix = []
                        try:
                            for row in bars:
                                ts = float(row[0])
                                if ts < existing_start:
                                    prefix.append(row)
                                elif ts > existing_end:
                                    suffix.append(row)
                        except Exception:
                            prefix = []
                            suffix = []
                        if prefix or suffix:
                            self._ignore_view_range = True
                            self._candle_normalize_seq += 1
                            seq = self._candle_normalize_seq
                            self._candle_normalize_last_start = time.time()
                            self._candle_normalize_merge[seq] = {
                                "prefix_len": len(prefix),
                                "suffix_len": len(suffix),
                                "existing": existing,
                                "auto_range": bool(auto_range),
                            }
                            worker = CandleNormalizeWorker(prefix + suffix, auto_range, seq)
                            self._candle_normalize_worker = worker
                            worker.result.connect(self._on_candle_normalized)
                            worker.error.connect(self._on_candle_normalize_error)
                            worker.finished.connect(self._on_candle_normalize_finished)
                            worker.start()
                            self._ignore_view_range = True
                            return
        self._ignore_view_range = True
        self._candle_normalize_seq += 1
        seq = self._candle_normalize_seq
        self._candle_normalize_last_start = time.time()
        worker = CandleNormalizeWorker(bars, auto_range, seq)
        self._candle_normalize_worker = worker
        worker.result.connect(self._on_candle_normalized)
        worker.error.connect(self._on_candle_normalize_error)
        worker.finished.connect(self._on_candle_normalize_finished)
        worker.start()

    def _on_candle_normalized(self, seq: int, normalized: list, ts_cache: list, auto_range_flag: int) -> None:
        if seq != self._candle_normalize_seq:
            return
        if hasattr(self, "_candle_normalize_last_start"):
            self._candle_normalize_last_ms = int((time.time() - self._candle_normalize_last_start) * 1000)
            self._candle_normalize_last_ts = time.time()
            self._perf_note("candle_normalize", self._candle_normalize_last_ms)
        try:
            auto_range = bool(auto_range_flag)
            merge_info = self._candle_normalize_merge.pop(seq, None)
            if merge_info:
                prefix_len = int(merge_info.get("prefix_len", 0))
                suffix_len = int(merge_info.get("suffix_len", 0))
                existing = merge_info.get("existing") or []
                prefix = normalized[:prefix_len] if prefix_len > 0 else []
                suffix = normalized[prefix_len:prefix_len + suffix_len] if suffix_len > 0 else []
                normalized = prefix + existing + suffix
            self.candles.begin_bulk_update()
            self.candles.set_historical_data(normalized, auto_range=False, normalized=True)
            self.candles.end_bulk_update(auto_range=auto_range)
            try:
                self._window_start_ms = int(normalized[0][0]) if normalized else None
                self._window_end_ms = int(normalized[-1][0]) if normalized else None
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
            self._report_error(f'Chart render failed: {exc}')
        finally:
            self._ignore_view_range = False
        self._recompute_indicators(immediate=True, reason="view")
        self._refresh_history_end_status()
        self._emit_debug_state()
        self._start_live_stream()
        if self._last_fetch_mode in ('load', 'load_cached'):
            self._start_history_probe()

    def _on_candle_normalize_error(self, message: str) -> None:
        self._ignore_view_range = False
        self._report_error(f'Chart render failed: {message}')

    def _on_candle_normalize_finished(self) -> None:
        if self._pending_normalize:
            bars, auto_range = self._pending_normalize
            self._pending_normalize = None
            self._start_candle_normalize(bars, auto_range)

    def _start_live_stream(self) -> None:
        if os.environ.get("PYSUPERCHART_NO_LIVE") == "1":
            return
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
            except Exception:
                pass
            self._indicator_hot_reload = None
        if self._strategy_hot_reload is not None:
            try:
                self._strategy_hot_reload.stop()
            except Exception:
                pass
            self._strategy_hot_reload = None
        if self._strategy_worker is not None and self._strategy_worker.isRunning():
            try:
                self._strategy_cancel_requested = True
                self._strategy_worker.quit()
                self._strategy_worker.wait(1500)
            except Exception:
                pass
        if self._kline_worker is not None:
            self._kline_worker.stop()
            self._kline_worker.wait(1500)
            self._kline_worker = None
        if self._trade_worker is not None:
            self._trade_worker.stop()
            self._trade_worker.wait(1500)
            self._trade_worker = None
        if getattr(self, "_strategy_store", None) is not None:
            try:
                self._strategy_store.close()
            except Exception:
                pass

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
            self._recompute_indicators(immediate=True, reason="close")
        self._emit_debug_state()

    def _on_trade(self, trade: dict) -> None:
        if self._initial_load_pending:
            self._pending_trade = trade
            return
        try:
            self.candles.update_live_trade(trade)
        except Exception as exc:
            self._report_error(f'Live trade update failed: {exc}')
        now_ms = int(time.time() * 1000)
        if now_ms - self._last_live_indicator_ms >= 250:
            self._last_live_indicator_ms = now_ms
            self._recompute_indicators(immediate=False, reason="live")
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
        self._recompute_indicators(immediate=True, reason="live")

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
        if not self._emitting_visible_range:
            try:
                ts_min = int(x_min)
                ts_max = int(x_max)
            except Exception:
                ts_min = None
                ts_max = None
            if ts_min is not None and ts_max is not None:
                if self._last_visible_ts_range != (ts_min, ts_max):
                    self._last_visible_ts_range = (ts_min, ts_max)
                    self._emitting_visible_range = True
                    try:
                        self.visible_ts_range_changed.emit(ts_min, ts_max)
                    finally:
                        self._emitting_visible_range = False
        tf_ms = self.candles.timeframe_ms or 60_000
        span = x_max - x_min
        span_bars = span / tf_ms if tf_ms > 0 else span
        self._last_visible_bars = int(span_bars) if span_bars is not None else 0
        try:
            if span_bars and span_bars >= self._indicator_freeze_visible_bars:
                self.candles.set_volume_live_updates_enabled(False)
            else:
                self.candles.set_volume_live_updates_enabled(True)
        except Exception:
            pass
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
        self._view_idle_timer.start(self._apply_idle_delay_ms)
        if span_bars and span_bars >= self._indicator_freeze_visible_bars:
            self._indicator_idle_timer.start(self._indicator_idle_ms)
        debounce_ms = self._backfill_debounce_ms_zoomed_out if span_bars and span_bars >= self._indicator_freeze_visible_bars else self._backfill_debounce_ms_normal
        self._backfill_debounce_timer.start(debounce_ms)
        self._emit_debug_state()
        self._recompute_indicators(immediate=False, reason="view")

    def _on_indicator_idle(self) -> None:
        self._do_recompute_indicators(force=True)

    def _on_view_idle(self) -> None:
        if self._pending_apply_bars is None:
            return
        bars = self._pending_apply_bars
        auto_range = self._pending_apply_auto_range
        self._pending_apply_bars = None
        self._pending_apply_auto_range = False
        try:
            self._start_candle_normalize(bars, auto_range)
        except Exception as exc:
            self._ignore_view_range = False
            self._report_error(f'Chart render failed: {exc}')

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
        now_ms = int(time.time() * 1000)
        if self._backfill_decision_worker and self._backfill_decision_worker.isRunning():
            return
        self._backfill_decision_last_start = time.time()
        self._backfill_decision_worker = BackfillDecisionWorker(
            x_min,
            x_max,
            int(tf_ms),
            int(current_min_ts),
            int(current_max_ts),
            int(oldest_ts) if oldest_ts is not None else None,
            bool(oldest_reached),
            int(now_ms),
        )
        self._backfill_decision_worker.result.connect(self._on_backfill_decision)
        self._backfill_decision_worker.error.connect(lambda msg: None)
        self._backfill_decision_worker.start()

    def _on_backfill_decision(self, result: dict) -> None:
        if hasattr(self, "_backfill_decision_last_start"):
            self._backfill_decision_last_ms = int((time.time() - self._backfill_decision_last_start) * 1000)
            self._backfill_decision_last_ts = time.time()
            self._perf_note("backfill_decision", self._backfill_decision_last_ms)
        action = result.get("action")
        if action in ("left", "right"):
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

    def get_visible_ts_range_snapshot(self) -> tuple[int, int]:
        try:
            view_box = self.plot_widget.getViewBox()
            x_range, _ = view_box.viewRange()
            return int(x_range[0]), int(x_range[1])
        except Exception:
            now_ms = int(time.time() * 1000)
            tf_ms = self.candles.timeframe_ms or 60_000
            return now_ms - tf_ms * 200, now_ms

    def jump_to_ts(self, ts_ms: int) -> None:
        try:
            tf_ms = self.candles.timeframe_ms or 60_000
            span = tf_ms * 400
            start = max(0, int(ts_ms - span / 2))
            end = int(ts_ms + span / 2)
            self._pending_backfill_view = (float(start), float(end))
            self._start_fetch('window', self.symbol_box.currentText() or 'BTCUSDT', self.current_timeframe, 0, window_start_ms=start, window_end_ms=end)
            view_box = self.plot_widget.getViewBox()
            view_box.setXRange(start, end, padding=0)
        except Exception:
            pass

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

    def _perf_note(self, key: str, ms: int) -> None:
        try:
            now = time.time()
            buf = self._perf_samples.setdefault(key, [])
            buf.append((now, int(ms)))
            cutoff = now - float(self._perf_window_s)
            while buf and buf[0][0] < cutoff:
                buf.pop(0)
        except Exception:
            pass

    def _perf_summary(self, key: str) -> tuple[float, int, int]:
        buf = self._perf_samples.get(key) or []
        if not buf:
            return 0.0, 0, 0
        vals = [v for _, v in buf]
        try:
            avg = float(sum(vals)) / float(len(vals)) if vals else 0.0
        except Exception:
            avg = 0.0
        try:
            mx = int(max(vals)) if vals else 0
        except Exception:
            mx = 0
        return avg, mx, int(len(vals))

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

        # Keep strategy UI widgets in sync (resolved range + report x-range) without adding more timers/signals.
        if view_range is not None:
            try:
                ts_min = int(view_range[0])
                ts_max = int(view_range[1])
                if self.strategy_panel is not None:
                    self.strategy_panel.set_resolved_visible_range(ts_min, ts_max)
                if self.strategy_report is not None:
                    self.strategy_report.set_visible_range(ts_min, ts_max)
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
        lines.append(f'Indicator compute: {self._indicator_compute_last_ms} ms')
        lines.append(f'Candle normalize: {self._candle_normalize_last_ms} ms')
        lines.append(f'Backfill decision: {self._backfill_decision_last_ms} ms')
        vol_ms, vol_update_ts = self.candles.get_volume_prep_stats()
        lines.append(f'Volume prep: {vol_ms} ms')
        if vol_update_ts is not None and vol_update_ts != self._volume_prep_last_seen_ts:
            self._volume_prep_last_seen_ts = vol_update_ts
            self._perf_note("volume_prep", int(vol_ms))

        ind_avg, ind_max, ind_n = self._perf_summary("indicator_compute")
        norm_avg, norm_max, norm_n = self._perf_summary("candle_normalize")
        back_avg, back_max, back_n = self._perf_summary("backfill_decision")
        vol_avg, vol_max, vol_n = self._perf_summary("volume_prep")
        lines.append(
            "Perf budget (5s avg/max, n): "
            f"ind={ind_avg:.0f}/{ind_max} ({ind_n}) | "
            f"norm={norm_avg:.0f}/{norm_max} ({norm_n}) | "
            f"backfill={back_avg:.0f}/{back_max} ({back_n}) | "
            f"vol={vol_avg:.0f}/{vol_max} ({vol_n})"
        )

        try:
            total_instances = len(self._indicator_instances)
            active_instances = sum(1 for inst in self._indicator_instances if inst.get("visible", True) is not False)
        except Exception:
            total_instances = 0
            active_instances = 0
        try:
            per_pane = {pane_id: len(getattr(r, "_items", {}) or {}) for pane_id, r in self._indicator_renderers.items()}
            total_items = sum(per_pane.values())
        except Exception:
            per_pane = {}
            total_items = 0
        try:
            candle_body_chunks, candle_line_chunks, candle_chunk_size = self.candles.get_candle_chunk_stats()
            vol_chunks, vol_chunk_size = self.candles.get_volume_chunk_stats()
        except Exception:
            candle_body_chunks, candle_line_chunks, candle_chunk_size = 0, 0, 0
            vol_chunks, vol_chunk_size = 0, 0
        lines.append(
            f"Indicator items: {total_items} ({active_instances}/{total_instances} active) | "
            f"Per-pane: {per_pane or 'n/a'}"
        )
        lines.append(
            f"Chunks: candles body={candle_body_chunks} line={candle_line_chunks} (size {candle_chunk_size}) | "
            f"volume={vol_chunks} (size {vol_chunk_size})"
        )
        lines.append(f'Worker running: {bool(self._worker and self._worker.isRunning())}')
        lines.append(f'Window pending: {self._backfill_pending}')
        lines.append(f'Live kline: {bool(self._kline_worker and self._kline_worker.isRunning())}')
        lines.append(f'Live trades: {bool(self._trade_worker and self._trade_worker.isRunning())}')

        try:
            self.debug_sink.set_metrics(lines)
        except Exception:
            pass
