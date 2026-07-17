"""FastAPI application factory + lifespan.

The app MUST start cleanly and serve ``/api/health`` even when Ditto is completely
down (the SSE consumer retries forever in the background) and when no LLM key is
configured (chat emits an ``error`` event; the sleeper's reflection is skipped but
its threshold rules keep working).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agents.sleeper import sleeper
from .config import settings
from .ingest import run_frame_flusher, run_ingest
from .routes.chat import router as chat_router
from .routes.control import router as control_router
from .routes.llm_config import router as config_router
from .routes.system import router as system_router
from .state import app_state
from .twin.client import ditto_client
from .ws import manager
from .ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Wire the broadcast choke point to the WS hub before any producer runs.
    app_state.set_broadcaster(manager.broadcast)

    tasks = [
        asyncio.create_task(run_ingest(), name="ditto-ingest"),
        asyncio.create_task(run_frame_flusher(), name="frame-flusher"),
        asyncio.create_task(sleeper.reflect_loop(), name="sleeper-reflect"),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
        await ditto_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Digital Twin Backend", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system_router)
    app.include_router(chat_router)
    app.include_router(config_router)
    app.include_router(control_router)
    app.include_router(ws_router)
    return app


app = create_app()
