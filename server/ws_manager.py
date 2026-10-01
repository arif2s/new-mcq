from fastapi import WebSocket, WebSocketDisconnect
from typing import List
import json
import logging

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_task_update(self, task_id: int, status: str, topic: str):
        message = json.dumps({"event": "TASK_UPDATE", "task_id": task_id, "status": status, "topic": topic})
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Failed to send WS message: {e}")
                self.disconnect(connection)

    async def broadcast_indexing_progress(self, progress: int, message_str: str):
        message = json.dumps({"event": "INDEXING_PROGRESS", "progress": progress, "message": message_str})
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Failed to send WS message: {e}")
                self.disconnect(connection)

ws_manager = ConnectionManager()
