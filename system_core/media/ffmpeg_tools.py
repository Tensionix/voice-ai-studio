"""Locate the portable (or system) ffmpeg / ffprobe binaries."""

from __future__ import annotations

from pathlib import Path
import os
import shutil

from ..core.paths import ProjectPaths


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _find(paths: ProjectPaths, name: str) -> str:
    """Prefer the portable Tools/ffmpeg/bin copy, fall back to PATH."""
    portable = paths.tools / "ffmpeg" / "bin" / _exe(name)
    if portable.exists():
        return str(portable)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(
        f"{name} not found. Install the portable build via "
        f"install/Install-Portable-FFmpeg-BtbN.cmd, or add {name} to PATH."
    )


def ffmpeg_path(paths: ProjectPaths) -> str:
    return _find(paths, "ffmpeg")


def ffprobe_path(paths: ProjectPaths) -> str:
    return _find(paths, "ffprobe")
