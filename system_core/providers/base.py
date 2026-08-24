"""Provider abstraction layer (TZ section 12).

UI and pipeline never bind to a concrete model or vendor. Compute mode
(api / cpu / gpu) selects which implementations the registry wires in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.models import Segment
from ..core.terms import build_stt_prompt


@dataclass
class TranscriptionOptions:
    language: Optional[str] = None          # None / "auto" -> auto-detect
    model: Optional[str] = None             # resolved by registry/config if None
    diarize: bool = False                   # request speaker labels
    context: str = ""                       # narrative topic/participants/purpose
    keyterms: tuple[str, ...] = ()          # exact spellings; provider support varies
    vad_filter: bool = True                 # local engines only
    temperature: float = 0.0

    def prompt_context(self, *, max_chars: int = 1000) -> str:
        return build_stt_prompt(self.context, self.keyterms, max_chars=max_chars)


@dataclass
class TranscriptResult:
    segments: list[Segment]
    language: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    diarization: bool = False


@dataclass
class MetadataResult:
    title: Optional[str] = None
    slug: Optional[str] = None
    summary: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    action_items: list[str] = field(default_factory=list)


@dataclass
class CleanTranscriptResult:
    # Polished, speaker-labelled, paragraphed transcript body (Markdown-ready).
    # Raw timestamped segments stay canonical and drive subtitles separately.
    markdown: str


@dataclass
class PostprocessOptions:
    model: Optional[str] = None
    context: str = ""
    language: Optional[str] = None
    generate_title: bool = True
    generate_summary: bool = True
    generate_tags: bool = True
    generate_action_items: bool = False
    # Cleanup pass (separate toggle): own model + editable prompt + hotwords.
    cleanup_model: Optional[str] = None
    cleanup_prompt: str = ""
    hotwords: list[str] = field(default_factory=list)


class TranscriptionProvider(ABC):
    name: str = "base"

    @abstractmethod
    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        ...


class PostprocessProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate_metadata(
        self, segments: list[Segment], options: PostprocessOptions
    ) -> MetadataResult:
        ...

    def cleanup_text(
        self, segments: list[Segment], options: PostprocessOptions
    ) -> CleanTranscriptResult:
        raise NotImplementedError("cleanup_text not implemented for this provider")


class DiarizationProvider(ABC):
    name: str = "base"

    @abstractmethod
    def diarize(self, audio_path: Path, segments: list[Segment]) -> list[Segment]:
        """Return segments annotated with speaker labels."""
        ...


class LiveRegionBlockedError(RuntimeError):
    """The realtime endpoint refused the caller's region (HTTP 403 geo-block).

    OpenAI blocks unsupported countries at the edge; the REST API is blocked the
    same way, so the user typically just needs their VPN on (as for batch)."""


@dataclass
class LiveOptions:
    """Streaming STT options for Live dictation (iteration 3)."""
    language: Optional[str] = None          # None / "auto" -> auto-detect
    model: Optional[str] = None             # resolved by registry/config if None
    context: str = ""                       # free-text hint / hotwords
    keyterms: tuple[str, ...] = ()          # exact vocabulary bias for cloud STT
    sample_rate: int = 16000                # PCM frames fed by audio_capture
    vad_filter: bool = True                 # local engines only


@dataclass
class LiveChunkResult:
    """One push-to-talk utterance result (partial during, final on finish)."""
    text: str
    is_final: bool = True


class LiveTranscriber(ABC):
    """Streaming STT seam for Live dictation (iteration 3).

    Cloud first (OpenAI Realtime over WebSocket); a local seam (faster-whisper
    + Silero VAD) plugs in later behind the same interface. The Live controller
    opens a session per utterance, feeds raw PCM frames captured from the mic,
    then reads partials and the final transcript. Implementations live next to
    the file providers (`realtime_provider.py`, future `faster_whisper_live.py`)
    and are lazy-imported so the base env stays light.
    """

    name: str = "base"
    # True when the implementation wants PCM frames pushed live during capture
    # (and emits partials via `on_partial`); False = batch (fed once on release).
    streaming: bool = False
    # Optional callback invoked with the running partial transcript as it grows.
    # Called from a background reader thread — the controller marshals it to the
    # GUI thread. Set by the controller before `start()`.
    on_partial: "Optional[callable]" = None
    # Optional callback invoked with each *completed* segment's final text (used
    # for incremental paste in latched/continuous mode). Reader-thread; marshalled.
    on_segment: "Optional[callable]" = None

    @abstractmethod
    def start(self, options: LiveOptions) -> None:
        """Open a streaming session for one utterance."""
        ...

    @abstractmethod
    def feed(self, pcm: bytes) -> Optional[LiveChunkResult]:
        """Push a PCM frame; may return a partial transcript or None."""
        ...

    @abstractmethod
    def finish(self) -> LiveChunkResult:
        """Close the session and return the final transcript."""
        ...

    def close(self) -> None:
        """Release any resources (idempotent)."""
        return None
