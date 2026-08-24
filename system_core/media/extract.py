"""Extract & normalize audio to a transcription-friendly WAV (TZ section 6, steps 4-5).

Output: 16 kHz mono PCM s16le. This is the canonical format Whisper/OpenAI
expect and keeps chunk math simple (32000 bytes/sec).
"""

from __future__ import annotations

from pathlib import Path

from ..core.jobs import LogCallback, run_process
from ..core.paths import ProjectPaths
from .ffmpeg_tools import ffmpeg_path

TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
BYTES_PER_SECOND = TARGET_SAMPLE_RATE * TARGET_CHANNELS * 2  # s16le = 2 bytes/sample


def extract_audio(
    paths: ProjectPaths,
    source: Path,
    dest_wav: Path,
    *,
    normalize: bool = True,
    log: LogCallback | None = None,
) -> Path:
    """Decode any supported media to 16k mono WAV at dest_wav."""
    dest_wav.parent.mkdir(parents=True, exist_ok=True)

    filters = []
    if normalize:
        # Loudness normalization improves recognition consistency.
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

    command = [
        ffmpeg_path(paths),
        "-y",
        "-i", str(source),
        "-vn",
        "-ac", str(TARGET_CHANNELS),
        "-ar", str(TARGET_SAMPLE_RATE),
    ]
    if filters:
        command += ["-af", ",".join(filters)]
    command += ["-c:a", "pcm_s16le", str(dest_wav)]

    run_process(command, log=log)
    if not dest_wav.exists() or dest_wav.stat().st_size == 0:
        raise RuntimeError(f"Audio extraction produced no output for {source.name}")
    return dest_wav
