from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import os
from typing import Dict, Iterable, List, Optional

from core.hot_reload import start_watcher, FileHashHotReloadWorker
from core.strategies.schema import validate_schema


@dataclass
class StrategyInfo:
    strategy_id: str
    name: str
    inputs: Dict[str, dict]
    path: str
    module_hash: str
    module: object
    load_error: Optional[str] = None


_LAST_GOOD_BY_PATH: Dict[str, StrategyInfo] = {}


def discover_strategies(root_paths: str | Iterable[str]) -> List[StrategyInfo]:
    strategies: List[StrategyInfo] = []
    paths = [root_paths] if isinstance(root_paths, str) else list(root_paths)

    for root_path in paths:
        if not root_path or not os.path.isdir(root_path):
            continue
        for entry in os.listdir(root_path):
            if not entry.endswith(".py"):
                continue
            if entry.startswith("_"):
                continue
            path = os.path.join(root_path, entry)
            module, load_err = _try_load_module_from_path(path)
            if module is None:
                # Keep last good version if the new version fails to load.
                last = _LAST_GOOD_BY_PATH.get(path)
                if last is not None:
                    strategies.append(
                        StrategyInfo(
                            strategy_id=last.strategy_id,
                            name=last.name,
                            inputs=last.inputs,
                            path=last.path,
                            module_hash=last.module_hash,
                            module=last.module,
                            load_error=load_err or "load_failed",
                        )
                    )
                continue
            schema, schema_err = _safe_schema(module)
            if not schema:
                last = _LAST_GOOD_BY_PATH.get(path)
                if last is not None:
                    strategies.append(
                        StrategyInfo(
                            strategy_id=last.strategy_id,
                            name=last.name,
                            inputs=last.inputs,
                            path=last.path,
                            module_hash=last.module_hash,
                            module=last.module,
                            load_error=schema_err or "schema_failed",
                        )
                    )
                continue
            ok, err = validate_schema(schema)
            if not ok:
                last = _LAST_GOOD_BY_PATH.get(path)
                if last is not None:
                    strategies.append(
                        StrategyInfo(
                            strategy_id=last.strategy_id,
                            name=last.name,
                            inputs=last.inputs,
                            path=last.path,
                            module_hash=last.module_hash,
                            module=last.module,
                            load_error=str(err or "invalid_schema"),
                        )
                    )
                continue
            strategy_id = str(schema.get("id") or os.path.splitext(entry)[0])
            name = str(schema.get("name") or strategy_id)
            inputs = schema.get("inputs") or {}
            module_hash = _hash_file(path)
            info = StrategyInfo(
                strategy_id=str(strategy_id),
                name=name,
                inputs=inputs,
                path=path,
                module_hash=module_hash,
                module=module,
                load_error=None,
            )
            _LAST_GOOD_BY_PATH[path] = info
            strategies.append(info)

    strategies.sort(key=lambda info: info.name.lower())
    return strategies


def start_strategy_watcher(
    root_paths: str | Iterable[str],
    on_change,
    on_error,
    poll_interval: float = 1.0,
) -> Optional[FileHashHotReloadWorker]:
    # Only watch file hashes on the worker thread. Strategy discovery/import must run on UI thread.
    return start_watcher(root_paths, on_change, on_error, poll_interval=poll_interval)


def _load_module_from_path(path: str) -> Optional[object]:
    spec = importlib.util.spec_from_file_location(f"strategy_{os.path.basename(path)}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _try_load_module_from_path(path: str) -> tuple[Optional[object], Optional[str]]:
    try:
        module = _load_module_from_path(path)
        if module is None:
            return None, "load_failed"
        return module, None
    except Exception as exc:
        return None, str(exc)


def _safe_schema(module: object) -> tuple[Optional[Dict[str, dict]], Optional[str]]:
    try:
        schema_fn = getattr(module, "schema", None)
        if schema_fn is None:
            return None, "missing_schema"
        schema = schema_fn()
        if not isinstance(schema, dict):
            return None, "schema_not_dict"
        return schema, None
    except Exception as exc:
        return None, str(exc)


def _hash_file(path: str) -> str:
    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
    except Exception:
        return ""
    return hasher.hexdigest()
