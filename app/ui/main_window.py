from PyQt6.QtWidgets import QMainWindow, QDockWidget, QTabWidget
from PyQt6.QtCore import Qt, QSettings

from .chart_view import ChartView
from .indicator_panel import IndicatorPanel
from .error_dock import ErrorDock


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('Trading Dashboard')
        self.resize(1400, 900)

        self.indicator_panel = IndicatorPanel()
        self.error_dock = ErrorDock()
        self.chart_view = ChartView(error_sink=self.error_dock)
        self.setCentralWidget(self.chart_view)

        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.indicator_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.error_dock)

        self.tabifyDockWidget(self.indicator_panel, self.error_dock)
        self.setTabPosition(Qt.DockWidgetArea.RightDockWidgetArea, QTabWidget.TabPosition.East)
        self.indicator_panel.raise_()

        self._settings = QSettings('TradingDashboard', 'TradingDashboard')
        self._restore_layout()

    def closeEvent(self, event) -> None:
        self._save_layout()
        super().closeEvent(event)

    def _save_layout(self) -> None:
        self._settings.setValue('geometry', self.saveGeometry())
        self._settings.setValue('windowState', self.saveState())

    def _restore_layout(self) -> None:
        geometry = self._settings.value('geometry')
        window_state = self._settings.value('windowState')
        if geometry is not None:
            self.restoreGeometry(geometry)
        if window_state is not None:
            self.restoreState(window_state)

