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
    wheel_root = paths.root / "install" / "wheels"
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

    def script_path(self, paths: ProjectPaths) -> Path:
        return paths.root / "install" / self.script

    def is_installed(self, paths: ProjectPaths) -> bool:
        try:
            return bool(self._check(paths))
        except Exception:
            return False


# GUI runs from an already prepared portable Python app. Mirror the builder's
# user-facing install flow from the first step that can be launched inside GUI.
_MODULES: list[ModuleInfo] = [
    ModuleInfo("ffmpeg", "Install-Portable-FFmpeg-BtbN.cmd",
               "mod_ffmpeg", "mod_ffmpeg_desc", _ffmpeg_installed),
    ModuleInfo("live", "Install-Live-Deps.cmd",
               "mod_live", "mod_live_desc", _live_installed),
    ModuleInfo("wheel_cache", "Rebuild-Wheel-Cache.cmd",
               "mod_wheel_cache", "mod_wheel_cache_desc", _wheel_cache_installed),
    ModuleInfo("gigaam", "Install-GigaAM-ONNX.cmd",
               "mod_gigaam", "mod_gigaam_desc", _gigaam_installed),
    ModuleInfo("vulkan", "Install-Live-Vulkan.cmd",
               "mod_vulkan", "mod_vulkan_desc", _vulkan_installed),
    ModuleInfo("whispercpp_models", "Install-WhisperCpp-Large-Models.cmd",
               "mod_whispercpp_models", "mod_whispercpp_models_desc",
               _whispercpp_large_models_installed, (EDITION_STUDIO,)),
    ModuleInfo("gpu", "Install-Diarization-GPU.cmd",
               "mod_gpu", "mod_gpu_desc", _gpu_installed, (EDITION_STUDIO,)),
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
