import os
from PyQt6.QtWidgets import QMainWindow, QDockWidget, QTabWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget, QMenuBar, QFileDialog, QStyle
from PyQt6.QtGui import QAction, QPixmap
from PyQt6.QtCore import Qt, QSettings, QPoint, QRect

from .chart_view import ChartView
from .indicator_panel import IndicatorPanel
from .error_dock import ErrorDock
from .debug_dock import DebugDock


class TitleBar(QWidget):
    def __init__(self, window: QMainWindow, menu_bar: QMenuBar) -> None:
        super().__init__()
        self.window = window
        self.setObjectName('TitleBar')
        self._drag_pos: Optional[QPoint] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        icon_label = QLabel()
        icon_path = os.path.join(os.path.dirname(__file__), 'theme', 'pysuperchart.png')
        if os.path.exists(icon_path):
            pixmap = QPixmap(icon_path)
            icon_label.setPixmap(pixmap.scaled(18, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(icon_label)

        title = QLabel('PySuperChart')
        title.setObjectName('TitleBarTitle')
        layout.addWidget(title)

        menu_bar.setObjectName('TitleBarMenu')
        layout.addWidget(menu_bar)
        layout.addStretch(1)

        self.min_button = QPushButton('–')
        self.min_button.setObjectName('TitleButton')
        self.min_button.clicked.connect(self.window.showMinimized)
        layout.addWidget(self.min_button)

        self.max_button = QPushButton('□')
        self.max_button.setObjectName('TitleButton')
        self.max_button.clicked.connect(self._toggle_maximize)
        layout.addWidget(self.max_button)

        self.close_button = QPushButton('×')
        self.close_button.setObjectName('TitleButtonClose')
        self.close_button.clicked.connect(self.window.close)
        layout.addWidget(self.close_button)

    def _toggle_maximize(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
            self.max_button.setText('□')
        else:
            self.window.showMaximized()
            self.max_button.setText('❐')

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            if not self.window.isMaximized():
                self.window.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()
            event.accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle('PySuperChart')
        self.resize(1400, 900)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self._resize_margin = 6
        self._resizing = False
        self._resize_edges: dict[str, bool] = {}
        self._press_pos: Optional[QPoint] = None
        self._press_geo: Optional[QRect] = None

        self.indicator_panel = IndicatorPanel()
        self.error_dock = ErrorDock()
        self.debug_dock = DebugDock()
        self.chart_view = ChartView(
            error_sink=self.error_dock,
            debug_sink=self.debug_dock,
            indicator_panel=self.indicator_panel,
        )

        self._menu_bar = QMenuBar()
        self.title_bar = TitleBar(self, self._menu_bar)
        self.title_bar.setObjectName('TitleBar')

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.title_bar)
        central_layout.addWidget(self.chart_view)
        self.setCentralWidget(central)

        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.indicator_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.error_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.debug_dock)

        self.tabifyDockWidget(self.indicator_panel, self.error_dock)
        self.tabifyDockWidget(self.error_dock, self.debug_dock)
        self.setTabPosition(Qt.DockWidgetArea.RightDockWidgetArea, QTabWidget.TabPosition.East)
        self.indicator_panel.raise_()
        self._set_dock_icons()

        self._settings = QSettings('PySuperChart', 'PySuperChart')
        self._setup_menu()
        self._restore_layout()

    def _set_dock_icons(self) -> None:
        try:
            style = self.style()
            self.indicator_panel.setWindowIcon(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
            self.error_dock.setWindowIcon(style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical))
            self.debug_dock.setWindowIcon(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._save_layout()
        try:
            self.chart_view.shutdown()
        except Exception:
            pass
        super().closeEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._hit_test_edges(event.pos())
            if any(edges.values()):
                self._resizing = True
                self._resize_edges = edges
                self._press_pos = event.globalPosition().toPoint()
                self._press_geo = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing and self._press_pos is not None and self._press_geo is not None:
            self._perform_resize(event.globalPosition().toPoint())
            event.accept()
            return
        edges = self._hit_test_edges(event.pos())
        self._update_cursor(edges)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            self._resizing = False
            self._resize_edges = {}
            self._press_pos = None
            self._press_geo = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _hit_test_edges(self, pos: QPoint) -> dict[str, bool]:
        rect = self.rect()
        left = pos.x() <= rect.left() + self._resize_margin
        right = pos.x() >= rect.right() - self._resize_margin
        top = pos.y() <= rect.top() + self._resize_margin
        bottom = pos.y() >= rect.bottom() - self._resize_margin
        return {'left': left, 'right': right, 'top': top, 'bottom': bottom}

    def _update_cursor(self, edges: dict[str, bool]) -> None:
        if edges.get('left') and edges.get('top') or edges.get('right') and edges.get('bottom'):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges.get('right') and edges.get('top') or edges.get('left') and edges.get('bottom'):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges.get('left') or edges.get('right'):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges.get('top') or edges.get('bottom'):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _perform_resize(self, global_pos: QPoint) -> None:
        if self._press_geo is None or self._press_pos is None:
            return
        dx = global_pos.x() - self._press_pos.x()
        dy = global_pos.y() - self._press_pos.y()
        geo = QRect(self._press_geo)

        if self._resize_edges.get('left'):
            new_left = geo.left() + dx
            if geo.right() - new_left > self.minimumWidth():
                geo.setLeft(new_left)
        if self._resize_edges.get('right'):
            new_right = geo.right() + dx
            if new_right - geo.left() > self.minimumWidth():
                geo.setRight(new_right)
        if self._resize_edges.get('top'):
            new_top = geo.top() + dy
            if geo.bottom() - new_top > self.minimumHeight():
                geo.setTop(new_top)
        if self._resize_edges.get('bottom'):
            new_bottom = geo.bottom() + dy
            if new_bottom - geo.top() > self.minimumHeight():
                geo.setBottom(new_bottom)

        self.setGeometry(geo)

    def _setup_menu(self) -> None:
        file_menu = self._menu_bar.addMenu('File')
        window_menu = self._menu_bar.addMenu('Window')
        settings_menu = self._menu_bar.addMenu('Settings')

        export_action = QAction('Export Chart as PNG...', self)
        export_action.triggered.connect(self._export_chart_png)
        file_menu.addAction(export_action)

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

        reset_history_action = QAction('Reset History End (Current)...', self)
        reset_history_action.triggered.connect(self._reset_history_end)
        settings_menu.addAction(reset_history_action)

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

    def _reset_history_end(self) -> None:
        try:
            self.chart_view.clear_history_end()
        except Exception:
            pass

    def _export_chart_png(self) -> None:
        default_path = os.path.join(os.path.expanduser('~'), 'pysuperchart.png')
        path, _ = QFileDialog.getSaveFileName(
            self,
            'Export Chart as PNG',
            default_path,
            'PNG Image (*.png)',
        )
        if not path:
            return
        if not path.lower().endswith('.png'):
            path = f'{path}.png'
        try:
            self.chart_view.export_chart_png(path)
        except Exception:
            pass

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

