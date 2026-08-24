from system_core.core.models import Segment
from system_core.pipeline.timeline import merge_chunk_segments


def test_merge_applies_offsets_and_reindexes():
    chunk0 = [Segment(index=0, start=0.0, end=2.0, text="a"),
              Segment(index=1, start=2.0, end=4.0, text="b")]
    chunk1 = [Segment(index=0, start=0.0, end=2.0, text="c")]
    merged = merge_chunk_segments([(0.0, chunk0), (10.0, chunk1)])
    assert [s.text for s in merged] == ["a", "b", "c"]
    assert [s.index for s in merged] == [0, 1, 2]
    assert merged[2].start == 10.0  # offset applied


def test_merge_sorts_by_start():
    later = [Segment(index=0, start=0.0, end=1.0, text="late")]
    early = [Segment(index=0, start=0.0, end=1.0, text="early")]
    merged = merge_chunk_segments([(100.0, later), (0.0, early)])
    assert [s.text for s in merged] == ["early", "late"]


def test_merge_dedupes_overlap_duplicates():
    chunk0 = [Segment(index=0, start=8.0, end=10.0, text="overlap line")]
    # second chunk starts at 8s, repeats the same line at the boundary
    chunk1 = [Segment(index=0, start=0.0, end=2.0, text="overlap line"),
              Segment(index=1, start=2.0, end=4.0, text="fresh line")]
    merged = merge_chunk_segments([(0.0, chunk0), (8.0, chunk1)])
    texts = [s.text for s in merged]
    assert texts.count("overlap line") == 1
    assert "fresh line" in texts


def test_merge_drops_empty_segments():
    chunk = [Segment(index=0, start=0.0, end=1.0, text="   "),
             Segment(index=1, start=1.0, end=2.0, text="real")]
    merged = merge_chunk_segments([(0.0, chunk)])
    assert [s.text for s in merged] == ["real"]
