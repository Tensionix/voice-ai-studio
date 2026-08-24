"""Portable whisper.cpp provider used by the Local Models compute mode.

The installer places binaries under Tools/whispercpp and GGML models under
models/. This provider is intentionally light: importing it does not require the
pack to be installed; the first transcription raises a clean setup error if the
pack is missing.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Optional

from ..core.editions import (
    find_whispercpp_binary,
    resolve_whispercpp_model,
    whispercpp_cublas_backend_present,
    whispercpp_vulkan_backend_present,
    whispercpp_model_file,
)
from ..core.jobs import run_capture
from ..core.local_hardware import recommended_whispercpp_backend
from ..core.models import Segment
from ..core.paths import ProjectPaths
from .base import TranscriptResult, TranscriptionOptions, TranscriptionProvider
from .whispercpp_server import WhisperCppServerError, cached_file_server

DEFAULT_WHISPERCPP_MODEL = "turbo"
REMOVED_WHISPERCPP_MODELS = {"large-v3", "ggml-large-v3.bin"}
_GPU_FAILURE_MARKERS = ("cublas", "cuda", "vulkan", "gpu", "ggml_backend", "ggml_vulkan", "device")


def missing_whispercpp_message(paths: ProjectPaths, model: str | None = None) -> str:
    expected_model = whispercpp_model_file(model or DEFAULT_WHISPERCPP_MODEL)
    return (
        "whisper.cpp local pack is not installed. Run "
        "install/Install-Live-Vulkan.cmd. Expected binary under "
        f"{paths.tools / 'whispercpp'} and model {paths.models / expected_model}."
    )


class WhisperCppProvider(TranscriptionProvider):
    name = "whisper.cpp"

    def __init__(
        self,
        paths: ProjectPaths,
        model: Optional[str] = None,
        *,
        backend: str = "auto",
    ) -> None:
        self.paths = paths
        model_value = str(model or DEFAULT_WHISPERCPP_MODEL).strip()
        if model_value.lower() in REMOVED_WHISPERCPP_MODELS:
            model_value = DEFAULT_WHISPERCPP_MODEL
        self.model = model_value
        self.backend = backend or "auto"

    def _resolve(self) -> tuple[Path, Path]:
        binary = find_whispercpp_binary(self.paths)
        model_path = resolve_whispercpp_model(self.paths, self.model)
        if binary is None or model_path is None:
            raise RuntimeError(missing_whispercpp_message(self.paths, self.model))
        return binary, model_path

    def _backend_args(self, backend: str) -> list[str]:
        # whisper.cpp uses -ng to force CPU execution when the binary was built
        # with a GPU backend. In auto/GPU modes the build chooses its default.
        return ["-ng"] if backend == "cpu" else []

    def _effective_backend(self) -> str:
        requested = str(self.backend or "auto").strip().lower()
        if requested == "auto":
            return recommended_whispercpp_backend(self.paths)
        if requested in {"cuda", "cublas"} and not whispercpp_cublas_backend_present(self.paths):
            return "cpu"
        if requested == "vulkan" and not whispercpp_vulkan_backend_present(self.paths):
            return "cpu"
        return requested

    def _build_command(
        self,
        binary: Path,
        model_path: Path,
        audio_path: Path,
        output_base: Path,
        *,
        language: str | None,
        context: str | None,
        backend: str,
    ) -> list[str]:
        command = [
            str(binary),
            *self._backend_args(backend),
            "-m",
            str(model_path),
            "-f",
            str(audio_path),
            "-otxt",
            "-oj",
            "-of",
            str(output_base),
        ]
        if language:
            command.extend(["-l", language])
        if context:
            command.extend(["--prompt", context])
        return command

    def _should_retry_cpu(self, result_text: str) -> bool:
        if self.backend != "auto":
            return False
        text = result_text.lower()
        return any(marker in text for marker in _GPU_FAILURE_MARKERS)

    @staticmethod
    def _payload_segments(payload: dict) -> list[dict]:
        segments = payload.get("segments")
        if isinstance(segments, list):
            return [item for item in segments if isinstance(item, dict)]
        transcription = payload.get("transcription")
        if isinstance(transcription, list):
            return [item for item in transcription if isinstance(item, dict)]
        return []

    @staticmethod
    def _segment_times(item: dict) -> tuple[float, float]:
        try:
            return float(item.get("start", 0.0)), float(item.get("end", 0.0))
        except (TypeError, ValueError):
            pass
        offsets = item.get("offsets")
        if isinstance(offsets, dict):
            try:
                return float(offsets.get("from", 0.0)) / 1000.0, float(offsets.get("to", 0.0)) / 1000.0
            except (TypeError, ValueError):
                pass
        return 0.0, 0.0

    def _result_from_payload(
        self,
        payload: dict,
        *,
        language: str | None,
        model_name: str,
    ) -> TranscriptResult:
        segments: list[Segment] = []
        for item in self._payload_segments(payload):
            raw_text = str(item.get("text", "") or "")
            if not raw_text.strip():
                continue
            start, end = self._segment_times(item)
            # whisper.cpp sometimes splits a long word between adjacent JSON
            # segments. A continuation has no leading whitespace; glue it back
            # before exposing canonical segments to diarization and writers.
            if segments and raw_text and not raw_text[0].isspace():
                previous = segments[-1]
                previous.text = previous.text + raw_text.strip()
                previous.end = max(previous.end, end)
                continue
            segments.append(
                Segment(index=len(segments), start=start, end=end, text=raw_text.strip())
            )
        if not segments:
            text = str(payload.get("text", "") or "").strip()
            if text:
                segments = [Segment(index=0, start=0.0, end=0.0, text=text)]
        detected = payload.get("detected_language") or payload.get("language")
        return TranscriptResult(
            segments=segments,
            language=language or (str(detected) if detected else None),
            model=model_name,
            provider=self.name,
            diarization=False,
        )

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        binary, model_path = self._resolve()
        language = options.language
        if language and language.lower() == "auto":
            language = None
        backend = self._effective_backend()
        prompt = options.prompt_context()

        server = cached_file_server(self.paths, self.model, backend)
        if server.available():
            try:
                payload = server.transcribe_wav_verbose(
                    audio_path,
                    language=language,
                    prompt=prompt,
                )
                return self._result_from_payload(
                    payload, language=language, model_name=model_path.name
                )
            except WhisperCppServerError:
                # Older/partial packs can still use the one-shot CLI below.
                pass

        self.paths.workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="audion-whispercpp-", dir=str(self.paths.workspace)
        ) as tmp:
            output_base = Path(tmp) / "result"
            command = self._build_command(
                binary,
                model_path,
                audio_path,
                output_base,
                language=language,
                context=prompt,
                backend=backend,
            )
            result = run_capture(command, cwd=self.paths.root)
            if result.exit_code != 0 and self._should_retry_cpu(result.text):
                command = self._build_command(
                    binary,
                    model_path,
                    audio_path,
                    output_base,
                    language=language,
                    context=prompt,
                    backend="cpu",
                )
                result = run_capture(command, cwd=self.paths.root)
            json_file = output_base.with_suffix(".json")
            if result.exit_code == 0 and json_file.exists():
                try:
                    payload = json.loads(json_file.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(payload, dict):
                        return self._result_from_payload(
                            payload, language=language, model_name=model_path.name
                        )
                except (OSError, ValueError, TypeError):
                    pass
            text_file = output_base.with_suffix(".txt")
            if text_file.exists():
                text = text_file.read_text(encoding="utf-8", errors="replace").strip()
            else:
                text = result.text.strip()
            if result.exit_code != 0:
                raise RuntimeError(text or f"whisper.cpp failed with exit code {result.exit_code}.")

        return self._result_from_payload(
            {"text": text}, language=language, model_name=model_path.name
        )
