"""Shared fixtures for Task 10's pytest suite.

Same by-path import trick ``task-8/tests/conftest.py`` already uses:
every task folder in this repo names its own package ``src``, so a normal
``import`` would clobber ``sys.modules["src"]`` across tasks.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def import_task_module(task_dir: str, module_name: str):
    task_path = REPO_ROOT / task_dir
    sys.path.insert(0, str(task_path))
    try:
        return importlib.import_module(f"src.{module_name}")
    finally:
        sys.path.remove(str(task_path))
        for name in list(sys.modules):
            if name == "src" or name.startswith("src."):
                del sys.modules[name]
