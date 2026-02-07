from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import os
from typing import Dict, Iterable, List, Optional


@dataclass
class IndicatorInfo:
    indicator_id: str
    name: str
    inputs: Dict[str, dict]
    pane: str
    path: str
    module_hash: str
    module: object
    load_error: Optional[str] = None


# Keep last-good indicator definition per file path so hot reload doesn't drop indicators on syntax errors.
_LAST_GOOD_BY_PATH: Dict[str, IndicatorInfo] = {}


def discover_indicators(root_paths: str | Iterable[str]) -> List[IndicatorInfo]:
    indicators: List[IndicatorInfo] = []
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
            module, mod_err = _load_module_from_path(path)
            schema = _safe_schema(module) if module is not None else None
            if module is None or not schema:
                last_good = _LAST_GOOD_BY_PATH.get(path)
                if last_good is not None:
                    indicators.append(
                        IndicatorInfo(
                            indicator_id=last_good.indicator_id,
                            name=last_good.name,
                            inputs=last_good.inputs,
                            pane=last_good.pane,
                            path=last_good.path,
                            module_hash=last_good.module_hash,
                            module=last_good.module,
                            load_error=(mod_err or "schema/load failed"),
                        )
                    )
                continue
            indicator_id = str(schema.get("id") or os.path.splitext(entry)[0])
            name = str(schema.get("name") or indicator_id)
            inputs = schema.get("inputs") or {}
            pane = str(schema.get("pane") or "price")
            module_hash = _hash_file(path)
            out = IndicatorInfo(
                indicator_id=indicator_id,
                name=name,
                inputs=inputs,
                pane=pane,
                path=path,
                module_hash=module_hash,
                module=module,
                load_error=None,
            )
            _LAST_GOOD_BY_PATH[path] = out
            indicators.append(out)

    indicators.sort(key=lambda info: info.name.lower())
    return indicators


def reload_indicator(info: IndicatorInfo) -> Optional[IndicatorInfo]:
    module, mod_err = _load_module_from_path(info.path)
    schema = _safe_schema(module) if module is not None else None
    if module is None or not schema:
        last_good = _LAST_GOOD_BY_PATH.get(info.path)
        if last_good is not None:
            return IndicatorInfo(
                indicator_id=last_good.indicator_id,
                name=last_good.name,
                inputs=last_good.inputs,
                pane=last_good.pane,
                path=last_good.path,
                module_hash=last_good.module_hash,
                module=last_good.module,
                load_error=(mod_err or "schema/load failed"),
            )
        return None
    indicator_id = str(schema.get("id") or info.indicator_id)
    name = str(schema.get("name") or info.name)
    inputs = schema.get("inputs") or {}
    pane = str(schema.get("pane") or info.pane)
    module_hash = _hash_file(info.path)
    out = IndicatorInfo(
        indicator_id=indicator_id,
        name=name,
        inputs=inputs,
        pane=pane,
        path=info.path,
        module_hash=module_hash,
        module=module,
        load_error=None,
    )
    _LAST_GOOD_BY_PATH[info.path] = out
    return out


def _load_module_from_path(path: str) -> tuple[Optional[object], Optional[str]]:
    try:
        spec = importlib.util.spec_from_file_location(f"indicator_{os.path.basename(path)}", path)
        if spec is None or spec.loader is None:
            return None, "no spec/loader"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _safe_schema(module: object) -> Optional[Dict[str, dict]]:
    try:
        schema_fn = getattr(module, "schema", None)
        if schema_fn is None:
            return None
        schema = schema_fn()
        if not isinstance(schema, dict):
            return None
        return schema
    except Exception:
        return None


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
