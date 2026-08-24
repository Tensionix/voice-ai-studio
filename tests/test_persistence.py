from pathlib import Path

from system_core.core.persistence import (
    COMPLETED,
    FAILED,
    PENDING,
    PROCESSING,
    SKIPPED,
    JobStore,
)


def _store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def test_upsert_is_idempotent(tmp_path: Path):
    with _store(tmp_path) as store:
        a = store.upsert_job("C:/a/file.mp3")
        b = store.upsert_job("C:/a/file.mp3")
        assert a.id == b.id
        assert a.status == PENDING


def test_status_transitions(tmp_path: Path):
    with _store(tmp_path) as store:
        job = store.upsert_job("C:/a/file.mp3")
        store.mark_processing(job.id)
        assert store.get_job("C:/a/file.mp3").status == PROCESSING
        store.mark_completed(job.id, {"json": "file.transcript.json"})
        done = store.get_job("C:/a/file.mp3")
        assert done.status == COMPLETED
        assert done.outputs["json"] == "file.transcript.json"


def test_mark_failed_and_skipped(tmp_path: Path):
    with _store(tmp_path) as store:
        j1 = store.upsert_job("C:/a/1.mp3")
        store.mark_failed(j1.id, "boom")
        assert store.get_job("C:/a/1.mp3").status == FAILED
        assert store.get_job("C:/a/1.mp3").error == "boom"
        j2 = store.upsert_job("C:/a/2.mp3")
        store.mark_skipped(j2.id)
        assert store.get_job("C:/a/2.mp3").status == SKIPPED


def test_reset_stale_processing_enables_resume(tmp_path: Path):
    db = tmp_path / "jobs.sqlite3"
    with JobStore(db) as store:
        job = store.upsert_job("C:/a/file.mp3")
        store.mark_processing(job.id)
    # Simulate restart after a crash mid-processing.
    with JobStore(db) as store:
        recovered = store.reset_stale_processing()
        assert recovered == 1
        assert store.get_job("C:/a/file.mp3").status == PENDING


def test_chunk_cache_roundtrip(tmp_path: Path):
    with _store(tmp_path) as store:
        job = store.upsert_job("C:/a/file.mp3")
        store.upsert_chunk(job.id, 0, 0.0, 600.0)
        assert store.get_chunk_transcript(job.id, 0) is None  # pending -> no cache
        store.save_chunk_transcript(job.id, 0, {"segments": [{"index": 0, "start": 0, "end": 1, "text": "hi"}]})
        cached = store.get_chunk_transcript(job.id, 0)
        assert cached is not None
        assert cached["segments"][0]["text"] == "hi"


def test_clear_chunks_supports_force(tmp_path: Path):
    with _store(tmp_path) as store:
        job = store.upsert_job("C:/a/file.mp3")
        store.upsert_chunk(job.id, 0, 0.0, 1.0)
        store.save_chunk_transcript(job.id, 0, {"segments": []})
        store.clear_chunks(job.id)
        assert store.get_chunk_transcript(job.id, 0) is None


def test_delete_job_cascades_cached_chunks(tmp_path: Path):
    db = tmp_path / "jobs.sqlite3"
    source = tmp_path / "meeting.wav"
    with JobStore(db) as store:
        job = store.upsert_job(str(source))
        store.upsert_chunk(job.id, 0, 0.0, 1.0)
        store.save_chunk_transcript(job.id, 0, {"segments": []})
        assert store.delete_job(str(source)) is True
        assert store.get_job(str(source)) is None
        assert store.get_chunk_transcript(job.id, 0) is None
