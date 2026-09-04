"""Runtime capability matrix for the setup UI and diagnostics.

Installers answer "what can I run"; capabilities answer "what is ready right
now". Checks are intentionally lightweight: no model downloads and no CUDA
runtime tests here.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass

from .credentials import read_api_key
from .editions import (
    EDITION_STUDIO,
    current_edition,
    find_whispercpp_cli,
    find_whispercpp_server,
    resolve_whispercpp_model,
    whispercpp_cuda_ready,
)
from .model_assets import gigaam_vad_available, whispercpp_model_assets
from .paths import ProjectPaths

STATE_READY = "ready"
STATE_PARTIAL = "partial"
STATE_MISSING = "missing"


@dataclass(frozen=True)
class CapabilityCheck:
    key: str
    ok: bool
    label_key: str
    detail: str = ""
    required: bool = True


@dataclass(frozen=True)
class CapabilityStatus:
    key: str
    name_key: str
    desc_key: str
    state: str
    checks: tuple[CapabilityCheck, ...]
    module_key: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY

    @property
    def missing_count(self) -> int:
        return sum(1 for check in self.checks if check.required and not check.ok)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _state(checks: tuple[CapabilityCheck, ...]) -> str:
    required_checks = tuple(check for check in checks if check.required)
    if not required_checks:
        return STATE_READY
    ok_count = sum(1 for check in required_checks if check.ok)
    if ok_count == len(required_checks):
        return STATE_READY
    if ok_count == 0:
        return STATE_MISSING
    return STATE_PARTIAL


def _cap(
    key: str,
    name_key: str,
    desc_key: str,
    checks: tuple[CapabilityCheck, ...],
    *,
    module_key: str | None = None,
) -> CapabilityStatus:
    return CapabilityStatus(
        key=key,
        name_key=name_key,
        desc_key=desc_key,
        state=_state(checks),
        checks=checks,
        module_key=module_key,
    )


def openai_capability(paths: ProjectPaths) -> CapabilityStatus:
    key = read_api_key(paths, "openai")
    checks = (
        CapabilityCheck("openai_key", bool(key), "cap_check_openai_key"),
    )
    return _cap("openai", "cap_openai", "cap_openai_desc", checks)


def ffmpeg_capability(paths: ProjectPaths) -> CapabilityStatus:
    bin_dir = paths.tools / "ffmpeg" / "bin"
    checks = (
        CapabilityCheck("ffmpeg", (bin_dir / "ffmpeg.exe").exists(), "cap_check_ffmpeg"),
        CapabilityCheck("ffprobe", (bin_dir / "ffprobe.exe").exists(), "cap_check_ffprobe"),
    )
    return _cap("ffmpeg", "cap_ffmpeg", "cap_ffmpeg_desc", checks, module_key="ffmpeg")


def live_capability(paths: ProjectPaths) -> CapabilityStatus:
    checks = (
        CapabilityCheck("sounddevice", _has("sounddevice"), "cap_check_sounddevice"),
        CapabilityCheck("websockets", _has("websockets"), "cap_check_websockets"),
    )
    return _cap("live", "cap_live", "cap_live_desc", checks, module_key="live")


def local_whisper_capability(paths: ProjectPaths) -> CapabilityStatus:
    checks = (
        CapabilityCheck("faster_whisper", _has("faster_whisper"), "cap_check_faster_whisper"),
        CapabilityCheck("ctranslate2", _has("ctranslate2"), "cap_check_ctranslate2"),
    )
    return _cap("whisper", "cap_whisper", "cap_whisper_desc", checks, module_key="whisper")


def gigaam_capability(paths: ProjectPaths) -> CapabilityStatus:
    marker = paths.tools / "gigaam" / "audion-gigaam-onnx-pack.txt"
    checks = (
        CapabilityCheck("onnx_asr", _has("onnx_asr"), "cap_check_onnx_asr"),
        CapabilityCheck("onnxruntime", _has("onnxruntime"), "cap_check_onnxruntime"),
        CapabilityCheck("gigaam_payload", marker.exists(), "cap_check_gigaam_payload", str(marker)),
        CapabilityCheck(
            "gigaam_vad",
            gigaam_vad_available(paths),
            "cap_check_gigaam_vad",
            str(paths.models / "huggingface"),
            required=False,
        ),
    )
    return _cap("gigaam", "cap_gigaam", "cap_gigaam_desc", checks, module_key="gigaam")


def vulkan_capability(paths: ProjectPaths) -> CapabilityStatus:
    root = paths.tools / "whispercpp"
    marker = root / "audion-whispercpp-pack.txt"
    cli = find_whispercpp_cli(paths)
    server = find_whispercpp_server(paths)
    common = ("ggml.dll", "ggml-base.dll", "whisper.dll")
    studio = current_edition(paths) == EDITION_STUDIO
    if studio:
        backend_ok = all(
            (root / name).exists()
            for name in ("ggml-cuda.dll", "cudart64_12.dll", "cublas64_12.dll")
        )
        backend = "cublas"
    else:
        backend_ok = any(root.glob("ggml-cpu-*.dll"))
        backend = "auto"
    model_checks: list[CapabilityCheck] = []
    for asset in whispercpp_model_assets():
        path = asset.path(paths)
        model = resolve_whispercpp_model(paths, asset.key)
        model_checks.append(
            CapabilityCheck(
                f"whispercpp_model_{asset.key}",
                model is not None,
                asset.label_key,
                str(model or path),
                required=asset.required,
            )
        )
    checks = (
        CapabilityCheck(
            "whispercpp_manifest",
            marker.exists(),
            "cap_check_whispercpp_manifest",
            str(marker),
        ),
        CapabilityCheck(
            "whispercpp_cli",
            cli is not None,
            "cap_check_whispercpp_binary",
            str(cli) if cli else str(root),
        ),
        CapabilityCheck(
            "whispercpp_server",
            server is not None,
            "cap_check_whispercpp_server",
            str(server) if server else str(root),
        ),
        CapabilityCheck(
            "whispercpp_common_payloads",
            all((root / name).exists() for name in common),
            "cap_check_whispercpp_payloads",
            str(root),
        ),
        CapabilityCheck(
            "whispercpp_backend_payloads",
            backend_ok,
            "cap_check_whispercpp_backend",
            backend,
        ),
        *model_checks,
    )
    return _cap("vulkan", "cap_vulkan", "cap_vulkan_desc", checks, module_key="vulkan")


def cuda_capability(paths: ProjectPaths) -> CapabilityStatus:
    nvidia_smi = shutil.which("nvidia-smi")
    large_v2 = resolve_whispercpp_model(paths, "large-v2")
    checks = (
        CapabilityCheck("nvidia_smi", nvidia_smi is not None, "cap_check_nvidia_smi", nvidia_smi or ""),
        CapabilityCheck(
            "whispercpp_cuda_runtime",
            whispercpp_cuda_ready(paths, model="large-v2", require_server=True),
            "cap_check_whispercpp_cuda_runtime",
            str(paths.tools / "whispercpp"),
        ),
        CapabilityCheck(
            "whispercpp_large_v2",
            large_v2 is not None,
            "cap_check_model_large_v2",
            str(large_v2 or (paths.models / "ggml-large-v2.bin")),
        ),
        CapabilityCheck("torch", _has("torch"), "cap_check_torch", required=False),
        CapabilityCheck("pyannote", _has("pyannote"), "cap_check_pyannote", required=False),
    )
    return _cap("cuda", "cap_cuda", "cap_cuda_desc", checks)


def list_capabilities(paths: ProjectPaths) -> list[CapabilityStatus]:
    capabilities = [
        openai_capability(paths),
        ffmpeg_capability(paths),
        live_capability(paths),
        gigaam_capability(paths),
        vulkan_capability(paths),
    ]
    if current_edition(paths) == EDITION_STUDIO:
        capabilities.extend([
            cuda_capability(paths),
        ])
    return capabilities
