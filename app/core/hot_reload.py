from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from core.indicator_registry import discover_indicators, IndicatorInfo


@dataclass
class ReloadEvent:
    path: str
    module_hash: str


class IndicatorHotReloadWorker(QThread):
    indicators_updated = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, watch_paths: str | Iterable[str], poll_interval: float = 1.0) -> None:
        super().__init__()
        self.watch_paths = watch_paths
        self.poll_interval = max(0.2, poll_interval)
        self._running = True
        self._last_hashes: Dict[str, str] = {}

    def run(self) -> None:
        while self._running:
            try:
                indicators = discover_indicators(self.watch_paths)
                changed = self._detect_changes(indicators)
                if changed:
                    self.indicators_updated.emit(indicators)
            except Exception as exc:
                self.error.emit(str(exc))
            self.msleep(int(self.poll_interval * 1000))

    def stop(self) -> None:
        self._running = False

    def _detect_changes(self, indicators: list[IndicatorInfo]) -> bool:
        updated = False
        current: Dict[str, str] = {}
        for info in indicators:
            current[info.path] = info.module_hash
            if self._last_hashes.get(info.path) != info.module_hash:
                updated = True
        if set(self._last_hashes.keys()) != set(current.keys()):
            updated = True
        self._last_hashes = current
        return updated


def start_watcher(
    root_paths: str | Iterable[str],
    on_change: Callable[[list[IndicatorInfo]], None],
    on_error: Callable[[str], None],
    poll_interval: float = 1.0,
) -> Optional[IndicatorHotReloadWorker]:
    worker = IndicatorHotReloadWorker(root_paths, poll_interval=poll_interval)
    worker.indicators_updated.connect(on_change)
    worker.error.connect(on_error)
    worker.start()
    return worker
