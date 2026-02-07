from __future__ import annotations

import csv
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.strategies.report import StrategyReport
from core.strategies.store import StrategyStore
from .strategy_equity import StrategyEquityWidget


class StrategyReportDock(QDockWidget):
    trade_selected = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__("Strategy Report")
        self.setObjectName("StrategyReportDock")
        self._trades: List = []
        self._store: Optional[StrategyStore] = None
        self._error_sink = None
        self._context: Optional[Tuple[str, str, str]] = None  # (symbol, timeframe, strategy_id)
        self._run_id_by_index: List[str] = []

        container = QWidget()
        layout = QVBoxLayout(container)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Run"))
        self.run_selector = QComboBox()
        self.run_selector.setObjectName("StrategyRunSelector")
        self.run_selector.currentIndexChanged.connect(self._on_run_selected)
        self.run_selector.setMinimumWidth(220)
        header_row.addWidget(self.run_selector, 1)
        self.export_trades_btn = QPushButton("Export trades CSV")
        self.export_trades_btn.setObjectName("StrategyExportTrades")
        self.export_trades_btn.clicked.connect(self._export_trades_csv)
        self.export_equity_btn = QPushButton("Export equity CSV")
        self.export_equity_btn.setObjectName("StrategyExportEquity")
        self.export_equity_btn.clicked.connect(self._export_equity_csv)
        header_row.addWidget(self.export_trades_btn)
        header_row.addWidget(self.export_equity_btn)
        layout.addLayout(header_row)

        self.stats_label = QLabel("Run a strategy to see stats.")
        self.stats_label.setObjectName("StrategyStatsRow")
        layout.addWidget(self.stats_label)

        self.equity_widget = StrategyEquityWidget()
        self.equity_widget.setObjectName("StrategyEquityPlot")
        layout.addWidget(self.equity_widget)

        self.trades_table = QTableWidget(0, 10)
        self.trades_table.setObjectName("StrategyTradesTable")
        self.trades_table.setMinimumHeight(160)
        self.trades_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.trades_table.setHorizontalHeaderLabels(
            ["Side", "Entry (UTC)", "Entry Px", "Exit (UTC)", "Exit Px", "Size", "Gross", "Fees", "Net PnL", "Bars"]
        )
        self.trades_table.setSortingEnabled(True)
        self.trades_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.trades_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.trades_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.trades_table.horizontalHeader().setStretchLastSection(True)
        self.trades_table.cellClicked.connect(self._on_trade_clicked)
        layout.addWidget(self.trades_table)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setObjectName("StrategyReportScroll")
        scroll.setWidget(container)
        self.setWidget(scroll)

    def set_store(self, store: StrategyStore) -> None:
        self._store = store

    def set_error_sink(self, sink) -> None:
        # Expected to be ErrorDock-like: has append_error(str).
        self._error_sink = sink

    def set_context(self, symbol: str, timeframe: str, strategy_id: str) -> None:
        self._context = (symbol, timeframe, strategy_id)
        self._refresh_run_selector()

    def set_report(self, report: StrategyReport) -> None:
        self._trades = list(report.trades)
        stats = report.stats
        self.stats_label.setText(
            f"Return: {stats.get('total_return_pct', 0):.2f}% | "
            f"Max DD: {stats.get('max_drawdown_pct', 0):.2f}% | "
            f"Trades: {int(stats.get('num_trades', 0))} | "
            f"Win rate: {stats.get('win_rate_pct', 0):.1f}% | "
            f"PF: {stats.get('profit_factor', 0):.2f}"
        )
        self.equity_widget.set_equity(report.equity_ts, report.equity)

        self.trades_table.setRowCount(0)
        for trade in report.trades:
            row = self.trades_table.rowCount()
            self.trades_table.insertRow(row)

            gross = (trade.exit_price - trade.entry_price) * trade.size
            if trade.side.upper() == "SHORT":
                gross = (trade.entry_price - trade.exit_price) * abs(trade.size)
            fees = float(trade.fee_total)
            net = float(trade.pnl)

            self.trades_table.setItem(row, 0, QTableWidgetItem(trade.side))
            self.trades_table.setItem(row, 1, QTableWidgetItem(self._fmt_utc(trade.entry_ts)))
            self.trades_table.setItem(row, 2, QTableWidgetItem(f"{trade.entry_price:.2f}"))
            self.trades_table.setItem(row, 3, QTableWidgetItem(self._fmt_utc(trade.exit_ts)))
            self.trades_table.setItem(row, 4, QTableWidgetItem(f"{trade.exit_price:.2f}"))
            self.trades_table.setItem(row, 5, QTableWidgetItem(f"{trade.size:.4f}"))
            self.trades_table.setItem(row, 6, QTableWidgetItem(f"{gross:.2f}"))
            self.trades_table.setItem(row, 7, QTableWidgetItem(f"{fees:.2f}"))
            pnl_item = QTableWidgetItem(f"{net:.2f}")
            pnl_item.setForeground(QColor("#4ADE80") if net >= 0 else QColor("#EF5350"))
            self.trades_table.setItem(row, 8, pnl_item)
            self.trades_table.setItem(row, 9, QTableWidgetItem(str(trade.bars_held)))

        try:
            self.trades_table.resizeColumnsToContents()
            for c in (1, 3):
                self.trades_table.setColumnWidth(c, 140)
        except Exception:
            pass

        self._select_run_id(report.run_id)

    def set_visible_range(self, ts_min: int, ts_max: int) -> None:
        self.equity_widget.set_visible_range(ts_min, ts_max)

    def _on_trade_clicked(self, row: int, _column: int) -> None:
        if row < 0 or row >= len(self._trades):
            return
        trade = self._trades[row]
        try:
            ts = int(trade.entry_ts)
        except Exception:
            return
        self.trade_selected.emit(ts)

    def _refresh_run_selector(self) -> None:
        if self._store is None or self._context is None:
            return
        symbol, timeframe, strategy_id = self._context
        rows = self._store.list_recent_runs(symbol=symbol, timeframe=timeframe, strategy_id=strategy_id, limit=50)
        self.run_selector.blockSignals(True)
        try:
            self.run_selector.clear()
            self._run_id_by_index = []
            for r in rows:
                run_id = str(r.get("run_id"))
                created_at = int(r.get("created_at") or 0)
                status = str(r.get("status") or "")
                # Skip corrupted/partial runs (e.g. crash mid-write). Verification is fast enough here.
                try:
                    ok, issues, _stats = self._store.verify_run(run_id)
                except Exception:
                    ok, issues = True, []
                if not ok:
                    try:
                        if self._error_sink is not None:
                            self._error_sink.append_error(f"Skipping corrupt strategy run {run_id}: {issues}")
                    except Exception:
                        pass
                    continue
                label = f"{self._fmt_utc(created_at)}  [{status}]  {run_id}"
                self.run_selector.addItem(label)
                self._run_id_by_index.append(run_id)
        finally:
            self.run_selector.blockSignals(False)

    def _select_run_id(self, run_id: str) -> None:
        if not run_id:
            return
        for idx, rid in enumerate(self._run_id_by_index):
            if rid == run_id:
                self.run_selector.setCurrentIndex(idx)
                return

    def _on_run_selected(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._run_id_by_index):
            return
        if self._store is None:
            return
        run_id = self._run_id_by_index[idx]
        try:
            ok, issues, _stats = self._store.verify_run(run_id)
            if not ok:
                if self._error_sink is not None:
                    try:
                        self._error_sink.append_error(f"Refusing to load corrupt strategy run {run_id}: {issues}")
                    except Exception:
                        pass
                return
        except Exception:
            pass
        report = self._store.load_run_report(run_id)
        if report is not None:
            self.set_report(report)

    def _export_trades_csv(self) -> None:
        if not self._trades:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export trades CSV", "trades.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["side", "entry_ts", "entry_price", "exit_ts", "exit_price", "size", "pnl", "fee_total", "bars_held"])
            for t in self._trades:
                w.writerow([t.side, t.entry_ts, t.entry_price, t.exit_ts, t.exit_price, t.size, t.pnl, t.fee_total, t.bars_held])

    def _export_equity_csv(self) -> None:
        ts = getattr(self.equity_widget, "_last_ts", None)
        eq = getattr(self.equity_widget, "_last_equity", None)
        if not ts or not eq:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export equity CSV", "equity.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts", "equity"])
            for t, v in zip(ts, eq):
                w.writerow([int(t), float(v)])

    @staticmethod
    def _fmt_utc(ts_ms: int) -> str:
        try:
            dt = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts_ms)
