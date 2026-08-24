"""File pipeline orchestrator (TZ section 6).

Per file: validate -> probe -> extract -> chunk -> transcribe (resume cached
chunks) -> merge timeline -> diarize (if separate) -> metadata -> write selected
sidecars atomically -> mark job. A failure on one file is isolated (TZ section
15): the job is marked failed and the queue continues.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.logging_utils import timestamp
from ..core.models import Metadata, Segment, SourceMeta, Transcription, TranscriptDoc
from ..core.paths import ProjectPaths
from ..core.persistence import JobStore
from ..core.prompt_store import ROLE_CLEANUP, PromptStore
from ..core.terms import shared_keyterms
from ..media.chunk import plan_chunks, split_audio
from ..media.extract import extract_audio
from ..media.probe import is_supported, probe_media
from ..providers import registry
from ..providers.base import PostprocessOptions, TranscriptionOptions
from ..writers.json_writer import write_json
from ..writers.markdown_writer import write_markdown
from ..writers.srt_writer import write_srt
from ..writers.subtitle_format import SubtitleSettings
from ..writers.txt_writer import write_txt
from ..writers.vtt_writer import write_vtt
from .timeline import merge_chunk_segments

Logger = Callable[[str], None]
Canceller = Callable[[], bool]


def _noop(_: str) -> None:
    pass


def sidecar_paths(
    source: Path, settings: dict[str, Any], paths: ProjectPaths | None = None
) -> dict[str, Path]:
    """Resolve transcript sidecar paths.

    By default sidecars stay next to the source for backward compatibility. When
    a ProjectPaths object is supplied, `pipeline.save_next_to_source: false`
    routes outputs to the app's central output folder.
    """
    pipeline_cfg = settings.get("pipeline", {}) if isinstance(settings, dict) else {}
    save_next_to_source = bool(pipeline_cfg.get("save_next_to_source", True))
    base_dir = source.parent if save_next_to_source or paths is None else paths.output
    stem = source.stem
    return {
        "json": base_dir / f"{stem}.transcript.json",
        "markdown": base_dir / f"{stem}.md",
        "srt": base_dir / f"{stem}.srt",
        "vtt": base_dir / f"{stem}.vtt",
        "txt": base_dir / f"{stem}.txt",
    }


def _selected_outputs(settings: dict[str, Any]) -> list[str]:
    exports = settings.get("exports", {})
    keys = []
    if exports.get("json", True):
        keys.append("json")
    if exports.get("markdown", True):
        keys.append("markdown")
    if exports.get("srt", False):
        keys.append("srt")
    if exports.get("vtt", False):
        keys.append("vtt")
    if exports.get("txt", False):
        keys.append("txt")
    return keys


def outputs_exist(
    source: Path, settings: dict[str, Any], paths: ProjectPaths | None = None
) -> bool:
    paths = sidecar_paths(source, settings, paths)
    selected = _selected_outputs(settings)
    return bool(selected) and all(paths[key].exists() for key in selected)


def _subtitle_settings(settings: dict[str, Any]) -> SubtitleSettings:
    sub = settings.get("subtitles", {})
    return SubtitleSettings(
        max_chars_per_line=int(sub.get("max_chars_per_line", 42)),
        max_lines=int(sub.get("max_lines", 2)),
        min_duration=float(sub.get("min_duration", 1.0)),
        max_duration=float(sub.get("max_duration", 7.0)),
        include_speakers=bool(sub.get("include_speakers", False)),
    )


def _effective_chunk_seconds(
    paths: ProjectPaths, settings: dict[str, Any], pipeline_cfg: dict[str, Any]
) -> float:
    chunk_seconds = float(pipeline_cfg.get("chunk_seconds", 600))
    try:
        mode = registry.resolved_compute_mode(paths, settings)
        engine = registry.local_engine(settings, scope="file")
    except Exception:
        return chunk_seconds

    if mode == registry.MODE_VULKAN and engine == "gigaam":
        diar_cfg = settings.get("diarization", {})
        local_chunk_seconds = float(diar_cfg.get("gigaam_chunk_seconds", 45))
        return max(1.0, min(chunk_seconds, local_chunk_seconds))
    return chunk_seconds


def _transcribe_chunks(
    paths: ProjectPaths,
    settings: dict[str, Any],
    chunks,
    store: Optional[JobStore],
    job_id: Optional[int],
    options: TranscriptionOptions,
    log: Logger,
    cancel: Canceller,
) -> tuple[list[tuple[float, list[Segment]]], Optional[str], Optional[str], bool]:
    provider = registry.get_transcription_provider(paths, settings)
    collected: list[tuple[float, list[Segment]]] = []
    language: Optional[str] = None
    model: Optional[str] = None
    diarized = False

    for chunk in chunks:
        if cancel():
            raise RuntimeError("Operation cancelled by user.")

        cached = None
        if store and job_id is not None:
            store.upsert_chunk(job_id, chunk.index, chunk.start, chunk.end)
            cached = store.get_chunk_transcript(job_id, chunk.index)

        if cached is not None:
            log(f"[chunk {chunk.index}] resumed from cache")
            segments = [Segment(**s) for s in cached.get("segments", [])]
            language = language or cached.get("language")
            model = model or cached.get("model")
            diarized = diarized or any(seg.speaker for seg in segments)
            collected.append((chunk.start, segments))
            continue

        log(f"[chunk {chunk.index}] transcribing {chunk.start:.0f}-{chunk.end:.0f}s")
        try:
            result = provider.transcribe(chunk.path, options)
        except Exception as exc:
            if store and job_id is not None:
                store.fail_chunk(job_id, chunk.index, str(exc))
            raise

        language = language or result.language
        model = model or result.model
        diarized = diarized or result.diarization
        collected.append((chunk.start, result.segments))

        if store and job_id is not None:
            store.save_chunk_transcript(
                job_id,
                chunk.index,
                {
                    "language": result.language,
                    "model": result.model,
                    "segments": [s.model_dump() for s in result.segments],
                },
            )

    return collected, language, model, diarized


def process_file(
    paths: ProjectPaths,
    source: Path,
    settings: dict[str, Any],
    *,
    store: Optional[JobStore] = None,
    job_id: Optional[int] = None,
    log: Logger = _noop,
    cancel: Canceller = lambda: False,
) -> dict[str, str]:
    """Run the full pipeline for one file and return written output paths."""
    source = source.resolve()
    if not source.exists():
        raise RuntimeError(f"Input file does not exist: {source}")
    if not is_supported(source):
        raise RuntimeError(f"Unsupported file type: {source.suffix}")

    log(f"Probing {source.name}")
    info = probe_media(paths, source)

    work_dir = paths.workspace / source.stem
    work_dir.mkdir(parents=True, exist_ok=True)
    wav_path = work_dir / "audio.wav"

    pipeline_cfg = settings.get("pipeline", {})
    log("Extracting audio")
    extract_audio(
        paths, source, wav_path,
        normalize=bool(pipeline_cfg.get("normalize_audio", True)),
        log=log,
    )

    chunk_seconds = _effective_chunk_seconds(paths, settings, pipeline_cfg)
    if chunk_seconds < float(pipeline_cfg.get("chunk_seconds", 600)):
        log(f"GigaAM diarization chunking: {chunk_seconds:.0f}s")

    chunks = split_audio(
        paths, wav_path, work_dir / "chunks", info.duration_seconds,
        chunk_seconds=chunk_seconds,
        overlap_seconds=float(pipeline_cfg.get("overlap_seconds", 0)),
        log=log,
    )
    log(f"Split into {len(chunks)} chunk(s)")

    options = TranscriptionOptions(
        language=settings.get("language", "auto"),
        model=settings.get("stt", {}).get("model"),
        diarize=bool(settings.get("transcription", {}).get("diarize", False)),
        context=settings.get("context", ""),
        keyterms=shared_keyterms(settings),
        vad_filter=True,
    )

    chunk_segments, language, model, diarized = _transcribe_chunks(
        paths, settings, chunks, store, job_id, options, log, cancel
    )

    segments = merge_chunk_segments(chunk_segments)
    log(f"Merged timeline: {len(segments)} segment(s)")

    # Separate diarization step (Studio local file modes: CUDA/GigaAM/whisper.cpp).
    diar_provider = registry.get_diarization_provider(paths, settings)
    if diar_provider is not None and segments:
        log("Running diarization")
        segments = diar_provider.diarize(wav_path, segments)
        diarized = True

    doc = TranscriptDoc(
        source=SourceMeta(
            path=str(source),
            filename=source.name,
            media_type=info.media_type,
            duration_seconds=info.duration_seconds,
            created_at=timestamp(),
        ),
        transcription=Transcription(
            language=language or (None if settings.get("language") == "auto" else settings.get("language")),
            model=model,
            provider=registry.get_transcription_provider(paths, settings).name,
            diarization=diarized,
            segments=segments,
        ),
        metadata=Metadata(),
    )

    _maybe_cleanup(paths, settings, doc, log)
    _maybe_generate_metadata(paths, settings, doc, log)

    outputs = _write_outputs(paths, source, settings, doc, log)
    _maybe_export_connectors(paths, settings, doc, source, log)

    if not bool(pipeline_cfg.get("keep_workspace", False)):
        shutil.rmtree(work_dir, ignore_errors=True)

    return outputs


def _maybe_generate_metadata(
    paths: ProjectPaths, settings: dict[str, Any], doc: TranscriptDoc, log: Logger
) -> None:
    pp = settings.get("postprocessing", {})
    wants = any(
        pp.get(flag, False)
        for flag in ("generate_title", "generate_summary", "generate_tags", "generate_action_items")
    )
    if not wants or not doc.transcription.segments:
        return

    log("Generating metadata (title/summary/tags)")
    provider = registry.get_postprocess_provider(paths, settings)
    options = PostprocessOptions(
        model=settings.get("postprocess", {}).get("model"),
        context=settings.get("context", ""),
        language=doc.transcription.language,
        generate_title=bool(pp.get("generate_title", True)),
        generate_summary=bool(pp.get("generate_summary", True)),
        generate_tags=bool(pp.get("generate_tags", True)),
        generate_action_items=bool(pp.get("generate_action_items", False)),
    )
    try:
        result = provider.generate_metadata(doc.transcription.segments, options)
    except Exception as exc:
        log(f"[warn] metadata generation failed: {exc}")
        return

    doc.metadata = Metadata(
        title=result.title,
        slug=result.slug,
        summary=result.summary,
        tags=result.tags,
        action_items=result.action_items,
    )


def _maybe_cleanup(
    paths: ProjectPaths, settings: dict[str, Any], doc: TranscriptDoc, log: Logger
) -> None:
    cfg = settings.get("postprocessing", {}).get("cleanup", {})
    if not bool(cfg.get("enabled", False)) or not doc.transcription.segments:
        return

    store = PromptStore.load(paths)
    prompt_id = cfg.get("prompt_id")
    entry = store.get(prompt_id) if prompt_id else None
    if entry is None or entry.role != ROLE_CLEANUP:
        entry = store.active(ROLE_CLEANUP)
    if not entry.builtin:
        store.touch(entry.id)

    log(f"Cleanup pass (model={cfg.get('model')}, prompt={entry.name})")
    provider = registry.get_postprocess_provider(paths, settings)
    options = PostprocessOptions(
        cleanup_model=cfg.get("model"),
        cleanup_prompt=entry.text,
        context=settings.get("context", ""),
        hotwords=list(shared_keyterms(settings)),
        language=doc.transcription.language,
    )
    try:
        result = provider.cleanup_text(doc.transcription.segments, options)
    except Exception as exc:
        log(f"[warn] cleanup pass failed: {exc}")
        return
    if result.markdown.strip():
        doc.cleaned_markdown = result.markdown.strip()


def _maybe_export_connectors(
    paths: ProjectPaths, settings: dict[str, Any], doc: TranscriptDoc, source: Path, log: Logger
) -> None:
    """Push to enabled export connectors (Notion/Obsidian). Isolated: any
    connector failure is logged as a warning and never fails the file."""
    from ..connectors import any_enabled, export_doc

    if not any_enabled(paths, settings):
        return
    try:
        export_doc(paths, settings, doc, source_path=source, log=log)
    except Exception as exc:  # last-resort guard
        log(f"[warn] connector export failed: {exc}")


def _write_outputs(
    paths: ProjectPaths, source: Path, settings: dict[str, Any], doc: TranscriptDoc, log: Logger
) -> dict[str, str]:
    paths_map = sidecar_paths(source, settings, paths)
    if paths_map:
        next(iter(paths_map.values())).parent.mkdir(parents=True, exist_ok=True)
    exports = settings.get("exports", {})
    sub_settings = _subtitle_settings(settings)
    segments = doc.transcription.segments
    outputs: dict[str, str] = {}

    # Record export paths in the canonical doc before writing JSON.
    if exports.get("markdown", True):
        doc.exports.markdown = paths_map["markdown"].name
    if exports.get("srt", False):
        doc.exports.srt = paths_map["srt"].name
    if exports.get("vtt", False):
        doc.exports.vtt = paths_map["vtt"].name
    if exports.get("txt", False):
        doc.exports.txt = paths_map["txt"].name
    if exports.get("json", True):
        doc.exports.json_path = paths_map["json"].name

    if exports.get("json", True):
        write_json(paths_map["json"], doc)
        outputs["json"] = str(paths_map["json"])
        log(f"Wrote {paths_map['json'].name}")
    if exports.get("markdown", True):
        write_markdown(paths_map["markdown"], doc)
        outputs["markdown"] = str(paths_map["markdown"])
        log(f"Wrote {paths_map['markdown'].name}")
    if exports.get("srt", False):
        write_srt(paths_map["srt"], segments, sub_settings)
        outputs["srt"] = str(paths_map["srt"])
        log(f"Wrote {paths_map['srt'].name}")
    if exports.get("vtt", False):
        write_vtt(paths_map["vtt"], segments, sub_settings)
        outputs["vtt"] = str(paths_map["vtt"])
        log(f"Wrote {paths_map['vtt'].name}")
    if exports.get("txt", False):
        write_txt(paths_map["txt"], segments, include_speakers=sub_settings.include_speakers)
        outputs["txt"] = str(paths_map["txt"])
        log(f"Wrote {paths_map['txt'].name}")

    return outputs
