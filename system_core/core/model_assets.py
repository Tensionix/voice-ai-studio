"""Known portable model assets for local whisper.cpp modes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import ProjectPaths


@dataclass(frozen=True)
class ModelAsset:
    key: str
    label_key: str
    filename: str
    url: str
    required: bool = False

    def path(self, paths: ProjectPaths) -> Path:
        return paths.models / self.filename


_HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

WHISPERCPP_MODEL_ASSETS: tuple[ModelAsset, ...] = (
    ModelAsset(
        key="small",
        label_key="cap_check_model_small",
        filename="ggml-small.bin",
        url=f"{_HF_BASE}/ggml-small.bin",
    ),
    ModelAsset(
        key="turbo",
        label_key="cap_check_model_turbo",
        filename="ggml-large-v3-turbo.bin",
        url=f"{_HF_BASE}/ggml-large-v3-turbo.bin",
        required=True,
    ),
    ModelAsset(
        key="large-v2",
        label_key="cap_check_model_large_v2",
        filename="ggml-large-v2.bin",
        url=f"{_HF_BASE}/ggml-large-v2.bin",
    ),
)


# Silero VAD (istupakov/silero-vad-onnx) lands in the same Hugging Face cache as
# the GigaAM payloads. GigaAM file transcription uses it to cut long audio at
# pauses instead of at fixed 45 s marks.
GIGAAM_VAD_MODEL = "silero"
_GIGAAM_VAD_REPO_DIR = "models--istupakov--silero-vad-onnx"


def gigaam_vad_cache_dir(paths: ProjectPaths) -> Path:
    return paths.models / "huggingface" / "hub" / _GIGAAM_VAD_REPO_DIR


def gigaam_vad_available(paths: ProjectPaths) -> bool:
    """True when the Silero VAD payload is already in the local HF cache."""
    return (gigaam_vad_cache_dir(paths) / "snapshots").is_dir()


def whispercpp_model_assets() -> tuple[ModelAsset, ...]:
    return WHISPERCPP_MODEL_ASSETS


def whispercpp_model_asset(key: str | None = None) -> ModelAsset | None:
    wanted = str(key or "turbo").strip().lower()
    for asset in WHISPERCPP_MODEL_ASSETS:
        if wanted in {asset.key, asset.filename.lower()}:
            return asset
    return None


def whispercpp_model_path(paths: ProjectPaths, key: str | None = None) -> Path:
    asset = whispercpp_model_asset(key)
    if asset is not None:
        return asset.path(paths)
    return paths.models / str(key or "turbo")
