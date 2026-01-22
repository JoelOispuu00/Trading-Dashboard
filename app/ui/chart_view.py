import os
import time
from typing import Optional, List
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QThread, pyqtSignal

from core.data_store import DataStore
from core.data_fetch import load_recent_bars, load_symbols, load_more_history
from .theme import theme
from .charts.candlestick_chart import CandlestickChart


class DataFetchWorker(QThread):
    data_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, mode: str, store: DataStore, exchange: str, symbol: str, timeframe: str, bar_count: int) -> None:
        super().__init__()
        self.mode = mode
        self.store = store
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe
        self.bar_count = bar_count

    def run(self) -> None:
        try:
            if self.mode == 'load':
                bars = load_recent_bars(self.store, self.exchange, self.symbol, self.timeframe, self.bar_count)
            elif self.mode == 'backfill':
                bars = load_more_history(self.store, self.exchange, self.symbol, self.timeframe, self.bar_count)
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
            except Exception:
                self._time_offset_ms = 0

        sync_time_offset()

        def on_message(ws, message):
            if self._stop:
                return
            try:
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
    def __init__(self, error_sink=None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.toolbar = QWidget()
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(8, 8, 8, 4)
        toolbar_layout.setSpacing(8)

        toolbar_layout.addWidget(QLabel('Symbol'))
        self.symbol_box = QComboBox()
        toolbar_layout.addWidget(self.symbol_box)

        toolbar_layout.addWidget(QLabel('Timeframe'))
        self.timeframe_box = QComboBox()
        self.timeframe_box.addItems(['1m', '5m', '15m', '1h', '4h', '1d'])
        toolbar_layout.addWidget(self.timeframe_box)

        self.load_button = QPushButton('Load')
        toolbar_layout.addWidget(self.load_button)

        self.backfill_button = QPushButton('Load More')
        toolbar_layout.addWidget(self.backfill_button)

        self.status_label = QLabel('')
        toolbar_layout.addWidget(self.status_label)
        toolbar_layout.addStretch(1)

        layout.addWidget(self.toolbar)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground(theme.BACKGROUND)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
        self.plot_widget.setClipToView(True)

        self._apply_axis_style()

        layout.addWidget(self.plot_widget)

        self.candles = CandlestickChart(self.plot_widget, theme.UP, theme.DOWN)
        self._setup_data_store()
        self._load_symbols()

        self.load_button.clicked.connect(self._on_load_clicked)
        self.backfill_button.clicked.connect(self._on_backfill_clicked)
        self.error_sink = error_sink

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
            self.symbol_box.addItems(symbols)
            if 'BTCUSDT' in symbols:
                self.symbol_box.setCurrentText('BTCUSDT')
        self._load_initial_data()

    def _on_symbol_error(self, message: str) -> None:
        self._report_error(f'Symbol list fetch failed: {message}')
        self._load_initial_data()

    def _on_symbol_fetch_finished(self) -> None:
        self._set_loading(False, '')

    def _load_initial_data(self) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.timeframe_box.currentText() or '1m'
        bar_count = 500
        self.candles.set_timeframe(timeframe)
        self._start_fetch('load', symbol, timeframe, bar_count)

    def _on_load_clicked(self) -> None:
        self._load_initial_data()

    def _on_backfill_clicked(self) -> None:
        symbol = self.symbol_box.currentText() or 'BTCUSDT'
        timeframe = self.timeframe_box.currentText() or '1m'
        bar_count = 2000
        self.candles.set_timeframe(timeframe)
        self._start_fetch('backfill', symbol, timeframe, bar_count)

    def _start_fetch(self, mode: str, symbol: str, timeframe: str, bar_count: int) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._set_loading(True, f'Loading {symbol} {timeframe}...')
        self._worker = DataFetchWorker(mode, self.store, self.exchange, symbol, timeframe, bar_count)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_fetch_finished)
        self._worker.start()

    def _on_data_ready(self, bars: list) -> None:
        if bars:
            try:
                self.candles.set_historical_data(bars)
            except Exception as exc:
                self._report_error(f'Chart render failed: {exc}')
        self._start_live_stream()

    def _on_error(self, message: str) -> None:
        self.status_label.setText(f'Error: {message}')
        self.status_label.setStyleSheet('color: #EF5350;')
        self._report_error(message)

    def _on_fetch_finished(self) -> None:
        self._set_loading(False, '')

    def _set_loading(self, is_loading: bool, message: str) -> None:
        self.load_button.setEnabled(not is_loading)
        self.backfill_button.setEnabled(not is_loading)
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
        timeframe = self.timeframe_box.currentText() or '1m'
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
                    timeframe = self.timeframe_box.currentText() or '1m'
                    self.store.store_bars(self.exchange, symbol, timeframe, [[ts, o, h, l, c, v]])
            except Exception as exc:
                self._report_error(f'Cache update failed: {exc}')

    def _on_trade(self, trade: dict) -> None:
        try:
            self.candles.update_live_trade(trade)
        except Exception as exc:
            self._report_error(f'Live trade update failed: {exc}')
