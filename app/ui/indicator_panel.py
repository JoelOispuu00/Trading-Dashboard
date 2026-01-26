from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import pyqtSignal, Qt


class IndicatorPanel(QDockWidget):
    indicator_add_requested = pyqtSignal(str)
    indicator_instance_selected = pyqtSignal(str)
    indicator_remove_requested = pyqtSignal(str)
    indicator_visibility_toggled = pyqtSignal(str, bool)
    indicator_params_changed = pyqtSignal(str, dict)
    indicator_pane_changed = pyqtSignal(str, str)
    indicator_reset_requested = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__('Indicators')
        self.setObjectName('IndicatorPanel')
        self._available: Dict[str, dict] = {}
        self._instances: Dict[str, dict] = {}
        self._active_instance_id: Optional[str] = None
        self._param_widgets: Dict[str, object] = {}
        self._pane_ids: List[str] = []

        container = QWidget()
        layout = QVBoxLayout(container)

        layout.addWidget(QLabel('Available Indicators'))
        self.indicator_list = QListWidget()
        self.indicator_list.itemDoubleClicked.connect(self._on_available_double_clicked)
        layout.addWidget(self.indicator_list)

        layout.addWidget(QLabel('Active Indicators'))
        self.active_list = QListWidget()
        self.active_list.itemSelectionChanged.connect(self._on_active_selected)
        layout.addWidget(self.active_list)

        self._controls_row = QWidget()
        controls_layout = QHBoxLayout(self._controls_row)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        self.visibility_button = QPushButton('Hide')
        self.visibility_button.clicked.connect(self._toggle_visibility)
        self.remove_button = QPushButton('Remove')
        self.remove_button.clicked.connect(self._remove_instance)
        self.reset_button = QPushButton('Reset')
        self.reset_button.clicked.connect(self._reset_defaults)
        self.pane_combo = QComboBox()
        self.pane_combo.currentTextChanged.connect(self._pane_changed)
        controls_layout.addWidget(self.visibility_button)
        controls_layout.addWidget(self.remove_button)
        controls_layout.addWidget(self.reset_button)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.pane_combo)
        layout.addWidget(self._controls_row)

        layout.addWidget(QLabel('Parameters'))
        self.params_container = QWidget()
        self.params_layout = QFormLayout(self.params_container)
        self.params_layout.setContentsMargins(0, 0, 0, 0)
        self.params_layout.setSpacing(6)
        layout.addWidget(self.params_container)

        self.params_placeholder = QLabel('Select an indicator to edit parameters.')
        self.params_placeholder.setWordWrap(True)
        layout.addWidget(self.params_placeholder)

        self.setWidget(container)

        self._controls_row.setEnabled(False)
        self.params_container.setVisible(False)

    def set_available_indicators(self, indicators: List[dict]) -> None:
        self._available = {item["indicator_id"]: item for item in indicators}
        self.indicator_list.clear()
        for info in indicators:
            item = QListWidgetItem(info["name"])
            item.setData(Qt.ItemDataRole.UserRole, info["indicator_id"])
            self.indicator_list.addItem(item)

    def set_indicator_instances(self, instances: List[dict], pane_ids: List[str]) -> None:
        self._instances = {item["instance_id"]: item for item in instances}
        self._pane_ids = pane_ids
        self.active_list.clear()
        for info in instances:
            name = info.get("name", info.get("indicator_id", "indicator"))
            pane_label = info.get("pane_id", "price")
            item = QListWidgetItem(f"{name} ({pane_label})")
            item.setData(Qt.ItemDataRole.UserRole, info["instance_id"])
            self.active_list.addItem(item)
        self._refresh_pane_combo()

        if self._active_instance_id and self._active_instance_id in self._instances:
            self._select_instance_in_list(self._active_instance_id)
        elif instances:
            self.active_list.setCurrentRow(0)
        else:
            self._clear_selection()

    def _refresh_pane_combo(self) -> None:
        self.pane_combo.blockSignals(True)
        self.pane_combo.clear()
        for pane_id in self._pane_ids:
            self.pane_combo.addItem(pane_id)
        self.pane_combo.blockSignals(False)

    def _select_instance_in_list(self, instance_id: str) -> None:
        for idx in range(self.active_list.count()):
            item = self.active_list.item(idx)
            if item.data(Qt.ItemDataRole.UserRole) == instance_id:
                self.active_list.setCurrentRow(idx)
                return

    def _on_available_double_clicked(self, item: QListWidgetItem) -> None:
        indicator_id = item.data(Qt.ItemDataRole.UserRole)
        if indicator_id:
            self.indicator_add_requested.emit(str(indicator_id))

    def _on_active_selected(self) -> None:
        item = self.active_list.currentItem()
        if item is None:
            self._clear_selection()
            return
        instance_id = item.data(Qt.ItemDataRole.UserRole)
        if not instance_id or instance_id not in self._instances:
            self._clear_selection()
            return
        self._active_instance_id = instance_id
        instance = self._instances[instance_id]
        self._controls_row.setEnabled(True)
        visible = bool(instance.get("visible", True))
        self.visibility_button.setText("Hide" if visible else "Show")
        self._refresh_pane_combo()
        pane_id = instance.get("pane_id", "price")
        idx = self.pane_combo.findText(pane_id)
        if idx >= 0:
            self.pane_combo.setCurrentIndex(idx)
        self._render_params(instance)
        self.indicator_instance_selected.emit(instance_id)

    def _clear_selection(self) -> None:
        self._active_instance_id = None
        self.params_container.setVisible(False)
        self.params_placeholder.setVisible(True)
        self._controls_row.setEnabled(False)
        self._param_widgets.clear()

    def _render_params(self, instance: dict) -> None:
        schema = instance.get("schema") or {}
        inputs = schema.get("inputs") or {}
        params = instance.get("params") or {}

        while self.params_layout.count():
            item = self.params_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._param_widgets.clear()

        if not inputs:
            self.params_container.setVisible(False)
            self.params_placeholder.setVisible(True)
            return

        for key, spec in inputs.items():
            field_type = spec.get("type", "float")
            label = QLabel(str(spec.get("label") or key))
            widget = self._create_param_widget(field_type, spec, params.get(key, spec.get("default")))
            if widget is None:
                continue
            self.params_layout.addRow(label, widget)
            self._param_widgets[key] = widget

        self.params_container.setVisible(True)
        self.params_placeholder.setVisible(False)

    def _create_param_widget(self, field_type: str, spec: dict, value) -> Optional[object]:
        if field_type == "int":
            widget = QSpinBox()
            widget.setMinimum(int(spec.get("min", -999999)))
            widget.setMaximum(int(spec.get("max", 999999)))
            widget.setValue(int(value) if value is not None else int(spec.get("default", 0)))
            widget.valueChanged.connect(self._emit_params)
            return widget
        if field_type == "float":
            widget = QDoubleSpinBox()
            widget.setMinimum(float(spec.get("min", -1e12)))
            widget.setMaximum(float(spec.get("max", 1e12)))
            step = spec.get("step", 0.1)
            widget.setSingleStep(float(step))
            decimals = 6 if float(step) < 1 else 2
            widget.setDecimals(decimals)
            widget.setValue(float(value) if value is not None else float(spec.get("default", 0.0)))
            widget.valueChanged.connect(self._emit_params)
            return widget
        if field_type == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value) if value is not None else bool(spec.get("default", False)))
            widget.stateChanged.connect(self._emit_params)
            return widget
        if field_type == "select":
            widget = QComboBox()
            options = spec.get("options") or []
            for opt in options:
                widget.addItem(str(opt))
            if value is not None:
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            widget.currentTextChanged.connect(self._emit_params)
            return widget
        if field_type == "color":
            widget = QComboBox()
            colors = spec.get("options") or []
            if colors:
                widget.addItems([str(c) for c in colors])
            else:
                widget.setEditable(True)
                widget.lineEdit().setPlaceholderText("#RRGGBB")
            if value:
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
                elif widget.isEditable():
                    widget.setCurrentText(str(value))
            widget.currentTextChanged.connect(self._emit_params)
            return widget
        return None

    def _emit_params(self) -> None:
        if not self._active_instance_id:
            return
        params = {}
        for key, widget in self._param_widgets.items():
            if isinstance(widget, QSpinBox):
                params[key] = int(widget.value())
            elif isinstance(widget, QDoubleSpinBox):
                params[key] = float(widget.value())
            elif isinstance(widget, QCheckBox):
                params[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                params[key] = widget.currentText()
        self.indicator_params_changed.emit(self._active_instance_id, params)

    def _toggle_visibility(self) -> None:
        if not self._active_instance_id:
            return
        instance = self._instances.get(self._active_instance_id)
        if not instance:
            return
        visible = not bool(instance.get("visible", True))
        self.indicator_visibility_toggled.emit(self._active_instance_id, visible)

    def _remove_instance(self) -> None:
        if not self._active_instance_id:
            return
        self.indicator_remove_requested.emit(self._active_instance_id)

    def _pane_changed(self, pane_id: str) -> None:
        if not self._active_instance_id or not pane_id:
            return
        self.indicator_pane_changed.emit(self._active_instance_id, pane_id)

    def _reset_defaults(self) -> None:
        if not self._active_instance_id:
            return
        self.indicator_reset_requested.emit(self._active_instance_id)
