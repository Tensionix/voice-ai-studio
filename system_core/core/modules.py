"""Optional Voice AI modules: the opt-in installers exposed in the GUI.

The base (API-mode) env stays light; heavier stacks (GigaAM/ONNX,
local Whisper, GPU diarization), the mic capture deps and the portable FFmpeg are installed on
demand via the `install/*.cmd` scripts. This catalog is the single source of
truth the UI renders: each entry knows its installer script and how to detect
whether it's already present, so the "Install Voice AI modules" section can show
live status and run the right script.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .editions import (
    EDITION_LIVE,
    EDITION_STUDIO,
    current_edition,
    whispercpp_cuda_ready,
    whispercpp_runtime_ready,
)
from .paths import ProjectPaths


def _has(module: str) -> bool:
    """True if an import would resolve `module` (without importing it)."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _ffmpeg_installed(paths: ProjectPaths) -> bool:
    bin_dir = paths.tools / "ffmpeg" / "bin"
    return (bin_dir / "ffmpeg.exe").exists() or (bin_dir / "ffmpeg").exists()


def _wheel_cache_installed(paths: ProjectPaths) -> bool:
    # The installers (Install-GigaAM-ONNX.ps1, Install-Live-Deps.cmd) and
    # Rebuild-Wheel-Cache.ps1 all read/write the root-level `wheelhouse`.
    wheel_root = paths.root / "wheelhouse"
    required = ["common", "directml", "cpu"]
    if current_edition(paths) == EDITION_STUDIO:
        required.append("cuda")
    return all(any((wheel_root / name).glob("*.whl")) for name in required)


def _live_installed(paths: ProjectPaths) -> bool:
    return _has("sounddevice")


def _gigaam_installed(paths: ProjectPaths) -> bool:
    marker = paths.tools / "gigaam" / "audion-gigaam-onnx-pack.txt"
    return _has("onnx_asr") and _has("onnxruntime") and marker.exists()


def _marker_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    prefix = f"{key}="
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith(prefix):
                return line[len(prefix):].strip().lower()
    except OSError:
        return ""
    return ""


def _onnx_providers() -> set[str]:
    try:
        import onnxruntime as ort  # type: ignore

        return set(ort.get_available_providers())
    except Exception:
        return set()


def _gigaam_provider(paths: ProjectPaths, kind: str, providers: tuple[str, ...]) -> bool:
    marker = paths.tools / "gigaam" / "audion-gigaam-onnx-pack.txt"
    if not (_has("onnx_asr") and _has("onnxruntime") and marker.exists()):
        return False
    if _marker_value(marker, "kind") != kind:
        return False
    available = _onnx_providers()
    return any(provider in available for provider in providers)


def _gigaam_directml_installed(paths: ProjectPaths) -> bool:
    return _gigaam_provider(paths, "directml", ("DmlExecutionProvider",))


def _gigaam_cuda_installed(paths: ProjectPaths) -> bool:
    return _gigaam_provider(
        paths,
        "cuda",
        ("CUDAExecutionProvider",),
    )


def _gpu_installed(paths: ProjectPaths) -> bool:
    return _has("torch") and _has("pyannote")


def _vulkan_installed(paths: ProjectPaths) -> bool:
    if current_edition(paths) == EDITION_STUDIO:
        return whispercpp_cuda_ready(paths, model="turbo", require_server=True)
    return whispercpp_runtime_ready(
        paths, model="turbo", backend="auto", require_server=True
    )


def _whispercpp_large_models_installed(paths: ProjectPaths) -> bool:
    return (paths.models / "ggml-large-v2.bin").exists()


@dataclass(frozen=True)
class ModuleInfo:
    key: str            # stable id
    script: str         # filename under install/
    name_key: str       # i18n key (display name)
    desc_key: str       # i18n key (one-line description)
    _check: Callable[[ProjectPaths], bool]
    editions: tuple[str, ...] = (EDITION_LIVE, EDITION_STUDIO)
    install_key: str = "mod_install"
    reinstall_key: str = "mod_reinstall"
    # Approximate network download in megabytes (models, packs, wheels not
    # shipped in the distribution). Shown in the first-run setup prompt.
    download_mb: int = 0

    def script_path(self, paths: ProjectPaths) -> Path:
        return paths.root / "install" / self.script

    def is_installed(self, paths: ProjectPaths) -> bool:
        try:
            return bool(self._check(paths))
        except Exception:
            return False


RECOMMENDED = "recommended"
OPTIONAL = "optional"
NOT_NEEDED = "not_needed"

# Modules every installation of the edition should have. Restore rows and the
# Studio GPU stack depend on the detected hardware (see module_recommendation).
_ALWAYS_RECOMMENDED = {"ffmpeg", "live", "wheel_cache", "gigaam", "vulkan", "whispercpp_models"}


