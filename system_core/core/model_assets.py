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
