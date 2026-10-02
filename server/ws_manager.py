import asyncio
from fastapi import WebSocket
from typing import Set, Dict, Any
import json
import logging

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def _broadcast(self, payload: Dict[str, Any]):
        """Internal helper to broadcast a message concurrently to all active clients."""
        if not self.active_connections:
            return

        message = json.dumps(payload)

        # Take a snapshot of the set to prevent RuntimeError if the set mutates during execution
        connections = list(self.active_connections)

        # Execute all socket transmissions concurrently rather than sequentially
        results = await asyncio.gather(
            *(conn.send_text(message) for conn in connections),
            return_exceptions=True
        )

        # Batch cleanup of dead or dropped connections
        for conn, result in zip(connections, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to send WS message: {result}")
                self.disconnect(conn)

    async def broadcast_task_update(self, task_id: int, status: str, topic: str):
        await self._broadcast({
            "event": "TASK_UPDATE",
            "task_id": task_id,
            "status": status,
            "topic": topic
        })

    async def broadcast_indexing_progress(self, progress: int, message_str: str):
        await self._broadcast({
            "event": "INDEXING_PROGRESS",
            "progress": progress,
            "message": message_str
        })

ws_manager = ConnectionManager()
