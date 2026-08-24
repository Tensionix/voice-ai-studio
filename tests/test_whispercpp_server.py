from __future__ import annotations

from system_core.core.paths import get_project_paths
from system_core.live.whispercpp_live import WhisperCppLiveTranscriber
from system_core.providers.base import LiveOptions
from system_core.providers.whispercpp_server import WhisperCppServer, WhisperCppServerError


class _FakeProc:
    """Minimal Popen stand-in: exit_code None = alive, int = already exited."""

    def __init__(self, exit_code=None):
        self._exit = exit_code

    def poll(self):
        return self._exit

    def terminate(self):
        if self._exit is None:
            self._exit = -15

    def wait(self, timeout=None):
        return self._exit or 0

    def kill(self):
        self._exit = -9

    @property
    def returncode(self):
        return self._exit


def _stage_server_pack(tmp_path):
    paths = get_project_paths(tmp_path)
    (paths.tools / "whispercpp").mkdir(parents=True)
    paths.models.mkdir(parents=True)
    paths.workspace.mkdir(parents=True, exist_ok=True)
    (paths.tools / "whispercpp" / "whisper-server.exe").write_text("", encoding="utf-8")
    (paths.models / "ggml-large-v3-turbo.bin").write_text("", encoding="utf-8")
    wav = paths.workspace / "u.wav"
    wav.write_bytes(b"data")
    return paths, wav


def test_server_unavailable_without_binary(tmp_path):
    paths = get_project_paths(tmp_path)
    assert WhisperCppServer(paths).available() is False


def test_server_available_with_server_binary(tmp_path):
    paths, _ = _stage_server_pack(tmp_path)
    assert WhisperCppServer(paths).available() is True


def test_server_transcribes_via_warm_server(tmp_path):
    paths, wav = _stage_server_pack(tmp_path)
    commands = []
    server = WhisperCppServer(
        paths,
        launcher=lambda cmd: commands.append(cmd) or _FakeProc(exit_code=None),
        http_get=lambda url, timeout: 200,
        http_post=lambda url, wav_path, data, timeout: '{"text": "  hello world "}'
        if data.get("response_format") != "json"
        else "hello world",
    )

    text = server.transcribe_wav(wav, language="ru")

    assert text == "hello world"
    assert commands and "-ng" not in commands[0]   # auto tries GPU first
    server.close()


def test_server_returns_verbose_timestamp_payload(tmp_path):
    paths, wav = _stage_server_pack(tmp_path)
    server = WhisperCppServer(
        paths,
        launcher=lambda _cmd: _FakeProc(exit_code=None),
        http_get=lambda _url, _timeout: 200,
        http_post=lambda _url, _wav, _data, _timeout: {
            "text": "hello",
            "segments": [{"text": " hello", "start": 1.25, "end": 2.5}],
        },
    )

    payload = server.transcribe_wav_verbose(wav, language="en")

    assert payload["segments"][0]["start"] == 1.25
    server.close()


def test_server_forced_cublas_does_not_demote_to_cpu(tmp_path):
    paths, wav = _stage_server_pack(tmp_path)
    commands = []
    server = WhisperCppServer(
        paths,
        backend="cublas",
        launcher=lambda cmd: (commands.append(cmd), _FakeProc(exit_code=1))[1],
        http_get=lambda _url, _timeout: 200,
        http_post=lambda _url, _wav, _data, _timeout: "ok",
    )

    try:
        server.transcribe_wav(wav)
        raised = False
    except WhisperCppServerError:
        raised = True

    assert raised
    assert len(commands) == 1
    assert "-ng" not in commands[0]
    server.close()


def test_server_auto_retries_cpu_when_gpu_dies(tmp_path):
    paths, wav = _stage_server_pack(tmp_path)
    commands = []
    procs = [_FakeProc(exit_code=1), _FakeProc(exit_code=None)]  # GPU dies, CPU alive

    server = WhisperCppServer(
        paths,
        launcher=lambda cmd: (commands.append(cmd), procs.pop(0))[1],
        http_get=lambda url, timeout: 200,
        http_post=lambda url, wav_path, data, timeout: "ok",
    )

    text = server.transcribe_wav(wav)

    assert text == "ok"
    assert len(commands) == 2
    assert "-ng" not in commands[0]
    assert "-ng" in commands[1]
    server.close()


def test_server_forced_vulkan_does_not_demote_to_cpu(tmp_path):
    paths, wav = _stage_server_pack(tmp_path)
    commands = []
    server = WhisperCppServer(
        paths,
        backend="vulkan",
        launcher=lambda cmd: (commands.append(cmd), _FakeProc(exit_code=1))[1],
        http_get=lambda url, timeout: 200,
        http_post=lambda url, wav_path, data, timeout: "ok",
    )

    try:
        server.transcribe_wav(wav)
        raised = False
    except WhisperCppServerError:
        raised = True

    assert raised
    assert len(commands) == 1          # no CPU retry when GPU is forced
    assert "-ng" not in commands[0]
    server.close()


def test_live_transcriber_prefers_warm_server(tmp_path):
    paths = get_project_paths(tmp_path)
    paths.workspace.mkdir(parents=True, exist_ok=True)

    class _FakeServer:
        def __init__(self):
            self.calls = 0
            self.closed = False

        def available(self):
            return True

        def ensure_running(self):
            return None

        def transcribe_wav(self, wav, *, language=None, prompt=None):
            self.calls += 1
            return "warm text"

        def close(self):
            self.closed = True

    server = _FakeServer()
    live = WhisperCppLiveTranscriber(paths, server=server)
    live.start(LiveOptions(sample_rate=16000))
    live.feed(b"\x00\x01" * 40)
    result = live.finish()
    live.close()

    assert result.text == "warm text"
    assert server.calls == 1
    assert server.closed is True


def test_live_transcriber_warm_preloads_server(tmp_path):
    paths = get_project_paths(tmp_path)

    class _FakeServer:
        def __init__(self):
            self.warmed = 0

        def available(self):
            return True

        def ensure_running(self):
            self.warmed += 1

        def close(self):
            return None

    server = _FakeServer()
    live = WhisperCppLiveTranscriber(paths, server=server)
    live.warm()
    if live._warm_thread is not None:
        live._warm_thread.join(timeout=5)
    live.close()

    assert server.warmed == 1


def test_live_transcriber_falls_back_to_batch_when_server_fails(tmp_path):
    paths = get_project_paths(tmp_path)
    paths.workspace.mkdir(parents=True, exist_ok=True)

    class _DeadServer:
        def available(self):
            return True

        def ensure_running(self):
            raise WhisperCppServerError("boom")

        def transcribe_wav(self, wav, *, language=None, prompt=None):
            raise WhisperCppServerError("boom")

        def close(self):
            return None

    class _FakeBatch:
        def __init__(self):
            self.used = False

        def transcribe(self, path, options):
            self.used = True
            from system_core.providers.base import TranscriptResult

            return TranscriptResult(segments=[], provider="batch")

    batch = _FakeBatch()
    live = WhisperCppLiveTranscriber(paths, provider=batch, server=_DeadServer())
    live.start(LiveOptions(sample_rate=16000))
    live.feed(b"\x00\x01" * 40)
    result = live.finish()
    live.close()

    assert batch.used is True
    assert result.text == ""
