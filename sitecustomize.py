"""Compatibility aliases for uppercase source-package directories.

The repository uses uppercase source folders while legacy imports may still
refer to their former lowercase package names.  This module is loaded by
Python's site initialization and can also be imported explicitly by entry
points.
"""

from importlib import import_module
from pathlib import Path
import sys
import types

_ROOT = Path(__file__).resolve().parent


def _namespace_alias(name: str, folder: str) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__package__ = name
    module.__path__ = [str(_ROOT / folder)]
    sys.modules[name] = module


for _legacy, _folder in (
    ("camera", "CAMERA"),
    ("connection", "CONNECTION"),
    ("gps", "GPS"),
    ("graph", "GRAPH"),
    ("interface", "INTERFACE"),
    ("tests", "TESTS"),
):
    _namespace_alias(_legacy, _folder)

# CAPTURE has an __init__.py with exported classes, so alias the actual package
# rather than a namespace shell.
if "recording" not in sys.modules:
    sys.modules["recording"] = import_module("CAPTURE")
