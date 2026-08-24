"""OpenAI post-processing: title / slug / summary / tags / action items (TZ section 12).

Reads transcript segments + user Context (TZ section 13) and produces metadata
as a strict JSON object. The chosen chat model is a separate role from the STT
model and never transcribes.
"""

from __future__ import annotations

import re
from typing import Optional

from ..core.credentials import require_api_key
from ..core.models import Segment
from ..core.paths import ProjectPaths
from .base import CleanTranscriptResult, MetadataResult, PostprocessOptions, PostprocessProvider
from ._openai_common import call_json_object, call_text
from .model_catalog import DEFAULT_POSTPROCESS_MODEL

_CLEANUP_MAX_CHARS = 16000

_MAX_TRANSCRIPT_CHARS = 24000


def _make_slug(title: str) -> str:
    """Slugify via python-slugify when available; fall back to a simple ASCII slug."""
    try:
        from slugify import slugify  # type: ignore

        return slugify(title)
    except Exception:
        text = re.sub(r"[^\w\s-]", "", title.lower(), flags=re.UNICODE).strip()
        return re.sub(r"[\s_]+", "-", text)

_INSTRUCTIONS = (
    "You are an assistant that summarizes transcripts. "
    "Always answer with a single JSON object and nothing else. "
    "Write the title, summary, tags and action items in the same language as the transcript. "
    "Keep the title concise (max ~80 chars). Tags are short lowercase keywords."
)


def _transcript_text(segments: list[Segment]) -> str:
    lines = []
    for seg in segments:
        speaker = f"{seg.speaker}: " if seg.speaker else ""
        lines.append(f"{speaker}{seg.text}".strip())
    text = "\n".join(line for line in lines if line)
    if len(text) > _MAX_TRANSCRIPT_CHARS:
        head = text[: _MAX_TRANSCRIPT_CHARS // 2]
        tail = text[-_MAX_TRANSCRIPT_CHARS // 2:]
        text = f"{head}\n...\n{tail}"
    return text


def _build_prompt(segments: list[Segment], options: PostprocessOptions) -> str:
    wanted = []
    if options.generate_title:
        wanted.append('"title": string')
    if options.generate_summary:
        wanted.append('"summary": string (a few sentences)')
    if options.generate_tags:
        wanted.append('"tags": array of short strings')
    if options.generate_action_items:
        wanted.append('"action_items": array of strings')
    schema = "{ " + ", ".join(wanted) + " }"

    parts = []
    if options.context.strip():
        parts.append(f"Context provided by the user:\n{options.context.strip()}\n")
    parts.append(f"Return a JSON object with this shape: {schema}")
    parts.append("Transcript:\n" + _transcript_text(segments))
    return "\n\n".join(parts)


def _group_turns(segments: list[Segment]) -> list[tuple[Optional[str], str]]:
    """Merge consecutive same-speaker segments into turns."""
    turns: list[tuple[Optional[str], str]] = []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if turns and turns[-1][0] == seg.speaker:
            turns[-1] = (seg.speaker, f"{turns[-1][1]} {text}".strip())
        else:
            turns.append((seg.speaker, text))
    return turns


def _turns_to_text(turns: list[tuple[Optional[str], str]]) -> str:
    lines = []
    for speaker, text in turns:
        lines.append(f"{speaker}: {text}" if speaker else text)
    return "\n\n".join(lines)


def _batch_turns(turns: list[tuple[Optional[str], str]], max_chars: int) -> list[list[tuple]]:
    batches: list[list[tuple]] = []
    current: list[tuple] = []
    size = 0
    for turn in turns:
        tlen = len(turn[1]) + len(turn[0] or "") + 4
        if current and size + tlen > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(turn)
        size += tlen
    if current:
        batches.append(current)
    return batches


class OpenAIPostprocessProvider(PostprocessProvider):
    name = "openai"

    def __init__(self, paths: ProjectPaths, model: Optional[str] = None) -> None:
        self.paths = paths
        self.model = model or DEFAULT_POSTPROCESS_MODEL
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI  # type: ignore

            self._client = OpenAI(api_key=require_api_key(self.paths, "openai"))
        return self._client

    def generate_metadata(
        self, segments: list[Segment], options: PostprocessOptions
    ) -> MetadataResult:
        if not segments:
            return MetadataResult()
        model = options.model or self.model
        prompt = _build_prompt(segments, options)
        data = call_json_object(
            self._get_client(),
            model=model,
            instructions=_INSTRUCTIONS,
            user_prompt=prompt,
        )

        title = (data.get("title") or "").strip() or None
        summary = (data.get("summary") or "").strip() or None
        tags_raw = data.get("tags") or []
        tags = [str(t).strip() for t in tags_raw if str(t).strip()] if isinstance(tags_raw, list) else []
        actions_raw = data.get("action_items") or []
        action_items = (
            [str(a).strip() for a in actions_raw if str(a).strip()]
            if isinstance(actions_raw, list)
            else []
        )
        slug = _make_slug(title) if title else None

        return MetadataResult(
            title=title,
            slug=slug,
            summary=summary,
            tags=tags,
            action_items=action_items,
        )

    def cleanup_text(
        self, segments: list[Segment], options: PostprocessOptions
    ) -> CleanTranscriptResult:
        """Polish the diarized transcript to Letterly-style readable Markdown.

        Operates turn by turn (batched for long inputs). Speaker labels and
        meaning are preserved; raw segments are untouched so subtitles stay exact.
        """
        turns = _group_turns(segments)
        if not turns:
            return CleanTranscriptResult(markdown="")

        model = options.cleanup_model or options.model or self.model
        instructions = options.cleanup_prompt.strip() or (
            "PUNCTUATION FIRST: conservatively restore punctuation, capitalization, "
            "sentence boundaries and paragraphs in the SAME language. Do not "
            "paraphrase, reorder thoughts, replace words, add facts, or shorten the "
            "content. Remove only obvious fillers, false starts and accidental "
            "repetitions. Preserve mixed-language terms, formats and speaker labels. "
            "Return only the corrected text."
        )
        guard = []
        if options.context.strip():
            guard.append(f"Context (do not contradict): {options.context.strip()}")
        if options.hotwords:
            guard.append("Keep these terms verbatim: " + ", ".join(options.hotwords))
        if guard:
            instructions = instructions + "\n\n" + "\n".join(guard)

        client = self._get_client()
        outputs: list[str] = []
        for batch in _batch_turns(turns, _CLEANUP_MAX_CHARS):
            cleaned = call_text(
                client,
                model=model,
                instructions=instructions,
                user_prompt=_turns_to_text(batch),
            )
            outputs.append(cleaned.strip())
        return CleanTranscriptResult(markdown="\n\n".join(outputs).strip())
