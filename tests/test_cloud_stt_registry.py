import ast
import json
import wave
from pathlib import Path

import system_core.providers.cloud_stt as cloud_stt
from system_core.core.credentials import read_api_key, write_api_key
from system_core.core.paths import get_project_paths
from system_core.providers.base import TranscriptionOptions
from system_core.providers.cloud_stt import (
    GeminiTranscribeProvider,
    GigaChatTranscribeProvider,
    XAITranscribeProvider,
)
from system_core.providers.registry import get_transcription_provider, stt_provider


def _provider_order(const_name: str) -> list[str]:
    source = (get_project_paths().system_core / "ui" / "settings_panel.py").read_text(
        encoding="utf-8"
    )
    module = ast.parse(source)
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == const_name for target in node.targets):
            return [provider for provider, _label in ast.literal_eval(node.value)]
    raise AssertionError(f"{const_name} not found")


def _write_sample_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 1600)


def test_api_provider_orders_prefer_xai():
    assert _provider_order("_FILE_STT_PROVIDERS")[0] == "xai"
    assert _provider_order("_LIVE_API_PROVIDERS")[0] == "xai"


def test_xai_key_uses_canonical_txt_file(monkeypatch, tmp_path):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    paths = get_project_paths(tmp_path)
    paths.config.mkdir(parents=True)
    assert read_api_key(paths, "xai") is None

    write_api_key(paths, "xai", "fresh")

    assert (paths.config / "api_key_xai.txt").read_text(encoding="utf-8").strip() == "fresh"
    assert read_api_key(paths, "xai") == "fresh"


def test_file_stt_provider_aliases():
    assert stt_provider({"stt": {"provider": "grok"}}) == "xai"
    assert stt_provider({"stt": {"provider": "x.ai"}}) == "xai"
    assert stt_provider({"stt": {"provider": "giga"}}) == "gigachat"
    assert stt_provider({"assemblyai": {"enabled": True}}) == "assemblyai"


def test_registry_selects_cloud_file_providers():
    paths = get_project_paths()
    cases = [
        ("xai", XAITranscribeProvider, "grok-transcribe"),
        ("gemini", GeminiTranscribeProvider, "gemini-3.5-flash"),
        ("gigachat", GigaChatTranscribeProvider, "GigaChat"),
    ]
    for provider_name, provider_type, expected_model in cases:
        provider = get_transcription_provider(
            paths,
            {
                "compute_mode": "api",
                "stt": {"provider": provider_name},
            },
        )
        assert isinstance(provider, provider_type)
        assert provider.model == expected_model


def test_xai_transcribe_uses_native_multipart(monkeypatch, tmp_path):
    wav_path = tmp_path / "sample.wav"
    _write_sample_wav(wav_path)
    paths = get_project_paths(tmp_path)
    paths.config.mkdir(parents=True)
    (paths.config / "api_key_xai.txt").write_text("secret\n", encoding="utf-8")
    captured = {}

    class FakeResponse:
        status_code = 200
        text = json.dumps(
            {
                "text": "привет мир",
                "language": "ru",
                "duration": 1.0,
                "words": [
                    {"word": "привет", "start": 0.0, "end": 0.4, "speaker": "1"},
                    {"word": "мир", "start": 0.4, "end": 0.9, "speaker": "1"},
                ],
            },
            ensure_ascii=False,
        )

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "text": "привет мир",
                "language": "ru",
                "duration": 1.0,
                "words": [
                    {"word": "привет", "start": 0.0, "end": 0.4, "speaker": "1"},
                    {"word": "мир", "start": 0.4, "end": 0.9, "speaker": "1"},
                ],
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(cloud_stt.httpx, "Client", FakeClient)

    provider = XAITranscribeProvider(paths, keyterms=["NiceGUI"])
    result = provider.transcribe(
        wav_path,
        TranscriptionOptions(
            language="ru",
            diarize=True,
            context="nicegui, CustomTerm",
            keyterms=("DirectTerm", "NiceGUI"),
        ),
    )

    assert captured["url"] == "https://api.x.ai/v1/stt"
    assert captured["headers"] == {"Authorization": "Bearer secret"}
    assert "data" not in captured
    files = captured["files"]
    names = [name for name, _part in files]
    assert names[-1] == "file"
    assert "model" not in names
    assert "diarize" in names

    field_values = [(name, part[1]) for name, part in files if name != "file"]
    assert ("language", "ru") in field_values
    assert ("format", "true") in field_values
    assert ("diarize", "true") in field_values
    keyterms = [value for name, value in field_values if name == "keyterm"]
    assert keyterms.count("NiceGUI") == 1
    assert "DirectTerm" in keyterms
    assert "CustomTerm" in keyterms
    assert files[-1][1][0] == "sample.wav"
    assert result.provider == "xai"
    assert result.model == "grok-transcribe"
    assert result.language == "ru"
    assert result.diarization is True
    assert result.segments[0].speaker == "Speaker 1"
