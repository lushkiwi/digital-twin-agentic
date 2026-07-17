"""LLM configuration endpoints (CONTRACTS §3).

Keys are held in process memory only, never logged, never returned unmasked.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..agents.llm import LLMError, complete
from ..config import llm_config

router = APIRouter()


class ConfigUpdate(BaseModel):
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@router.get("/api/config")
async def get_config() -> dict:
    return llm_config.public_dict()


@router.post("/api/config")
async def set_config(update: ConfigUpdate) -> dict:
    llm_config.update(
        model=update.model, api_key=update.api_key, base_url=update.base_url
    )
    return llm_config.public_dict()


@router.post("/api/config/test")
async def test_config() -> dict:
    start = time.perf_counter()
    try:
        await complete([{"role": "user", "content": "ping"}], tools=None, max_tokens=1)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"ok": True, "error": None, "latency_ms": latency_ms}
    except LLMError as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"ok": False, "error": str(exc), "latency_ms": latency_ms}
