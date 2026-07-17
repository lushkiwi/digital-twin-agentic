"""POST /api/chat — the interactive agent as an SSE stream (CONTRACTS §3)."""
from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agents.interactive import run_chat

router = APIRouter()


class HistoryTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[HistoryTurn] = Field(default_factory=list)


@router.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    history = [turn.model_dump() for turn in req.history]

    async def event_stream():
        async for event in run_chat(req.message, history):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
