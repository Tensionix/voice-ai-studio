"""pyannote diarization — Studio file mode, opt-in (TZ decision 4).

Heavy (torch + pyannote.audio + HF models). Installed via
install/Install-Diarization-GPU.cmd. Assigns each transcript segment the
speaker whose diarization turn overlaps it most.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.credentials import read_api_key
from ..core.models import Segment
from ..core.paths import ProjectPaths
from .base import DiarizationProvider

_DEFAULT_PIPELINE = "pyannote/speaker-diarization-3.1"


class PyannoteDiarizationProvider(DiarizationProvider):
    name = "pyannote"

    def __init__(self, paths: ProjectPaths, pipeline: str = _DEFAULT_PIPELINE) -> None:
        self.paths = paths
        self.pipeline_name = pipeline
        self._pipeline = None

    def _get_pipeline(self):
        if self._pipeline is None:
            try:
                from pyannote.audio import Pipeline  # type: ignore
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "pyannote.audio is not installed. Run install/Install-Diarization-GPU.cmd."
                ) from exc
            token = read_api_key(self.paths, "huggingface") or None
            self._pipeline = Pipeline.from_pretrained(self.pipeline_name, use_auth_token=token)
        return self._pipeline

    def diarize(self, audio_path: Path, segments: list[Segment]) -> list[Segment]:
        pipeline = self._get_pipeline()
        diarization = pipeline(str(audio_path))

        turns = [
            (turn.start, turn.end, f"Speaker {label}")
            for turn, _, label in diarization.itertracks(yield_label=True)
        ]

        for seg in segments:
            seg.speaker = _best_speaker(seg, turns) or seg.speaker
        return segments


def _best_speaker(seg: Segment, turns: list[tuple[float, float, str]]) -> Optional[str]:
    best_label: Optional[str] = None
    best_overlap = 0.0
    for start, end, label in turns:
        overlap = max(0.0, min(seg.end, end) - max(seg.start, start))
        if overlap > best_overlap:
            best_overlap = overlap
            best_label = label
    return best_label
