from system_core.core.models import Segment
from system_core.writers.subtitle_format import (
    SubtitleSettings,
    build_cues,
    format_timestamp,
    wrap_text,
)


def test_format_timestamp_srt_and_vtt():
    assert format_timestamp(3661.5, comma=True) == "01:01:01,500"
    assert format_timestamp(3661.5, comma=False) == "01:01:01.500"
    assert format_timestamp(0, comma=True) == "00:00:00,000"
    assert format_timestamp(-5, comma=False) == "00:00:00.000"


def test_wrap_text_respects_line_limits():
    text = "one two three four five six seven eight nine ten"
    lines = wrap_text(text, max_chars=12, max_lines=2)
    assert len(lines) <= 2
    assert lines[0] == "one two"  # greedy fill under 12 chars


def test_wrap_text_overflow_collapses_into_last_line():
    text = "alpha beta gamma delta epsilon zeta eta theta"
    lines = wrap_text(text, max_chars=10, max_lines=2)
    assert len(lines) == 2
    assert "theta" in lines[-1]


def test_build_cues_clamps_duration_and_indexes():
    segments = [
        Segment(index=0, start=0.0, end=0.2, text="too short"),     # below min -> extended
        Segment(index=1, start=10.0, end=30.0, text="too long"),    # above max -> trimmed
        Segment(index=2, start=40.0, end=40.0, text="", speaker="S1"),  # empty -> dropped
    ]
    settings = SubtitleSettings(min_duration=1.0, max_duration=7.0)
    cues = build_cues(segments, settings)
    assert len(cues) == 2
    assert cues[0].index == 1 and cues[1].index == 2
    assert abs((cues[0].end - cues[0].start) - 1.0) < 1e-6
    assert abs((cues[1].end - cues[1].start) - 7.0) < 1e-6


def test_build_cues_includes_speaker_when_enabled():
    segments = [Segment(index=0, start=0.0, end=3.0, text="hi", speaker="Speaker 1")]
    cues = build_cues(segments, SubtitleSettings(include_speakers=True))
    assert cues[0].lines[0].startswith("Speaker 1:")
