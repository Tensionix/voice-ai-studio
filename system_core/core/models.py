"""Canonical transcript data model (TZ section 8).

The JSON document produced from these models is the single source of truth.
Markdown / SRT / VTT / TXT are all generated from it, so any consumer
(Notion, Obsidian, re-export, speaker rename) reads JSON, never the sidecars.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class Segment(BaseModel):
    index: int
    start: float
    end: float
    speaker: Optional[str] = None
    text: str = ""
    confidence: Optional[float] = None


class SourceMeta(BaseModel):
    path: str
    filename: str
    media_type: str = "audio"  # "audio" | "video"
    duration_seconds: float = 0.0
    created_at: Optional[str] = None


class Transcription(BaseModel):
    language: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    diarization: bool = False
    segments: list[Segment] = Field(default_factory=list)


class Metadata(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    summary: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)


class Exports(BaseModel):
    markdown: Optional[str] = None
    srt: Optional[str] = None
    vtt: Optional[str] = None
    txt: Optional[str] = None
    json_path: Optional[str] = Field(default=None, alias="json")

    model_config = {"populate_by_name": True}


class TranscriptDoc(BaseModel):
    schema_version: str = SCHEMA_VERSION
    source: SourceMeta
    transcription: Transcription = Field(default_factory=lambda: Transcription())
    metadata: Metadata = Field(default_factory=Metadata)
    exports: Exports = Field(default_factory=Exports)
    # Optional polished transcript body from the cleanup pass (Letterly-style).
    # Markdown uses it when present; SRT/VTT/TXT always use raw segments.
    cleaned_markdown: Optional[str] = None

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=False)

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for segment in self.transcription.segments:
            if segment.speaker and segment.speaker not in seen:
                seen.append(segment.speaker)
        return seen

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "TranscriptDoc":
        return cls.model_validate(data)
