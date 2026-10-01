from fastapi import WebSocket, WebSocketDisconnect
from typing import Set
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

    async def broadcast_task_update(self, task_id: int, status: str, topic: str):
        message = json.dumps({"event": "TASK_UPDATE", "task_id": task_id, "status": status, "topic": topic})
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Failed to send WS message: {e}")
                dead_connections.add(connection)
        for connection in dead_connections:
            self.disconnect(connection)

    async def broadcast_indexing_progress(self, progress: int, message_str: str):
        message = json.dumps({"event": "INDEXING_PROGRESS", "progress": progress, "message": message_str})
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Failed to send WS message: {e}")
                dead_connections.add(connection)
        for connection in dead_connections:
            self.disconnect(connection)

ws_manager = ConnectionManager()
