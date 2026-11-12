import asyncio
import json
import logging
from typing import List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.rate_analyzer import rate_analyzer
from app.services.nexus_client import nexus_client
from app.services.scheduler import scheduler

logger = logging.getLogger("beldex_sentinel.websocket")

router = APIRouter(tags=["WebSocket"])

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Active: {len(self.active_connections)}")

    async def broadcast_json(self, message: dict):
        text = json.dumps(message)
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(text)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.disconnect(d)

manager = ConnectionManager()

# Hook into scheduler broadcast
async def _scheduler_broadcast_hook(payload: dict):
    await manager.broadcast_json(payload)

scheduler.register_broadcast_callback(_scheduler_broadcast_hook)

@router.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial state on connect
        analysis = await rate_analyzer.analyze_current_rate()
        initial_payload = {
            "type": "initial_state",
            "analysis": analysis.model_dump() if analysis else None,
            "nexus_state": nexus_client.state.model_dump(),
            "scheduler_running": scheduler.is_running
        }
        await websocket.send_text(json.dumps(initial_payload))

        while True:
            # Keep alive / listen for ping
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.debug(f"WebSocket error: {e}")
        manager.disconnect(websocket)
