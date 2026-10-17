"""
Lazy loading: names a module offers without importing their source module when it is imported itself.

    __getattr__ = lazy_attributes(__name__, {"FrameworkManager": "orchestrator.Core.framework"})

`module.FrameworkManager` (and `from module import FrameworkManager`) imports orchestrator.Core.framework on first
use and keeps the value on the module, so later lookups are plain attribute reads (PEP 562). Code inside the module
reads the name with `lazy_value(globals(), name, __getattr__)`, so a value set on the module (a test's stand-in)
wins over the import.
"""

import importlib
import sys
from typing import Any, Callable, Dict


def lazy_attributes(module_name: str, names: Dict[str, str]) -> Callable[[str], Any]:
    """A module-level __getattr__ for `module_name`: each key of `names` comes from the module named by its value,
    imported when the name is first read."""

    def __getattr__(name: str) -> Any:
        source = names.get(name)
        if source is None:
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(source), name)
        setattr(sys.modules[module_name], name, value)
        return value

    return __getattr__


def lazy_value(namespace: Dict[str, Any], name: str, getattr_: Callable[[str], Any]) -> Any:
    """`name` from a module's globals() if it is set there (imported before, or replaced), else imported now."""
    return namespace[name] if name in namespace else getattr_(name)


class LazyModule:
    """A module imported at the first attribute read (`views = LazyModule("orchestrator.Core.mojo.views")`)."""

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attribute: str) -> Any:
        return getattr(importlib.import_module(self._name), attribute)
