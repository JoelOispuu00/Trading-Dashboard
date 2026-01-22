from PyQt6.QtWidgets import QMainWindow, QDockWidget, QTabWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtGui import QAction
from PyQt6.QtCore import Qt, QSettings

from .chart_view import ChartView
from .indicator_panel import IndicatorPanel
from .error_dock import ErrorDock
from .debug_dock import DebugDock


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('Trading Dashboard')
        self.resize(1400, 900)

        self.indicator_panel = IndicatorPanel()
        self.error_dock = ErrorDock()
        self.debug_dock = DebugDock()
        self.chart_view = ChartView(error_sink=self.error_dock, debug_sink=self.debug_dock)
        self.setCentralWidget(self.chart_view)

        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.indicator_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.error_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.debug_dock)

        self.tabifyDockWidget(self.indicator_panel, self.error_dock)
        self.tabifyDockWidget(self.error_dock, self.debug_dock)
        self.setTabPosition(Qt.DockWidgetArea.RightDockWidgetArea, QTabWidget.TabPosition.East)
        self.indicator_panel.raise_()

        self._settings = QSettings('TradingDashboard', 'TradingDashboard')
        self._setup_menu()
        self._restore_layout()

    def closeEvent(self, event) -> None:
        self._save_layout()
        try:
            self.chart_view.shutdown()
        except Exception:
            pass
        super().closeEvent(event)

    def _setup_menu(self) -> None:
        menu_bar = self.menuBar()
        window_menu = menu_bar.addMenu('Window')
        settings_menu = menu_bar.addMenu('Settings')

        self._dock_actions = []
        for dock in (self.indicator_panel, self.error_dock, self.debug_dock):
            action = QAction(dock.windowTitle(), self)
            action.setCheckable(True)
            action.setChecked(not dock.isHidden())
            action.triggered.connect(lambda checked, d=dock: self._toggle_dock(d, checked))
            dock.visibilityChanged.connect(lambda visible, a=action: a.setChecked(visible))
            window_menu.addAction(action)
            self._dock_actions.append(action)

        settings_action = QAction('Preferences...', self)
        settings_action.triggered.connect(self._open_settings)
        settings_menu.addAction(settings_action)

        themes_menu = settings_menu.addMenu('Themes')
        themes_action = QAction('Theme Editor...', self)
        themes_action.triggered.connect(self._open_theme_editor)
        themes_menu.addAction(themes_action)

    def _toggle_dock(self, dock: QDockWidget, visible: bool) -> None:
        if visible:
            dock.show()
            dock.raise_()
        else:
            dock.hide()

    def _open_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle('Settings')
        dialog.setModal(True)
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Settings (coming soon)'))
        layout.addWidget(QLabel('Data: default symbol/timeframe, cache settings'))
        layout.addWidget(QLabel('Chart: grid, crosshair, candle width'))
        layout.addWidget(QLabel('Live: stream source, refresh rates'))
        layout.addWidget(QLabel('UI: layout persistence, font size'))
        layout.addWidget(QLabel('Performance: max FPS, throttling'))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        dialog.exec()

    def _open_theme_editor(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle('Theme Editor')
        dialog.setModal(True)
        dialog.setMinimumWidth(420)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Theme editor (coming soon)'))
        layout.addWidget(QLabel('Customize colors, grid, candle styles'))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(dialog.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        dialog.exec()

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

