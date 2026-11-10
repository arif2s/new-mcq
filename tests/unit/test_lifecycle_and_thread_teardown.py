import asyncio
import os
import sys
import threading
from unittest.mock import patch
import pytest

from agents.tema_q.pipeline import (
    _ACTIVE_CLIENTS,
    close_http_client,
    close_thread_local_http_client,
    get_http_client,
)
from core.model_lifecycle import LazyModelHolder, get_gpu_enabled_subprocess_env
from core.queue_manager import PriorityQueueWorker


def test_gpu_enabled_subprocess_env_and_lazy_torch():
    # 1. Verify subprocess GPU environment scrubbing
    base_env = {"CUDA_VISIBLE_DEVICES": "", "PATH": "/usr/bin"}
    sub_env = get_gpu_enabled_subprocess_env(base_env)
    assert "CUDA_VISIBLE_DEVICES" not in sub_env
    assert sub_env["PATH"] == "/usr/bin"

    base_env_neg1 = {"CUDA_VISIBLE_DEVICES": "-1", "USER": "test"}
    sub_env_neg1 = get_gpu_enabled_subprocess_env(base_env_neg1)
    assert "CUDA_VISIBLE_DEVICES" not in sub_env_neg1
    assert sub_env_neg1["USER"] == "test"

    base_env_valid = {"CUDA_VISIBLE_DEVICES": "0,1", "USER": "test"}
    sub_env_valid = get_gpu_enabled_subprocess_env(base_env_valid)
    assert sub_env_valid.get("CUDA_VISIBLE_DEVICES") == "0,1"

    # With os.environ
    orig_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    try:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        sub_env_default = get_gpu_enabled_subprocess_env()
        assert "CUDA_VISIBLE_DEVICES" not in sub_env_default
        assert os.environ["CUDA_VISIBLE_DEVICES"] == ""
    finally:
        if orig_cuda is not None:
            os.environ["CUDA_VISIBLE_DEVICES"] = orig_cuda
        else:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)

    # 2. Verify zero torch import on CPU release
    holder = LazyModelHolder("cpu_mock", lambda: {"session": "loaded"})
    assert holder.get()["session"] == "loaded"
    with patch.dict(sys.modules, {"torch": None}):
        assert holder.release_memory() is True
    assert not holder.is_loaded


def test_thread_local_http_client_teardown():
    client_refs = []

    def worker_thread_simulation():
        t_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(t_loop)
        try:
            async def _init_client():
                c = get_http_client()
                client_refs.append(c)
                assert c in _ACTIVE_CLIENTS
                assert not c.is_closed

            t_loop.run_until_complete(_init_client())
        finally:
            t_loop.run_until_complete(close_thread_local_http_client())
            t_loop.run_until_complete(t_loop.shutdown_asyncgens())
            t_loop.close()

    t = threading.Thread(target=worker_thread_simulation)
    t.start()
    t.join(timeout=2.0)

    assert len(client_refs) == 1
    assert client_refs[0].is_closed
    assert client_refs[0] not in _ACTIVE_CLIENTS


def test_close_http_client_with_closed_loop_client():
    # If a client is registered but its loop is already closed, close_http_client should not raise
    t_loop = asyncio.new_event_loop()
    client = None

    def create_client_on_t_loop():
        nonlocal client
        asyncio.set_event_loop(t_loop)
        client = get_http_client()
        t_loop.close()

    t = threading.Thread(target=create_client_on_t_loop)
    t.start()
    t.join(timeout=2.0)

    assert client is not None
    # Now call close_http_client on a new loop
    asyncio.run(close_http_client())


def test_priority_queue_worker_loop_binding_and_stop():
    custom_loop = asyncio.new_event_loop()
    try:
        worker = PriorityQueueWorker()
        with patch.object(worker, "_recover_interrupted_tasks"), \
             patch.object(worker, "_claim_highest_priority_task", return_value=None):
            worker.start(loop=custom_loop)
            assert worker.main_loop is custom_loop
            worker.stop()
            assert worker.running is False
            assert not worker.process_thread.is_alive()
            assert not worker.prune_thread.is_alive()
    finally:
        custom_loop.close()
