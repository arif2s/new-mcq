import asyncio
import io
from pathlib import Path
import pytest

from server.routes_mcq import grade_mcq_and_compute_fsrs
from server.routes_models import mask_api_key, sanitize_model_config_dict
from server.routes_notes import build_note_index_document
from server.routes_search import execute_parallel_hybrid_search
from server.routes_sources import stream_upload_to_disk, validate_safe_source_path
from server.routes_state import merge_state_patch
from server.ws_manager import broadcast_concurrently


class _DummyUploadFile:
    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)

    async def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


class _HealthyWebSocket:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.received.append(data)


class _StalledWebSocket:
    async def send_json(self, _data: dict) -> None:
        await asyncio.sleep(5.0)


def test_ws_manager_prunes_stalled_clients_without_blocking():
    async def _run():
        healthy = _HealthyWebSocket()
        stalled = _StalledWebSocket()
        pool = {healthy, stalled}

        dead = await broadcast_concurrently(pool, {"event": "progress", "pct": 50}, timeout_sec=0.05)
        assert stalled in dead
        assert stalled not in pool
        assert healthy in pool
        assert healthy.received == [{"event": "progress", "pct": 50}]

    asyncio.run(_run())


def test_chunked_upload_streaming_and_path_traversal_guard(tmp_path: Path):
    async def _run():
        raw_bytes = b"medical-pdf-content-" * 1024
        dummy_upload = _DummyUploadFile(raw_bytes)
        dest = tmp_path / "uploads" / "sample.pdf"
        meta = await stream_upload_to_disk(dummy_upload, dest, chunk_size=512)
        assert meta["size_bytes"] == len(raw_bytes)
        assert len(meta["sha256"]) == 64
        assert dest.read_bytes() == raw_bytes

    asyncio.run(_run())

    allowed_dir = tmp_path / "vault"
    allowed_dir.mkdir()
    safe_file = allowed_dir / "note.md"
    safe_file.write_text("# Note", encoding="utf-8")

    assert validate_safe_source_path(safe_file, allowed_roots=[allowed_dir]) == safe_file.resolve()
    with pytest.raises(ValueError):
        validate_safe_source_path(tmp_path / "outside.md", allowed_roots=[allowed_dir])


def test_parallel_hybrid_search_tolerates_slow_source_and_fuses_rrf():
    async def _fast_bm25():
        return [{"id": "chunk_1", "title": "Tamponade"}, {"id": "chunk_2", "title": "Pericarditis"}]

    async def _fast_vec():
        return [{"id": "chunk_2", "title": "Pericarditis"}, {"id": "chunk_3", "title": "Effusion"}]

    async def _slow_stalled_engine():
        await asyncio.sleep(5.0)
        return [{"id": "chunk_99"}]

    async def _run():
        results = await execute_parallel_hybrid_search(
            "pericarditis",
            [_fast_bm25(), _fast_vec(), _slow_stalled_engine()],
            top_n=5,
            timeout_sec=0.05,
        )
        assert len(results) == 3
        assert results[0]["id"] == "chunk_2"
        assert results[0]["rrf_score"] > results[1]["rrf_score"]

    asyncio.run(_run())


def test_api_key_masking_state_merge_and_mcq_grading():
    assert mask_api_key("sk-1234567890abcdef") == "sk-1...cdef"
    cfg = sanitize_model_config_dict({"provider": "lm_studio", "openai_api_key": "sk-9876543210fedcba"})
    assert cfg["openai_api_key"] == "sk-9...dcba"
    assert cfg["provider"] == "lm_studio"

    initial_state = {"theme": "dark", "quiz": {"score": 10, "streak": 2}, "obsolete": True}
    patched = merge_state_patch(initial_state, {"quiz": {"streak": 3}, "obsolete": None})
    assert patched == {"theme": "dark", "quiz": {"score": 10, "streak": 3}}

    graded = grade_mcq_and_compute_fsrs("Option A", "Option A", response_time_ms=3500)
    assert graded["is_correct"] is True
    assert graded["fsrs_state"]["interval_days"] >= 1

    note_doc = build_note_index_document("42", "Heart Failure", "### Overview", tags=["cardiology"])
    assert note_doc["id"] == "note:42"
