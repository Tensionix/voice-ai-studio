from __future__ import annotations

import json
import queue
from urllib.parse import parse_qs, urlsplit

import pytest

from system_core.core.paths import get_project_paths
from system_core.providers.base import LiveOptions
from system_core.providers.cloud_realtime import (
    ElevenLabsRealtimeLiveTranscriber,
    XAIRealtimeLiveTranscriber,
)


class FakeWebSocket:
    def __init__(self, events):
        self._events = queue.Queue()
        for event in events:
            self._events.put(event)
        self.sent = []
        self.closed = False

    def recv(self):
        return self._events.get(timeout=1)

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True
        self._events.put(None)


def test_xai_realtime_streams_partials_and_final(tmp_path):
    ws = FakeWebSocket(
        [
            {"type": "transcript.created"},
            {"type": "transcript.partial", "text": "при", "is_final": False},
            {"type": "transcript.partial", "text": "привет", "is_final": True, "speech_final": True},
            {"type": "transcript.done", "text": "привет"},
        ]
    )
    partials = []
    segments = []
    live = XAIRealtimeLiveTranscriber(
        get_project_paths(tmp_path),
        connection_factory=lambda _opts: ws,
        final_timeout=1.0,
    )
    live.on_partial = partials.append
    live.on_segment = segments.append

    live.start(LiveOptions(sample_rate=16000))
    live.feed(b"\x01\x02" * 20)
    result = live.finish()

    assert result.text == "привет"
    assert partials
    assert segments == ["привет"]
    assert any(isinstance(item, bytes) for item in ws.sent)
    assert json.loads(ws.sent[-1])["type"] == "audio.done"


def test_xai_speech_final_does_not_end_session_or_duplicate_done(tmp_path):
    ws = FakeWebSocket(
        [
            {"type": "transcript.created"},
            {"type": "transcript.partial", "text": "первая", "is_final": True, "speech_final": True},
            {"type": "transcript.partial", "text": "вторая", "is_final": True, "speech_final": True},
            {"type": "transcript.done", "text": "первая вторая"},
        ]
    )
    segments = []
    live = XAIRealtimeLiveTranscriber(
        get_project_paths(tmp_path),
        connection_factory=lambda _opts: ws,
        final_timeout=1.0,
    )
    live.on_segment = segments.append

    live.start(LiveOptions(sample_rate=16000))
    result = live.finish()

    assert segments == ["первая", "вторая"]
    assert result.text == "первая вторая"


def test_xai_stitched_speech_final_only_commits_new_tail(tmp_path):
    ws = FakeWebSocket(
        [
            {"type": "transcript.created"},
            {"type": "transcript.partial", "text": "один два", "is_final": True, "speech_final": False},
            {"type": "transcript.partial", "text": "Один, два, три", "is_final": True, "speech_final": True},
            {"type": "transcript.done", "text": "Один, два, три"},
        ]
    )
    segments = []
    live = XAIRealtimeLiveTranscriber(
        get_project_paths(tmp_path),
        connection_factory=lambda _opts: ws,
        final_timeout=1.0,
    )
    live.on_segment = segments.append

    live.start(LiveOptions(sample_rate=16000))
    result = live.finish()

    assert segments == ["один два", "три"]
    assert result.text == "один два три"


def test_xai_realtime_rejects_connection_closed_before_ready(tmp_path):
    ws = FakeWebSocket([])
    live = XAIRealtimeLiveTranscriber(
        get_project_paths(tmp_path),
        connection_factory=lambda _opts: ws,
        ready_timeout=2.0,
    )

    with pytest.raises(RuntimeError, match="before ready"):
        live.start(LiveOptions(sample_rate=16000))


def test_elevenlabs_realtime_streams_partials_and_final(tmp_path):
    ws = FakeWebSocket(
        [
            {"message_type": "session_started"},
            {"message_type": "partial_transcript", "text": "hello"},
            {"message_type": "committed_transcript", "text": "hello world"},
        ]
    )
    partials = []
    segments = []
    live = ElevenLabsRealtimeLiveTranscriber(
        get_project_paths(tmp_path),
        connection_factory=lambda _opts: ws,
        final_timeout=0.05,
    )
    live.on_partial = partials.append
    live.on_segment = segments.append

    live.start(LiveOptions(sample_rate=16000))
    live.feed(b"\x01\x02" * 20)
    result = live.finish()

    assert result.text == "hello world"
    assert partials
    assert segments == ["hello world"]
    sent = [json.loads(item) for item in ws.sent]
    assert sent[0]["message_type"] == "input_audio_chunk"
    assert sent[-1]["commit"] is True


def test_cloud_realtime_urls_receive_language_and_dictionary(tmp_path, monkeypatch):
    from system_core.providers import cloud_realtime

    calls = []

    def connect(uri, **kwargs):
        calls.append((uri, kwargs))
        return object()

    monkeypatch.setattr(cloud_realtime, "require_api_key", lambda *_args: "secret")
    monkeypatch.setattr("websockets.sync.client.connect", connect)
    options = LiveOptions(
        language="ru",
        keyterms=("ИАС УГРТ", "API", "PowerShell"),
        sample_rate=16000,
    )

    XAIRealtimeLiveTranscriber(get_project_paths(tmp_path))._default_factory(options)
    ElevenLabsRealtimeLiveTranscriber(get_project_paths(tmp_path))._default_factory(options)

    xai = parse_qs(urlsplit(calls[0][0]).query)
    eleven = parse_qs(urlsplit(calls[1][0]).query)
    assert xai["language"] == ["ru"]
    assert xai["keyterm"] == ["ИАС УГРТ", "API", "PowerShell"]
    assert eleven["language_code"] == ["ru"]
    assert eleven["keyterms"] == ["ИАС УГРТ", "API", "PowerShell"]
