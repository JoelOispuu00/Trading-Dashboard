from __future__ import annotations

from typing import Dict, List, Optional

from datetime import datetime, timezone

from PyQt6.QtCore import Qt, pyqtSignal, QDateTime
from PyQt6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class StrategyPanel(QDockWidget):
    run_requested = pyqtSignal(str, dict, dict)
    stop_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__("Strategies")
        self.setObjectName("StrategyPanelDock")
        self._strategies: Dict[str, dict] = {}
        self._active_strategy_id: Optional[str] = None
        self._param_widgets: Dict[str, object] = {}
        self._last_resolved_visible: tuple[int, int] | None = None

        container = QWidget()
        layout = QVBoxLayout(container)

        title = QLabel("Available Strategies")
        title.setObjectName("StrategySectionTitle")
        layout.addWidget(title)
        self.strategy_list = QListWidget()
        self.strategy_list.setObjectName("StrategyList")
        self.strategy_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.strategy_list.setMinimumHeight(90)
        self.strategy_list.itemSelectionChanged.connect(self._on_selected)
        layout.addWidget(self.strategy_list)

        params_group = QGroupBox("Parameters")
        params_group.setObjectName("StrategyParamsGroup")
        params_layout_outer = QVBoxLayout(params_group)
        self.params_container = QWidget()
        self.params_container.setObjectName("StrategyParamsForm")
        self.params_layout = QFormLayout(self.params_container)
        self.params_layout.setContentsMargins(0, 0, 0, 0)
        self.params_layout.setSpacing(6)
        params_layout_outer.addWidget(self.params_container)
        layout.addWidget(params_group)

        self.params_placeholder = QLabel("Select a strategy to edit parameters.")
        self.params_placeholder.setWordWrap(True)
        params_layout_outer.addWidget(self.params_placeholder)

        run_group = QGroupBox("Backtest Range")
        run_group.setObjectName("StrategyRangeGroup")
        run_layout = QVBoxLayout(run_group)

        self.use_visible_range = QCheckBox("Use visible range")
        self.use_visible_range.setObjectName("StrategyUseVisibleRange")
        self.use_visible_range.setChecked(True)
        self.use_visible_range.toggled.connect(self._sync_range_controls)
        run_layout.addWidget(self.use_visible_range)

        self.resolved_range_label = QLabel("Resolved range (UTC): n/a")
        self.resolved_range_label.setObjectName("StrategyResolvedRange")
        self.resolved_range_label.setVisible(True)
        run_layout.addWidget(self.resolved_range_label)

        self.start_picker = QDateTimeEdit()
        self.start_picker.setObjectName("StrategyStartPicker")
        self.start_picker.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.start_picker.setCalendarPopup(True)
        self.end_picker = QDateTimeEdit()
        self.end_picker.setObjectName("StrategyEndPicker")
        self.end_picker.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.end_picker.setCalendarPopup(True)
        now = QDateTime.currentDateTime()
        self.start_picker.setDateTime(now.addDays(-7))
        self.end_picker.setDateTime(now)
        self._sync_range_controls(self.use_visible_range.isChecked())

        range_form = QFormLayout()
        range_form.setContentsMargins(0, 0, 0, 0)
        range_form.addRow("Start (UTC)", self.start_picker)
        range_form.addRow("End (UTC)", self.end_picker)
        run_layout.addLayout(range_form)

        presets_row = QHBoxLayout()
        presets_row.setSpacing(6)
        self.preset_1d = QPushButton("1D")
        self.preset_7d = QPushButton("7D")
        self.preset_30d = QPushButton("30D")
        self.preset_90d = QPushButton("90D")
        self.preset_1y = QPushButton("1Y")
        for b in (self.preset_1d, self.preset_7d, self.preset_30d, self.preset_90d, self.preset_1y):
            b.setObjectName("StrategyPresetButton")
            b.clicked.connect(lambda _=False, btn=b: self._apply_preset(btn.text()))
            presets_row.addWidget(b)
        presets_row.addStretch(1)
        run_layout.addLayout(presets_row)

        self.allow_fetch = QCheckBox("Allow fetch missing data")
        self.allow_fetch.setObjectName("StrategyAllowFetch")
        self.allow_fetch.setChecked(True)
        run_layout.addWidget(self.allow_fetch)

        layout.addWidget(run_group)

        config_group = QGroupBox("Run Config")
        config_group.setObjectName("StrategyConfigGroup")
        config_layout = QVBoxLayout(config_group)

        self.warmup_spin = QSpinBox()
        self.warmup_spin.setRange(0, 10000)
        self.warmup_spin.setValue(200)

        self.initial_cash_spin = QDoubleSpinBox()
        self.initial_cash_spin.setRange(0.0, 1e12)
        self.initial_cash_spin.setValue(1000.0)

        self.leverage_spin = QDoubleSpinBox()
        self.leverage_spin.setRange(1.0, 200.0)
        self.leverage_spin.setValue(1.0)

        self.commission_spin = QDoubleSpinBox()
        self.commission_spin.setRange(0.0, 1000.0)
        self.commission_spin.setValue(2.0)
        self.commission_spin.setSuffix(" bps")

        self.slippage_spin = QDoubleSpinBox()
        self.slippage_spin.setRange(0.0, 1000.0)
        self.slippage_spin.setValue(2.0)
        self.slippage_spin.setSuffix(" bps")

        config_form = QFormLayout()
        config_form.addRow("Warmup bars", self.warmup_spin)
        config_form.addRow("Initial cash", self.initial_cash_spin)
        config_form.addRow("Leverage", self.leverage_spin)
        config_form.addRow("Commission", self.commission_spin)
        config_form.addRow("Slippage", self.slippage_spin)
        config_layout.addLayout(config_form)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("StrategyRunStatus")
        config_layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("StrategyRunProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        config_layout.addWidget(self.progress)

        layout.addWidget(config_group)

        btn_row = QHBoxLayout()
        self.run_button = QPushButton("Run Backtest")
        self.run_button.setObjectName("StrategyRunButton")
        self.run_button.clicked.connect(self._emit_run)
        self.stop_button = QPushButton("Cancel")
        self.stop_button.setObjectName("StrategyCancelButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.reset_button = QPushButton("Reset Params")
        self.reset_button.setObjectName("StrategyResetButton")
        self.reset_button.clicked.connect(self._reset_params)
        btn_row.addWidget(self.run_button)
        btn_row.addWidget(self.stop_button)
        btn_row.addWidget(self.reset_button)
        layout.addLayout(btn_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setObjectName("StrategyPanelScroll")
        scroll.setWidget(container)
        self.setWidget(scroll)
        self.params_container.setVisible(False)
        self.params_placeholder.setVisible(True)

    def _sync_range_controls(self, use_visible: bool) -> None:
        # If using the visible window, the date pickers are ignored.
        self.start_picker.setEnabled(not use_visible)
        self.end_picker.setEnabled(not use_visible)
        # Preset buttons are created slightly later in __init__.
        if hasattr(self, "preset_1d"):
            for b in (self.preset_1d, self.preset_7d, self.preset_30d, self.preset_90d, self.preset_1y):
                b.setEnabled(not use_visible)

    def set_strategies(self, strategies: List[dict]) -> None:
        self._strategies = {s["strategy_id"]: s for s in strategies}
        self.strategy_list.clear()
        for info in strategies:
            name = info.get("name", info["strategy_id"])
            load_error = info.get("load_error")
            label = name
            if load_error:
                label = f"{name} (stale)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, info["strategy_id"])
            if load_error:
                item.setToolTip(str(load_error))
            self.strategy_list.addItem(item)
        if strategies:
            self.strategy_list.setCurrentRow(0)

    def _on_selected(self) -> None:
        item = self.strategy_list.currentItem()
        if not item:
            return
        strategy_id = item.data(Qt.ItemDataRole.UserRole)
        if not strategy_id or strategy_id not in self._strategies:
            return
        self._active_strategy_id = strategy_id
        self._render_params(self._strategies[strategy_id])

    def _render_params(self, info: dict) -> None:
        schema = info.get("schema") or {}
        inputs = schema.get("inputs") or {}
        params = info.get("params") or {}

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
            widget = self._create_widget(field_type, spec, params.get(key, spec.get("default")))
            if widget is None:
                continue
            self.params_layout.addRow(label, widget)
            self._param_widgets[key] = widget

        self.params_container.setVisible(True)
        self.params_placeholder.setVisible(False)

    def _create_widget(self, field_type: str, spec: dict, value):
        if field_type == "int":
            widget = QSpinBox()
            widget.setRange(int(spec.get("min", -999999)), int(spec.get("max", 999999)))
            widget.setValue(int(value) if value is not None else int(spec.get("default", 0)))
            return widget
        if field_type == "float":
            widget = QDoubleSpinBox()
            widget.setRange(float(spec.get("min", -1e12)), float(spec.get("max", 1e12)))
            widget.setValue(float(value) if value is not None else float(spec.get("default", 0.0)))
            return widget
        if field_type == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value) if value is not None else bool(spec.get("default", False)))
            return widget
        if field_type == "select":
            from PyQt6.QtWidgets import QComboBox
            widget = QComboBox()
            options = spec.get("options") or []
            for opt in options:
                widget.addItem(str(opt))
            if value is not None:
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)
            return widget
        return None

    def _collect_params(self) -> dict:
        params = {}
        for key, widget in self._param_widgets.items():
            if isinstance(widget, QSpinBox):
                params[key] = int(widget.value())
            elif isinstance(widget, QDoubleSpinBox):
                params[key] = float(widget.value())
            elif isinstance(widget, QCheckBox):
                params[key] = widget.isChecked()
            else:
                try:
                    params[key] = widget.currentText()
                except Exception:
                    pass
        return params

    def _emit_run(self) -> None:
        if not self._active_strategy_id:
            return
        params = self._collect_params()
        run_cfg = {
            "use_visible_range": bool(self.use_visible_range.isChecked()),
            "start_ts": int(self.start_picker.dateTime().toSecsSinceEpoch() * 1000),
            "end_ts": int(self.end_picker.dateTime().toSecsSinceEpoch() * 1000),
            "warmup_bars": int(self.warmup_spin.value()),
            "initial_cash": float(self.initial_cash_spin.value()),
            "leverage": float(self.leverage_spin.value()),
            "commission_bps": float(self.commission_spin.value()),
            "slippage_bps": float(self.slippage_spin.value()),
            "allow_fetch": bool(self.allow_fetch.isChecked()),
        }
        self.set_running(True)
        self.set_status("Starting...")
        self.set_progress(0, 1)
        self.run_requested.emit(self._active_strategy_id, params, run_cfg)

    def _reset_params(self) -> None:
        if not self._active_strategy_id:
            return
        info = self._strategies.get(self._active_strategy_id)
        if not info:
            return
        self._render_params(info)

    def set_running(self, running: bool) -> None:
        self.run_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.reset_button.setEnabled(not running)
        self.strategy_list.setEnabled(not running)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_progress(self, i: int, n: int) -> None:
        try:
            if n <= 0:
                self.progress.setValue(0)
                return
            pct = int(max(0.0, min(1.0, float(i) / float(n))) * 100.0)
            self.progress.setValue(pct)
        except Exception:
            pass

    def set_resolved_visible_range(self, ts_min: int, ts_max: int) -> None:
        if self._last_resolved_visible == (ts_min, ts_max):
            return
        self._last_resolved_visible = (ts_min, ts_max)
        if not self.use_visible_range.isChecked():
            return
        self.resolved_range_label.setText(
            f"Resolved range (UTC): {self._fmt_utc(ts_min)}  ->  {self._fmt_utc(ts_max)}"
        )

    def _apply_preset(self, label: str) -> None:
        # Presets apply in UTC by setting relative offsets from "now" (local clock), then UI labels clarify UTC.
        # This keeps the control simple while the engine uses epoch ms (UTC).
        now = QDateTime.currentDateTime()
        if label == "1D":
            self.start_picker.setDateTime(now.addDays(-1))
        elif label == "7D":
            self.start_picker.setDateTime(now.addDays(-7))
        elif label == "30D":
            self.start_picker.setDateTime(now.addDays(-30))
        elif label == "90D":
            self.start_picker.setDateTime(now.addDays(-90))
        elif label == "1Y":
            self.start_picker.setDateTime(now.addDays(-365))
        self.end_picker.setDateTime(now)

    @staticmethod
    def _fmt_utc(ts_ms: int) -> str:
        try:
            dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts_ms)
