"""Split normalized audio into API-sized chunks (TZ section 6, step 6).

Time-based splitting keeps chunks safely under the OpenAI upload limit (25 MB)
and lets multi-hour recordings flow through the same path. A small overlap
reduces words lost at chunk boundaries; the timeline merge accounts for offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.jobs import LogCallback, run_process
from ..core.paths import ProjectPaths
from .extract import BYTES_PER_SECOND
from .ffmpeg_tools import ffmpeg_path

# 25 MB API limit / 32000 B/s ≈ 780 s. Default well under that for safety margin.
DEFAULT_CHUNK_SECONDS = 600.0
DEFAULT_OVERLAP_SECONDS = 0.0


@dataclass
class AudioChunk:
    index: int
    path: Path
    start: float  # absolute offset in the source timeline (seconds)
    end: float


def plan_chunks(
    duration_seconds: float,
    *,
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
) -> list[tuple[int, float, float]]:
    """Return (index, start, end) windows covering [0, duration]."""
    if duration_seconds <= 0:
        return [(0, 0.0, 0.0)]
    chunk_seconds = max(1.0, chunk_seconds)
    overlap_seconds = max(0.0, min(overlap_seconds, chunk_seconds - 0.5))
    step = chunk_seconds - overlap_seconds

    windows: list[tuple[int, float, float]] = []
    index = 0
    start = 0.0
    while start < duration_seconds:
        end = min(start + chunk_seconds, duration_seconds)
        windows.append((index, start, end))
        if end >= duration_seconds:
            break
        index += 1
        start += step
    return windows


def split_audio(
    paths: ProjectPaths,
    wav_path: Path,
    out_dir: Path,
    duration_seconds: float,
    *,
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS,
    overlap_seconds: float = DEFAULT_OVERLAP_SECONDS,
    log: LogCallback | None = None,
) -> list[AudioChunk]:
    """Cut wav_path into chunk WAV files under out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    windows = plan_chunks(
        duration_seconds, chunk_seconds=chunk_seconds, overlap_seconds=overlap_seconds
    )

    # Single chunk fits the whole file: reuse it directly, no re-encode.
    if len(windows) == 1:
        return [AudioChunk(index=0, path=wav_path, start=windows[0][1], end=windows[0][2])]

    chunks: list[AudioChunk] = []
    ffmpeg = ffmpeg_path(paths)
    for index, start, end in windows:
        chunk_path = out_dir / f"chunk_{index:04d}.wav"
        command = [
            ffmpeg, "-y",
            "-ss", f"{start:.3f}",
            "-t", f"{max(0.0, end - start):.3f}",
            "-i", str(wav_path),
            "-c:a", "pcm_s16le",
            str(chunk_path),
        ]
        run_process(command, log=log)
        chunks.append(AudioChunk(index=index, path=chunk_path, start=start, end=end))
    return chunks


def estimated_bytes(seconds: float) -> int:
    return int(seconds * BYTES_PER_SECOND)
