"""Persist GUI settings back to the two config files.

`load_settings` merges built-in defaults <- app_settings.yaml <- providers.yaml.
The GUI edits a single merged dict; on save we split it back to the right file so
the CLI and the backend keep reading the same configuration:

  app_settings.yaml  <- compute_mode, language, ui_language, ui_theme, context, term_dictionary,
                        transcription, exports, subtitles, postprocessing, pipeline
  providers.yaml     <- stt, postprocess, assemblyai, diarization, local

We update keys in place (preserving anything else, e.g. schema_version) and write
atomically so a crash never leaves a half-written config.
"""

from __future__ import annotations

from typing import Any

from ..core.config import load_yaml_or_json
from ..core.paths import ProjectPaths
from ..writers.atomic import write_text_atomic

APP_KEYS = (
    "compute_mode",
    "language",
    "ui_language",
    "ui_theme",
    "context",
    "term_dictionary",
    "transcription",
    "exports",
    "subtitles",
    "postprocessing",
    "pipeline",
    "connectors",
    "live",
)
PROVIDER_KEYS = ("stt", "postprocess", "assemblyai", "diarization", "local", "vulkan")


def _dump_yaml(data: dict[str, Any]) -> str:
    import yaml  # pyyaml is a base dependency

    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _write_subset(paths: ProjectPaths, filename: str, settings: dict[str, Any], keys) -> None:
    target = paths.config / filename
    existing = load_yaml_or_json(target)
    for key in keys:
        if key in settings:
            existing[key] = settings[key]
    write_text_atomic(target, _dump_yaml(existing))


def save_settings(paths: ProjectPaths, settings: dict[str, Any]) -> None:
    _write_subset(paths, "app_settings.yaml", settings, APP_KEYS)
    _write_subset(paths, "providers.yaml", settings, PROVIDER_KEYS)
