"""
Convenience package shim.

The app is organized under `app/` and is typically run via `python app/main.py`,
which puts `app/` on `sys.path` so imports like `import core.*` work.

When running tools from the repo root (e.g. `python -m core.strategies.cli`),
`app/` is not on `sys.path`. This shim makes `core.*` resolvable by extending
the package search path to include `app/core`.
"""

from __future__ import annotations

import os

# Make this a namespace-like package that also searches `app/core`.
_HERE = os.path.abspath(os.path.dirname(__file__))
_APP_CORE = os.path.normpath(os.path.join(_HERE, "..", "app", "core"))

if os.path.isdir(_APP_CORE):
    __path__.append(_APP_CORE)  # type: ignore[name-defined]

