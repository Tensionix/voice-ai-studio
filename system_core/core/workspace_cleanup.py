"""Safe cleanup of per-source pipeline intermediates under ``workspace``."""

from __future__ import annotations

import shutil
from pathlib import Path

from .paths import ProjectPaths


def source_work_dir(paths: ProjectPaths, source: Path) -> Path:
    return paths.workspace / Path(source).stem


def remove_source_workspace(paths: ProjectPaths, source: Path) -> bool:
    """Remove only the source-specific directory, never the workspace root."""
    root = paths.workspace.resolve()
    target = source_work_dir(paths, source).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError(f"Unsafe workspace cleanup target: {target}")
    if not target.exists():
        return False
    if not target.is_dir():
        raise ValueError(f"Workspace target is not a directory: {target}")
    shutil.rmtree(target)
    return True
