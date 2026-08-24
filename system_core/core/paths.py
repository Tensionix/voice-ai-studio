from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import subprocess
import sys


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    input: Path
    output: Path
    logs: Path
    report: Path
    workspace: Path
    config: Path
    release: Path
    models: Path
    tools: Path
    runtime: Path
    system_core: Path

    @property
    def db_file(self) -> Path:
        return self.workspace / "jobs.sqlite3"


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent

    for parent in [current, *current.parents]:
        if (parent / "system_core").exists() and (parent / "config").exists():
            return parent

    return Path(__file__).resolve().parents[2]


def get_project_paths(root: Path | None = None) -> ProjectPaths:
    project_root = (root or find_project_root()).resolve()
    return ProjectPaths(
        root=project_root,
        input=project_root / "input",
        output=project_root / "output",
        logs=project_root / "logs",
        report=project_root / "report",
        workspace=project_root / "workspace",
        config=project_root / "config",
        release=project_root / "release",
        models=project_root / "models",
        tools=project_root / "Tools",
        runtime=project_root / "runtime",
        system_core=project_root / "system_core",
    )


def ensure_project_dirs(paths: ProjectPaths) -> None:
    for directory in (
        paths.input,
        paths.output,
        paths.logs,
        paths.report,
        paths.workspace,
        paths.config,
        paths.release,
        paths.models,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return
    subprocess.Popen(["xdg-open", str(path)])
