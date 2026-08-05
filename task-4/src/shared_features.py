"""Bridge to Task 3's shared feature module — the one source of truth for
rolling-window sensor feature math.

Task 4 does not reimplement ``FeatureComputer``; it imports Task 3's copy
directly from ``task-3/src/features.py``. It cannot do that with a normal
``from ...task-3.src.features import ...`` package import: every task in
this repo names its own package ``src`` (see task-3/src/load_data.py's
docstring for the same collision, one task earlier), so a plain import
would either grab Task 4's own ``src`` or fail outright depending on
import order. Loading the file directly by path under a unique module
name sidesteps the collision without duplicating a single line of the
rolling-window math itself.
"""
from __future__ import annotations

import importlib.util
import sys

from . import config

_MODULE_NAME = "fleetpulse_task3_features"
_FEATURES_PATH = config.TASK3_ROOT / "src" / "features.py"


def _load_task3_features():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    if not _FEATURES_PATH.exists():
        raise FileNotFoundError(
            f"{_FEATURES_PATH} not found. Task 4 serves Task 3's registered "
            "model and reuses Task 3's feature module in place — make sure "
            "task-3/ is present (fresh clone should already have it; it is "
            "tracked in git, not DVC)."
        )
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _FEATURES_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_features = _load_task3_features()

FeatureComputer = _features.FeatureComputer
SENSOR_COLUMNS: list[str] = _features.SENSOR_COLUMNS
DEFAULT_WINDOWS: tuple[int, ...] = _features.DEFAULT_WINDOWS
