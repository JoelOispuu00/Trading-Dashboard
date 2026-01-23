import os
import time
from datetime import datetime
from typing import Optional, List
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel, QCompleter, QButtonGroup, QTabBar
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QThread, pyqtSignal, QSortFilterProxyModel, Qt, QTimer, QSettings

from core.data_store import DataStore
from core.data_fetch import load_recent_bars, load_symbols, load_more_history, load_cached_bars, load_cached_full, load_window_bars
from .theme import theme
from .charts.candlestick_chart import CandlestickChart


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
    def __init__(self, error_sink=None, debug_sink=None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.toolbar = QWidget()
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(8, 8, 8, 4)
        toolbar_layout.setSpacing(8)

        toolbar_layout.addWidget(QLabel('Symbol'))
        self.symbol_box = QComboBox()
        self.symbol_box.setEditable(True)
        self.symbol_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.symbol_box.setMaxVisibleItems(20)
        self.symbol_box.setMinimumWidth(240)
        toolbar_layout.addWidget(self.symbol_box)

        toolbar_layout.addWidget(QLabel('Timeframe'))
        self.timeframe_buttons: dict[str, QPushButton] = {}
        self.timeframe_group = QButtonGroup(self)
        self.timeframe_group.setExclusive(True)
        self.current_timeframe = '1m'
        for tf in ['1m', '5m', '15m', '1h', '4h', '1d', '1w', '1M']:
            button = QPushButton(tf)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, val=tf: self._set_timeframe(val))
            self.timeframe_buttons[tf] = button
            self.timeframe_group.addButton(button)
            toolbar_layout.addWidget(button)
        self.timeframe_buttons[self.current_timeframe].setChecked(True)

        self.load_button = QPushButton('Reset Cache')
        toolbar_layout.addWidget(self.load_button)

        self.status_label = QLabel('')
        toolbar_layout.addWidget(self.status_label)
        toolbar_layout.addStretch(1)

        layout.addWidget(self.toolbar)

        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.setMovable(True)
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tab_bar.tabMoved.connect(self._on_tab_moved)
        layout.addWidget(self.tab_bar)

        view_box = TimeScaleViewBox()
        self.plot_widget = pg.PlotWidget(viewBox=view_box)
        self.plot_widget.setBackground(theme.BACKGROUND)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
        self.plot_widget.setClipToView(True)

        self._apply_axis_style()
        self._ensure_grid_visible()

        layout.addWidget(self.plot_widget)
        self.plot_widget.getViewBox().sigRangeChanged.connect(self._on_view_range_changed)

        self.candles = CandlestickChart(self.plot_widget, theme.UP, theme.DOWN)
        self._setup_data_store()
        self._load_symbols()

        self.load_button.clicked.connect(self._on_load_clicked)
        self.error_sink = error_sink
        self.debug_sink = debug_sink
        self._debug_last_update = 0.0
        self._tab_syncing = False
        self._skip_next_plus = False
        self._settings = QSettings('TradingDashboard', 'TradingDashboard')
        self.symbol_box.currentIndexChanged.connect(self._on_symbol_changed)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_grid_visible()

    def _ensure_grid_visible(self) -> None:
        try:
            self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
            for axis_name in ('left', 'right', 'bottom'):
                axis = self.plot_widget.getAxis(axis_name)
                if axis:
                    axis.setGrid(0.2)
        except Exception:
            pass

    def _apply_axis_style(self) -> None:
        axis_pen = pg.mkPen(theme.GRID)
        text_pen = pg.mkPen(theme.TEXT)
        font = QFont()
        font.setPointSize(9)

        for axis_name in ('left', 'bottom'):
            axis = self.plot_widget.getAxis(axis_name)
            axis.setPen(axis_pen)
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
        self._kline_worker: Optional[LiveKlineWorker] = None
        self._trade_worker: Optional[LiveTradeWorker] = None
        self._symbol_filter = None
        self._auto_backfill_last = 0.0
        self._last_fetch_mode = 'load'
        self._backfill_pending = False
        self._backfill_timer = QTimer(self)
        self._backfill_timer.setSingleShot(True)
        self._backfill_timer.timeout.connect(self._trigger_window_load)
        self._pending_backfill_view: Optional[tuple[float, float]] = None
        self._window_bars = 2000
        self._window_buffer_bars = 500
        self._window_start_ms: Optional[int] = None
        self._window_end_ms: Optional[int] = None
        self._ignore_view_range = False
        self._max_visible_bars = 1000
        self._clamp_in_progress = False
        self._fetch_start_ms: Optional[int] = None
        self._last_fetch_duration_ms: Optional[int] = None

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
        self._start_fetch(mode, symbol, timeframe, bar_count)

    def _on_load_clicked(self) -> None:
        self._load_initial_data()

    def _on_symbol_changed(self) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        self._update_active_tab_symbol(symbol)
        timeframe = self.current_timeframe
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
            except Exception as exc:
                self._ignore_view_range = False
                self._report_error(f'Chart render failed: {exc}')
        self._refresh_history_end_status()
        self._emit_debug_state()
        self._start_live_stream()

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

    def _start_live_stream(self) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.current_timeframe
        self.candles.set_timeframe(timeframe)
        if self._kline_worker is not None:
            self._kline_worker.stop()
            self._kline_worker = None
        if self._trade_worker is not None:
            self._trade_worker.stop()
            self._trade_worker = None
        self._kline_worker = LiveKlineWorker(symbol, timeframe)
        self._kline_worker.kline.connect(self._on_kline)
        self._kline_worker.error.connect(lambda msg: self._report_error(f'Live stream error: {msg}'))
        self._kline_worker.start()
        self._trade_worker = LiveTradeWorker(symbol)
        self._trade_worker.trade.connect(self._on_trade)
        self._trade_worker.error.connect(lambda msg: self._report_error(f'Trade stream error: {msg}'))
        self._trade_worker.start()

    def shutdown(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(1500)
        if self._symbol_worker and self._symbol_worker.isRunning():
            self._symbol_worker.quit()
            self._symbol_worker.wait(1500)
        if self._kline_worker is not None:
            self._kline_worker.stop()
            self._kline_worker.wait(1500)
            self._kline_worker = None
        if self._trade_worker is not None:
            self._trade_worker.stop()
            self._trade_worker.wait(1500)
            self._trade_worker = None

    def _on_kline(self, kline: dict) -> None:
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
        self._emit_debug_state()

    def _on_trade(self, trade: dict) -> None:
        try:
            self.candles.update_live_trade(trade)
        except Exception as exc:
            self._report_error(f'Live trade update failed: {exc}')
        self._emit_debug_state()

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
        cached_range = self.store.get_cached_range(self.exchange, symbol, timeframe)
        self._load_initial_data(use_cache_only=bool(cached_range))

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
        if self._backfill_pending or self._worker and self._worker.isRunning():
            return
        if self._current_loaded_range()[0] is None:
            return
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
        right_near = (current_max_ts - x_max) <= edge_threshold
        if (left_near and not left_at_end) or (right_near and not right_at_end):
            self._pending_backfill_view = (x_min, x_max)
            self._backfill_pending = True
            self._backfill_timer.start(200)
        self._emit_debug_state()

    def _trigger_window_load(self) -> None:
        if self._worker and self._worker.isRunning():
            self._backfill_pending = False
            return
        if not self._pending_backfill_view:
            self._backfill_pending = False
            return
        x_min, x_max = self._pending_backfill_view
        tf_ms = self.candles.timeframe_ms or 60_000
        buffer_ms = int(self._window_buffer_bars * tf_ms)
        desired_start = int(x_min - buffer_ms)
        desired_end = int(x_max + buffer_ms)
        desired_span = desired_end - desired_start
        window_span = int(self._window_bars * tf_ms)
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
