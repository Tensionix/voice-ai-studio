"""ffprobe media inspection (TZ section 6, step 3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

from ..core.jobs import run_capture
from ..core.paths import ProjectPaths
from .ffmpeg_tools import ffprobe_path

AUDIO_EXTS = {
    ".aac", ".ac3", ".aif", ".aiff", ".amr", ".ape", ".caf", ".flac", ".m4a", ".mka",
    ".mp2", ".mp3", ".ogg", ".opus", ".wav", ".wma", ".wv",
}
VIDEO_EXTS = {
    ".3g2", ".3gp", ".avi", ".divx", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".ogv", ".ts", ".vob", ".webm", ".wmv",
}
SUPPORTED_EXTS = AUDIO_EXTS | VIDEO_EXTS


@dataclass
class MediaInfo:
    path: Path
    media_type: str  # "audio" | "video"
    duration_seconds: float
    has_audio: bool
    raw: dict[str, Any]


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


def supported_extensions() -> list[str]:
    return sorted(SUPPORTED_EXTS)


def supported_extensions_label() -> str:
    return ", ".join(ext[1:].upper() for ext in supported_extensions())


def probe_media(paths: ProjectPaths, source: Path) -> MediaInfo:
    if not source.exists():
        raise RuntimeError(f"Input file does not exist: {source}")
    if not is_supported(source):
        raise RuntimeError(f"Unsupported file type: {source.suffix}")

    command = [
        ffprobe_path(paths),
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]
    result = run_capture(command)
    if result.exit_code != 0 or not result.text.strip():
        raise RuntimeError(f"ffprobe failed for {source.name}")

    try:
        data = json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {source.name}: {exc}") from exc

    streams = data.get("streams", []) or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" for s in streams)
    if not has_audio:
        raise RuntimeError(f"No audio stream found in {source.name}")

    fmt = data.get("format", {}) or {}
    duration = 0.0
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0.0:
        for stream in streams:
            if stream.get("codec_type") == "audio":
                try:
                    duration = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    duration = 0.0
                if duration > 0.0:
                    break

    media_type = "video" if (has_video and source.suffix.lower() in VIDEO_EXTS) else "audio"
    return MediaInfo(
        path=source,
        media_type=media_type,
        duration_seconds=duration,
        has_audio=has_audio,
        raw=data,
    )
