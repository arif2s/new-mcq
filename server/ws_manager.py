"""
Global WebSocket connection manager for live task, session, and indexing progress.
Enforces zero VRAM usage, blueprint message schemas, cross-loop thread safety, and channel isolation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Set, Union

# Enforce Invariant 1: Zero VRAM for backend processes
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from fastapi import WebSocket

try:
    from starlette.websockets import WebSocketState
except ImportError:
    WebSocketState = None

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages active WebSocket connections for:
    - Primary live WebSocket hub (/ws, /ws/)
    - Task Queue state transitions (/ws/tasks)
    - Vault indexing progress updates (/ws/indexing)
    - Knowledge Hub session status updates (/ws)
    """

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()
        self._subscriptions: Dict[WebSocket, str] = {}
        self._conn_locks: Dict[WebSocket, asyncio.Lock] = {}
        self._state_lock = threading.RLock()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def main_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._main_loop

    @main_loop.setter
    def main_loop(self, loop: Optional[asyncio.AbstractEventLoop]) -> None:
        self._main_loop = loop

    def set_main_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Explicitly binds the primary FastAPI asyncio event loop during lifespan startup (Invariant 16)."""
        if loop is not None:
            self._main_loop = loop
        else:
            self._capture_main_loop()

    def _capture_main_loop(self) -> None:
        """Captures the primary running asyncio loop from the FastAPI thread."""
        if self._main_loop is None or self._main_loop.is_closed() or not self._main_loop.is_running():
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    async def connect(self, websocket: WebSocket, channel: str = "all") -> None:
        """
        Accepts and registers a new WebSocket connection with channel isolation.
        Ensures idempotent accept states and per-connection write serialization locks.
        """
        self._capture_main_loop()

        try:
            if WebSocketState is not None and hasattr(websocket, "client_state"):
                if websocket.client_state == WebSocketState.CONNECTING:
                    await websocket.accept()
            else:
                await websocket.accept()
        except Exception as exc:
            logger.debug("WebSocket accept failed or already connected: %s", exc)

        with self._state_lock:
            self.active_connections.add(websocket)
            self._subscriptions[websocket] = str(channel or "all").strip().lower()
            if websocket not in self._conn_locks:
                self._conn_locks[websocket] = asyncio.Lock()

    def disconnect(self, websocket: WebSocket) -> None:
        """Safely removes an active WebSocket connection and purges associated locks."""
        with self._state_lock:
            self.active_connections.discard(websocket)
            self._subscriptions.pop(websocket, None)
            self._conn_locks.pop(websocket, None)

    async def _send_safe(self, conn: WebSocket, message: str) -> None:
        """Serializes writes per connection with a timeout guard to prevent frame collisions."""
        lock = self._conn_locks.get(conn)
        try:
            if (
                WebSocketState is not None
                and getattr(conn, "client_state", None) == WebSocketState.DISCONNECTED
            ):
                self.disconnect(conn)
                return

            if lock:
                async with lock:
                    await asyncio.wait_for(conn.send_text(message), timeout=1.5)
            else:
                await asyncio.wait_for(conn.send_text(message), timeout=1.5)
        except Exception as exc:
            logger.debug("WebSocket send failed; purging connection: %s", exc)
            self.disconnect(conn)

    async def _broadcast_direct(self, payload: Dict[str, Any], channel: str = "all") -> None:
        """Delivers JSON message to target channel connections on the primary event loop."""
        with self._state_lock:
            if not self.active_connections:
                return

            target_conns: List[WebSocket] = []
            req_channel = str(channel or "all").strip().lower()

            for conn in list(self.active_connections):
                sub = self._subscriptions.get(conn, "all")
                if req_channel == "all" or sub == "all" or sub == req_channel:
                    target_conns.append(conn)

        if not target_conns:
            return

        message = json.dumps(payload, default=str)
        await asyncio.gather(
            *(self._send_safe(conn, message) for conn in target_conns),
            return_exceptions=True,
        )

    async def _broadcast(self, payload: Dict[str, Any], channel: str = "all") -> None:
        """
        Thread-safe broadcast dispatcher (Invariant 18).
        Automatically bridges across thread/loop boundaries via run_coroutine_threadsafe.
        """
        self._capture_main_loop()

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if (
            self._main_loop
            and not self._main_loop.is_closed()
            and self._main_loop.is_running()
            and (current_loop is None or current_loop is not self._main_loop)
        ):
            future = asyncio.run_coroutine_threadsafe(
                self._broadcast_direct(payload, channel),
                self._main_loop,
            )
            if current_loop is not None:
                try:
                    await asyncio.wrap_future(future)
                except Exception as exc:
                    logger.debug("Cross-loop WebSocket broadcast error: %s", exc)
            return

        await self._broadcast_direct(payload, channel)

    def broadcast_sync(self, payload: Dict[str, Any], channel: str = "all") -> None:
        """Non-blocking thread-safe cross-loop dispatcher for synchronous background workers."""
        self._capture_main_loop()
        if (
            self._main_loop
            and not self._main_loop.is_closed()
            and self._main_loop.is_running()
        ):
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._broadcast_direct(payload, channel),
                    self._main_loop,
                )
                future.add_done_callback(
                    lambda f: f.exception() if not f.cancelled() else None
                )
            except Exception as exc:
                logger.debug("broadcast_sync dispatch error: %s", exc)

    @staticmethod
    def _build_task_payload(
        task_id: int = 0,
        status: str = "pending",
        topic: str = "",
        session_id: Optional[str] = None,
        progress: int = 0,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        p_val = int(progress if progress is not None else kwargs.get("percent", 0))
        return {
            "type": "task_update",
            "event": "TASK_UPDATE",
            "task_id": int(task_id or 0),
            "status": str(status or "pending"),
            "topic": str(topic or ""),
            "session_id": str(session_id) if session_id is not None else None,
            "progress": p_val,
        }

    @staticmethod
    def _build_session_payload(
        session_id: str,
        *args: Any,
        refs_status: Optional[str] = None,
        llm_status: Optional[str] = None,
        topics_reviewed: Optional[bool] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        actual_session_id = str(session_id or "")
        actual_topics_reviewed = False
        actual_refs_status = "none"
        actual_llm_status = "none"

        if len(args) == 3:
            actual_topics_reviewed = bool(args[0])
            actual_refs_status = str(args[1] or "none")
            actual_llm_status = str(args[2] or "none")
        elif len(args) == 2:
            if isinstance(args[0], bool):
                actual_topics_reviewed = bool(args[0])
                actual_refs_status = str(args[1] or "none")
                actual_llm_status = str(llm_status or kwargs.get("llm_status", "none"))
            else:
                actual_refs_status = str(args[0] or "none")
                actual_llm_status = str(args[1] or "none")
                actual_topics_reviewed = (
                    bool(topics_reviewed)
                    if topics_reviewed is not None
                    else bool(kwargs.get("topics_reviewed", False))
                )
        elif len(args) == 1:
            if isinstance(args[0], bool):
                actual_topics_reviewed = bool(args[0])
                actual_refs_status = str(refs_status or kwargs.get("refs_status", "none"))
                actual_llm_status = str(llm_status or kwargs.get("llm_status", "none"))
            else:
                actual_refs_status = str(args[0] or "none")
                actual_llm_status = str(llm_status or kwargs.get("llm_status", "none"))
                actual_topics_reviewed = (
                    bool(topics_reviewed)
                    if topics_reviewed is not None
                    else bool(kwargs.get("topics_reviewed", False))
                )
        else:
            actual_refs_status = str(refs_status or kwargs.get("refs_status", "none"))
            actual_llm_status = str(llm_status or kwargs.get("llm_status", "none"))
            actual_topics_reviewed = (
                bool(topics_reviewed)
                if topics_reviewed is not None
                else bool(kwargs.get("topics_reviewed", False))
            )

        return {
            "type": "session_update",
            "event": "SESSION_STATUS_UPDATE",
            "session_id": actual_session_id,
            "refs_status": actual_refs_status,
            "llm_status": actual_llm_status,
            "topics_reviewed": actual_topics_reviewed,
        }

    @staticmethod
    def _build_indexing_payload(
        source_id: Union[str, int] = "",
        *args: Any,
        percent: Optional[int] = None,
        message: Optional[str] = None,
        status: str = "running",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if isinstance(source_id, int):
            act_percent = int(source_id)
            act_source_id = str(kwargs.get("source_id", "default"))
            act_message = (
                str(args[0])
                if args
                else str(message or kwargs.get("message_str", ""))
            )
            act_status = (
                str(args[1]) if len(args) > 1 else str(kwargs.get("status", status))
            )
        else:
            act_source_id = str(source_id or kwargs.get("source_id", "default"))
            if args:
                act_percent = int(args[0])
                act_message = str(args[1]) if len(args) > 1 else str(message or "")
                act_status = str(args[2]) if len(args) > 2 else str(status)
            else:
                act_percent = int(
                    percent if percent is not None else kwargs.get("progress", 0)
                )
                act_message = str(
                    message if message is not None else kwargs.get("message_str", "")
                )
                act_status = str(status or "running")

        return {
            "type": "indexing_progress",
            "event": "INDEXING_PROGRESS",
            "source_id": act_source_id,
            "percent": act_percent,
            "progress": act_percent,
            "message": act_message,
            "status": act_status,
        }

    async def broadcast_task_update(
        self,
        task_id: int,
        status: str,
        topic: str = "",
        session_id: Optional[str] = None,
        progress: int = 0,
        **kwargs: Any,
    ) -> None:
        """Broadcasts Task Queue transitions matching Blueprint Section 4 contract verbatim."""
        payload = self._build_task_payload(
            task_id=task_id,
            status=status,
            topic=topic,
            session_id=session_id,
            progress=progress,
            **kwargs,
        )
        await self._broadcast(payload, channel="tasks")

    async def broadcast_session_update(
        self,
        session_id: str,
        *args: Any,
        refs_status: Optional[str] = None,
        llm_status: Optional[str] = None,
        topics_reviewed: Optional[bool] = None,
        **kwargs: Any,
    ) -> None:
        """Broadcasts session synthesis status matching Blueprint Section 4 contract verbatim."""
        payload = self._build_session_payload(
            session_id,
            *args,
            refs_status=refs_status,
            llm_status=llm_status,
            topics_reviewed=topics_reviewed,
            **kwargs,
        )
        await self._broadcast(payload, channel="all")

    async def broadcast_indexing_progress(
        self,
        source_id: Union[str, int] = "",
        *args: Any,
        percent: Optional[int] = None,
        message: Optional[str] = None,
        status: str = "running",
        **kwargs: Any,
    ) -> None:
        """Broadcasts indexing progress matching Blueprint Section 4 contract verbatim."""
        payload = self._build_indexing_payload(
            source_id,
            *args,
            percent=percent,
            message=message,
            status=status,
            **kwargs,
        )
        await self._broadcast(payload, channel="indexing")

    def broadcast_task_update_sync(
        self,
        task_id: int = 0,
        status: str = "pending",
        topic: str = "",
        session_id: Optional[str] = None,
        progress: int = 0,
        **kwargs: Any,
    ) -> None:
        payload = self._build_task_payload(
            task_id=task_id,
            status=status,
            topic=topic,
            session_id=session_id,
            progress=progress,
            **kwargs,
        )
        self.broadcast_sync(payload, channel="tasks")

    def broadcast_session_update_sync(
        self,
        session_id: str = "",
        *args: Any,
        refs_status: Optional[str] = None,
        llm_status: Optional[str] = None,
        topics_reviewed: Optional[bool] = None,
        **kwargs: Any,
    ) -> None:
        payload = self._build_session_payload(
            session_id,
            *args,
            refs_status=refs_status,
            llm_status=llm_status,
            topics_reviewed=topics_reviewed,
            **kwargs,
        )
        self.broadcast_sync(payload, channel="all")

    def broadcast_indexing_progress_sync(
        self,
        source_id: Union[str, int] = "",
        *args: Any,
        percent: Optional[int] = None,
        message: Optional[str] = None,
        status: str = "running",
        **kwargs: Any,
    ) -> None:
        payload = self._build_indexing_payload(
            source_id,
            *args,
            percent=percent,
            message=message,
            status=status,
            **kwargs,
        )
        self.broadcast_sync(payload, channel="indexing")


# Canonical singleton instances ensuring zero import breakages (Section 3)
manager = ConnectionManager()
ws_manager = manager

__all__ = ["ConnectionManager", "manager", "ws_manager"]