"""WebSocket fan-out hub.

One connection per browser tab.  The server pushes JSON text frames (telemetry,
observation, status per CONTRACTS §3).  There is deliberately NO telemetry backfill
over WS — the frontend backfills via REST (`GET /api/telemetry`) and then goes live
here.  On connect we send only the current status frame.
"""
from __future__ import annotations

import asyncio
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .state import app_state

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast(self, frame: dict) -> None:
        """Send ``frame`` to every live connection; drop dead ones silently."""
        async with self._lock:
            targets = list(self._connections)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(frame)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)


manager = ConnectionManager()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        # Send the current connectivity status immediately (no telemetry backfill).
        await ws.send_json(app_state.status_frame())
        # Keep the connection open; we don't expect inbound messages, but draining
        # them keeps the socket healthy and lets us notice a client disconnect.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.disconnect(ws)
