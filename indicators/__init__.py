"""
Convenience package shim.

Like `core`, indicators live under `app/indicators`, and the app typically runs with
`app/` on `sys.path`. This shim makes `import indicators.*` work from repo-root tools.
"""

from __future__ import annotations

import os

_HERE = os.path.abspath(os.path.dirname(__file__))
_APP_INDICATORS = os.path.normpath(os.path.join(_HERE, "..", "app", "indicators"))

if os.path.isdir(_APP_INDICATORS):
    __path__.append(_APP_INDICATORS)  # type: ignore[name-defined]

