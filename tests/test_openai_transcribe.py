from pathlib import Path

from system_core.core.paths import get_project_paths
from system_core.live.transcriber import write_wav_pcm16
from system_core.providers.base import TranscriptionOptions
from system_core.providers.model_catalog import DEFAULT_DIARIZE_STT_MODEL
from system_core.providers.openai_transcribe import OpenAITranscribeProvider


class _FakeTranscriptions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "segments": [
                {"text": "hello", "start": 0.0, "end": 1.0, "speaker": "0"},
            ],
            "language": "en",
        }


class _FakeAudio:
    def __init__(self):
        self.transcriptions = _FakeTranscriptions()


class _FakeClient:
    def __init__(self):
        self.audio = _FakeAudio()


def test_openai_diarize_request_uses_diarize_model(tmp_path: Path, monkeypatch):
    wav = tmp_path / "sample.wav"
    write_wav_pcm16(wav, b"\x00\x00" * 16000, 16000)

    client = _FakeClient()
    provider = OpenAITranscribeProvider(get_project_paths(), model="whisper-1")
    monkeypatch.setattr(provider, "_get_client", lambda: client)

    result = provider.transcribe(wav, TranscriptionOptions(model="whisper-1", diarize=True))

    call = client.audio.transcriptions.calls[0]
    assert call["model"] == DEFAULT_DIARIZE_STT_MODEL
    assert call["response_format"] == "diarized_json"
    assert result.diarization is True
    assert result.segments[0].speaker == "Speaker 0"
