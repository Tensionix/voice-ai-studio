"""Runtime checks for the small Live-dictation dependency set.

The GUI uses this before it creates the resident Live controller.  Importing the
packages (instead of only looking for their module specs) also catches a broken
``sounddevice`` installation where the bundled PortAudio DLL or ``cffi`` cannot
be loaded.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib


LIVE_DEPENDENCIES = ("sounddevice", "websockets")


@dataclass(frozen=True)
class LiveDependencyStatus:
    missing: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.missing

    @property
    def detail(self) -> str:
        return "; ".join(self.errors) if self.errors else ", ".join(self.missing)


def check_live_dependencies() -> LiveDependencyStatus:
    """Import every required package and return a non-throwing status."""
    importlib.invalidate_caches()
    missing: list[str] = []
    errors: list[str] = []
    for module_name in LIVE_DEPENDENCIES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            missing.append(module_name)
            errors.append(f"{module_name}: {exc.__class__.__name__}: {exc}")
    return LiveDependencyStatus(tuple(missing), tuple(errors))
