import asyncio
import json
import logging
from typing import Set, Dict, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages active WebSocket connections for:
    - Real-time background queue updates (TASK_UPDATE)
    - Knowledge Hub 1-2-3 circle status transitions (SESSION_STATUS_UPDATE)
    - Vault indexing progress bars (INDEXING_PROGRESS)
    """

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def _broadcast(self, payload: Dict[str, Any]):
        if not self.active_connections:
            return

        message = json.dumps(payload)
        connections = list(self.active_connections)

        results = await asyncio.gather(
            *(conn.send_text(message) for conn in connections),
            return_exceptions=True,
        )

        for conn, result in zip(connections, results):
            if isinstance(result, Exception):
                logger.debug(f"Removing closed WebSocket connection: {result}")
                self.disconnect(conn)

    async def broadcast_task_update(self, task_id: int, status: str, topic: str = ""):
        await self._broadcast({
            "event": "TASK_UPDATE",
            "task_id": task_id,
            "status": status,
            "topic": topic,
        })

    async def broadcast_session_update(
        self,
        session_id: str,
        topics_reviewed: bool,
        refs_status: str,
        llm_status: str,
    ):
        await self._broadcast({
            "event": "SESSION_STATUS_UPDATE",
            "session_id": session_id,
            "topics_reviewed": bool(topics_reviewed),
            "refs_status": refs_status,
            "llm_status": llm_status,
        })

    async def broadcast_indexing_progress(self, progress: int, message_str: str):
        await self._broadcast({
            "event": "INDEXING_PROGRESS",
            "progress": int(progress),
            "message": message_str,
        })


ws_manager = ConnectionManager()