def module_recommendation(mod: ModuleInfo, paths: ProjectPaths, profile=None) -> str:
    """Classify a module for this computer: RECOMMENDED / OPTIONAL / NOT_NEEDED.

    `profile` is a `LocalHardwareProfile` (or None while detection runs / after
    it failed, which yields the safe non-GPU recommendations)."""
    edition = current_edition(paths)
    has_nvidia = bool(getattr(profile, "has_nvidia", False))
    has_windows_gpu = bool(
        has_nvidia
        or getattr(profile, "has_amd", False)
        or getattr(profile, "has_intel", False)
    )
    if mod.key in _ALWAYS_RECOMMENDED:
        return RECOMMENDED
    if mod.key == "gpu":
        return RECOMMENDED if edition == EDITION_STUDIO and has_nvidia else NOT_NEEDED
    if mod.key == "restore_rtx":
        if mod.is_installed(paths):
            return NOT_NEEDED
        return OPTIONAL if edition == EDITION_STUDIO and has_nvidia else NOT_NEEDED
    if mod.key == "restore_intel":
        if mod.is_installed(paths):
            return NOT_NEEDED
        return OPTIONAL if has_windows_gpu and not has_nvidia else NOT_NEEDED
    return OPTIONAL


def missing_recommended_modules(paths: ProjectPaths, profile=None) -> list[ModuleInfo]:
    """Recommended modules that are not installed yet, in install order.

    This is the list the first-run setup prompt offers to download so the
    readiness matrix on the Maintenance page turns fully green."""
    return [
        mod
        for mod in list_modules(paths)
        if module_recommendation(mod, paths, profile) == RECOMMENDED
        and not mod.is_installed(paths)
    ]


# GUI runs from an already prepared portable Python app. Mirror the builder's
# user-facing install flow from the first step that can be launched inside GUI.
_MODULES: list[ModuleInfo] = [
    ModuleInfo("ffmpeg", "Install-Portable-FFmpeg-BtbN.cmd",
               "mod_ffmpeg", "mod_ffmpeg_desc", _ffmpeg_installed, download_mb=150),
    ModuleInfo("live", "Install-Live-Deps.cmd",
               "mod_live", "mod_live_desc", _live_installed, download_mb=1),
    # common + directml + cpu + cuda wheels (onnxruntime-gpu, cuDNN, cuBLAS...).
    ModuleInfo("wheel_cache", "Rebuild-Wheel-Cache.cmd",
               "mod_wheel_cache", "mod_wheel_cache_desc", _wheel_cache_installed, download_mb=1900),
    # GigaAM v3 CTC + RNN-T ONNX payloads (~845 MB each) from Hugging Face.
    ModuleInfo("gigaam", "Install-GigaAM-ONNX.cmd",
               "mod_gigaam", "mod_gigaam_desc", _gigaam_installed, download_mb=1750),
    # whisper.cpp CUDA/cuBLAS pack (640 MB) + ggml-large-v3-turbo.bin (1549 MB).
    ModuleInfo("vulkan", "Install-Live-Vulkan.cmd",
               "mod_vulkan", "mod_vulkan_desc", _vulkan_installed, download_mb=2190),
    # ggml-large-v2.bin (2951 MB).
    ModuleInfo("whispercpp_models", "Install-WhisperCpp-Large-Models.cmd",
               "mod_whispercpp_models", "mod_whispercpp_models_desc",
               _whispercpp_large_models_installed, (EDITION_STUDIO,), download_mb=2960),
    # torch cu128 (3273 MB) + torchaudio + pyannote.audio and dependencies.
    ModuleInfo("gpu", "Install-Diarization-GPU.cmd",
               "mod_gpu", "mod_gpu_desc", _gpu_installed, (EDITION_STUDIO,), download_mb=3700),
    ModuleInfo("restore_intel", "Restore-GigaAM-DirectML.cmd",
               "mod_restore_intel", "mod_restore_intel_desc",
               _gigaam_directml_installed, install_key="mod_restore", reinstall_key="mod_restore"),
    ModuleInfo("restore_rtx", "Restore-GigaAM-CUDA.cmd",
               "mod_restore_rtx", "mod_restore_rtx_desc",
               _gigaam_cuda_installed, install_key="mod_restore", reinstall_key="mod_restore"),
]


def list_modules(paths: ProjectPaths | None = None, settings: dict | None = None) -> list[ModuleInfo]:
    if paths is None:
        return list(_MODULES)
    edition = current_edition(paths, settings)
    return [module for module in _MODULES if edition in module.editions]
