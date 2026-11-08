import asyncio
from pathlib import Path
import pytest

from core.database import atomic_transaction, batch_execute, connect_wal
from core.model_lifecycle import LazyModelHolder
from core.queue_manager import cancel_async_job, persist_job_state, recover_interrupted_jobs
from core.reporter import render_safe_report_html, sanitize_report_path
from parsers.embedding_engine import encode_length_bucketed
from parsers.reranker import reciprocal_rank_fusion
from parsers.tantivy_engine import cleanup_stale_tantivy_lock


def test_sqlite_wal_and_atomic_batch_transactions(tmp_path: Path):
    db_file = tmp_path / "test_wal.db"
    conn = connect_wal(db_file)
    try:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert mode.lower() == "wal"

        with atomic_transaction(conn) as cur:
            cur.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);")

        batch_execute(conn, "INSERT INTO items (id, name) VALUES (?, ?);", [(1, "Alpha"), (2, "Beta")])
        rows = conn.execute("SELECT name FROM items ORDER BY id;").fetchall()
        assert [r["name"] for r in rows] == ["Alpha", "Beta"]

        with pytest.raises(RuntimeError):
            with atomic_transaction(conn) as cur:
                cur.execute("INSERT INTO items (id, name) VALUES (3, 'Gamma');")
                raise RuntimeError("Simulated failure")

        count = conn.execute("SELECT COUNT(*) FROM items;").fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_queue_persistence_recovery_and_cancellation(tmp_path: Path):
    conn = connect_wal(tmp_path / "queue.db")
    try:
        persist_job_state(conn, "job-1", "pdf_parse", "RUNNING", progress=45)
        persist_job_state(conn, "job-2", "synthesis", "COMPLETED", progress=100)

        recovered = recover_interrupted_jobs(conn, target_status="PENDING")
        assert recovered == 1

        status = conn.execute("SELECT status FROM queue_jobs WHERE job_id = 'job-1';").fetchone()["status"]
        assert status == "PENDING"
    finally:
        conn.close()

    async def _dummy_worker():
        await asyncio.sleep(10)

    async def _run_cancel_check():
        task = asyncio.create_task(_dummy_worker())
        registry = {"job-1": task}
        assert cancel_async_job(registry, "job-1") is True
        await asyncio.sleep(0)
        assert task.cancelled()

    asyncio.run(_run_cancel_check())


def test_reporter_blocks_stopwords_and_path_traversal(tmp_path: Path):
    assert sanitize_report_path("does", tmp_path) is None
    assert sanitize_report_path("exits", tmp_path) is None
    assert sanitize_report_path("restarts", tmp_path) is None

    valid_path = sanitize_report_path("Acute Pericarditis", tmp_path)
    assert valid_path is not None
    assert valid_path.name == "acute_pericarditis_report.html"
    assert valid_path.parent == tmp_path.resolve()

    html_out = render_safe_report_html("<script>alert(1)</script>", "<b>bold</b>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_stale_tantivy_lock_cleanup(tmp_path: Path):
    lock_path = tmp_path / ".tantivy-meta.lock"
    lock_path.touch()
    assert lock_path.exists()
    assert cleanup_stale_tantivy_lock(tmp_path) is True
    assert not lock_path.exists()


def test_reciprocal_rank_fusion_and_length_bucketing():
    bm25_hits = [{"id": "doc_b", "title": "B"}, {"id": "doc_a", "title": "A"}]
    vec_hits = [{"id": "doc_a", "title": "A"}, {"id": "doc_c", "title": "C"}]
    alias_hits = [{"id": "doc_a", "title": "A"}]

    fused = reciprocal_rank_fusion([bm25_hits, vec_hits, alias_hits], k=60, top_n=2)
    assert len(fused) == 2
    assert fused[0]["id"] == "doc_a"
    assert fused[0]["rrf_score"] > fused[1]["rrf_score"]

    texts = ["very long sentence here", "a", "medium text"]
    vecs = encode_length_bucketed(texts, lambda batch: [len(s) for s in batch], batch_size=2)
    assert vecs == [len(s) for s in texts]


def test_lazy_model_holder_lifecycle():
    calls = 0

    def _loader():
        nonlocal calls
        calls += 1
        return {"weights": [1, 2, 3]}

    holder = LazyModelHolder("test_model", _loader, idle_ttl_seconds=0)
    assert holder.is_loaded is False
    assert holder.get() == {"weights": [1, 2, 3]}
    assert calls == 1
    assert holder.is_loaded is True
    assert holder.unload_if_idle(ttl_override=0.0) is True
    assert holder.is_loaded is False
