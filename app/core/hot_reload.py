from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Dict, Iterable, Optional

from PyQt6.QtCore import QCoreApplication, QObject, QThread, QTimer, pyqtSignal, QFileSystemWatcher


def _iter_py_files(root_paths: str | Iterable[str]) -> list[str]:
    paths = [root_paths] if isinstance(root_paths, str) else list(root_paths)
    out: list[str] = []
    for root in paths:
        if not root or not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                if not entry.endswith(".py"):
                    continue
                if entry.startswith("_"):
                    continue
                out.append(os.path.join(root, entry))
        except Exception:
            continue
    out.sort()
    return out


def _stat_sig(path: str) -> str:
    """
    Return a cheap change signature for a file.

    Avoid hashing file contents in a background QThread: repeated I/O while the editor
    is writing can be slow, and on Windows we have seen rare access violations in
    native layers during shutdown. mtime+size is enough to trigger a reload.
    """
    try:
        st = os.stat(path)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        size = int(st.st_size)
    except Exception:
        return ""
    return f"{mtime_ns}:{size}"


@dataclass(frozen=True)
class FileHash:
    path: str
    module_hash: str


class FileHashHotReloadWorker(QThread):
    """
    File change watcher that only scans + hashes .py files.

    Important: it does NOT import/exec modules. Importing Qt/pyqtgraph from a background
    thread can crash the process on Windows (access violation). Actual module loading
    must happen on the UI thread after this emits an update.
    """

    updated = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, watch_paths: str | Iterable[str], poll_interval: float = 1.0) -> None:
        super().__init__()
        self.watch_paths = watch_paths
        self.poll_interval = max(0.2, poll_interval)
        self._running = True
        self._last_hashes: Dict[str, str] = {}

    def run(self) -> None:
        while self._running and not self.isInterruptionRequested():
            try:
                items = self._scan_hashes()
                if (self._running and not self.isInterruptionRequested()) and self._detect_changes(items):
                    self.updated.emit(items)
            except Exception as exc:
                if self._running and not self.isInterruptionRequested():
                    self.error.emit(str(exc))
            self.msleep(int(self.poll_interval * 1000))

    def stop(self) -> None:
        self._running = False
        try:
            self.requestInterruption()
        except Exception:
            pass

    def _scan_hashes(self) -> list[FileHash]:
        out: list[FileHash] = []
        for path in _iter_py_files(self.watch_paths):
            out.append(FileHash(path=path, module_hash=_stat_sig(path)))
        return out

    def _detect_changes(self, items: list[FileHash]) -> bool:
        changed = False
        current: Dict[str, str] = {}
        for it in items:
            current[it.path] = it.module_hash
            if self._last_hashes.get(it.path) != it.module_hash:
                changed = True
        if set(self._last_hashes.keys()) != set(current.keys()):
            changed = True
        self._last_hashes = current
        return changed


def start_watcher(
    root_paths: str | Iterable[str],
    on_change: Callable[[list[FileHash]], None],
    on_error: Callable[[str], None],
    poll_interval: float = 1.0,
) -> Optional[FileHashHotReloadWorker]:
    worker = FileHashHotReloadWorker(root_paths, poll_interval=poll_interval)
    worker.updated.connect(on_change)
    worker.error.connect(on_error)
    # Ensure we stop watchers on app shutdown (avoid threads running during interpreter teardown).
    app = QCoreApplication.instance()
    if app is not None:
        try:
            app.aboutToQuit.connect(worker.stop)
        except Exception:
            pass
    worker.start()
    return worker


class QtFsHotReload(QObject):
    """
    QFileSystemWatcher-based hot reload helper.

    This runs on the UI thread (no extra QThreads) and tends to be more stable on
    Windows than polling in a background thread. It also reacts immediately to saves.
    """

    def __init__(
        self,
        root_paths: str | Iterable[str],
        on_change: Callable[[], None],
        on_error: Callable[[str], None],
        debounce_ms: int = 150,
    ) -> None:
        super().__init__()
        self._root_paths = [root_paths] if isinstance(root_paths, str) else list(root_paths)
        self._on_change = on_change
        self._on_error = on_error
        self._watcher = QFileSystemWatcher(self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(50, int(debounce_ms)))
        self._timer.timeout.connect(self._fire)
        self._watcher.directoryChanged.connect(self._schedule)
        self._watcher.fileChanged.connect(self._schedule)
        self._paths_watched: set[str] = set()
        self._refresh_watches()

        app = QCoreApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.connect(self.stop)
            except Exception:
                pass

    def stop(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            self._watcher.removePaths(list(self._paths_watched))
        except Exception:
            pass
        self._paths_watched.clear()

    def _schedule(self, _path: str) -> None:
        # Re-arm; coalesce rapid saves/atomic writes.
        self._timer.start()

    def _fire(self) -> None:
        # Refresh file watches first (atomic-save patterns remove/recreate files).
        self._refresh_watches()
        try:
            self._on_change()
        except Exception as exc:
            try:
                self._on_error(str(exc))
            except Exception:
                pass

    def _refresh_watches(self) -> None:
        wanted: set[str] = set()
        for root in self._root_paths:
            if not root or not os.path.isdir(root):
                continue
            wanted.add(root)
            for path in _iter_py_files(root):
                wanted.add(path)

        # Add new watches.
        to_add = [p for p in sorted(wanted) if p not in self._paths_watched]
        if to_add:
            try:
                self._watcher.addPaths(to_add)
                self._paths_watched.update(to_add)
            except Exception as exc:
                self._on_error(str(exc))

        # Remove stale watches.
        to_remove = [p for p in list(self._paths_watched) if p not in wanted]
        if to_remove:
            try:
                self._watcher.removePaths(to_remove)
            except Exception:
                pass
            for p in to_remove:
                self._paths_watched.discard(p)


def start_fs_watcher(
    root_paths: str | Iterable[str],
    on_change: Callable[[], None],
    on_error: Callable[[str], None],
    debounce_ms: int = 150,
) -> QtFsHotReload:
    return QtFsHotReload(root_paths, on_change=on_change, on_error=on_error, debounce_ms=debounce_ms)
