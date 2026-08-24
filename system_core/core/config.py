from __future__ import annotations

from pathlib import Path
from typing import Any
import copy
import json


def load_yaml_or_json(path: Path) -> dict[str, Any]:
    """Load a config file (.yaml/.yml/.json). Missing file -> empty mapping."""
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8")

    if path.suffix.lower() == ".json":
        return json.loads(text or "{}")

    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(f"PyYAML is required to read {path.name}. Install package: pyyaml") from exc

    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a mapping: {path}")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base (override wins)."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_with_defaults(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    """Load a config file and merge it on top of the given defaults."""
    return deep_merge(defaults, load_yaml_or_json(path))
