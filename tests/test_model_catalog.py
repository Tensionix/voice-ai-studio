from pathlib import Path

from system_core.core.paths import get_project_paths
from system_core.providers import model_catalog as mc


def test_classify_stt_models():
    assert mc.ROLE_STT in mc.classify_model("gpt-4o-transcribe-diarize")
    assert mc.ROLE_STT in mc.classify_model("whisper-1")
    assert mc.ROLE_STT in mc.classify_model("gpt-4o-mini-transcribe")
    assert mc.ROLE_STT not in mc.classify_model("gpt-realtime-whisper")


def test_classify_chat_models():
    assert mc.ROLE_CHAT in mc.classify_model("gpt-5.4-mini")
    assert mc.ROLE_CHAT in mc.classify_model("gpt-4o")
    # transcribe models are not chat models
    assert mc.ROLE_CHAT not in mc.classify_model("gpt-4o-transcribe")


def test_classify_excludes_non_text():
    assert mc.classify_model("tts-1") == set()
    assert mc.classify_model("text-embedding-3-large") == set()
    assert mc.ROLE_CHAT not in mc.classify_model("dall-e-3")


def test_filter_models_by_role():
    ids = ["gpt-4o", "gpt-4o-transcribe", "gpt-5.4-mini", "whisper-1", "gpt-realtime-whisper", "tts-1"]
    stt = mc.filter_models(ids, mc.ROLE_STT)
    chat = mc.filter_models(ids, mc.ROLE_CHAT)
    assert "gpt-4o-transcribe" in stt and "whisper-1" in stt
    assert "gpt-4o" in chat and "gpt-5.4-mini" in chat
    assert "gpt-realtime-whisper" not in stt
    assert "tts-1" not in stt and "tts-1" not in chat


def test_cache_read_write_roundtrip(tmp_path: Path):
    paths = get_project_paths().__class__(  # build a ProjectPaths rooted at tmp
        root=tmp_path, input=tmp_path, output=tmp_path, logs=tmp_path,
        report=tmp_path, workspace=tmp_path, config=tmp_path, release=tmp_path,
        models=tmp_path, tools=tmp_path, runtime=tmp_path, system_core=tmp_path,
    )
    mc._write_cache(paths, ["gpt-4o", "whisper-1"])
    cache = mc._read_cache(paths)
    assert cache is not None
    assert set(cache["models"]) == {"gpt-4o", "whisper-1"}


def test_fallback_when_no_api_and_no_cache(tmp_path: Path, monkeypatch):
    paths = get_project_paths().__class__(
        root=tmp_path, input=tmp_path, output=tmp_path, logs=tmp_path,
        report=tmp_path, workspace=tmp_path, config=tmp_path, release=tmp_path,
        models=tmp_path, tools=tmp_path, runtime=tmp_path, system_core=tmp_path,
    )
    monkeypatch.setattr(mc, "_fetch_from_api", lambda _paths: None)
    result = mc.list_models(paths, mc.ROLE_STT, refresh=True)
    assert result.source == "fallback"
    assert mc.DEFAULT_STT_MODEL in result.models
