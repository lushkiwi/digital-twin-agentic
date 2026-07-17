"""Device simulator entrypoint (CONTRACTS.md §2).

Run via: uvicorn sim:app --app-dir device-sim --port 9001

The physics loop ticks on its own asyncio task at TELEMETRY_INTERVAL_S regardless of
Ditto's availability; ditto_io's loops sync telemetry/reported state out and desired
state in, but never block or crash the physics loop if Ditto is down.
"""
import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

from ditto_io import DittoConfig, DittoIO
from physics import PumpPhysics

# Repo-root .env (device-sim/ is one level below repo root).
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sim")

TELEMETRY_INTERVAL_S = float(os.environ.get("TELEMETRY_INTERVAL_S", "1.0"))
FAULT_MODES = {"overheat", "leak", "clear"}

physics = PumpPhysics()
ditto_io = DittoIO(physics, DittoConfig.from_env())


async def _physics_loop() -> None:
    while True:
        await asyncio.sleep(TELEMETRY_INTERVAL_S)
        physics.tick(TELEMETRY_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ditto_io.start()
    physics_task = asyncio.create_task(_physics_loop(), name="physics-tick")
    try:
        yield
    finally:
        physics_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await physics_task
        await ditto_io.stop()


app = FastAPI(title="device-sim", lifespan=lifespan)


@app.post("/fault/{mode}")
async def post_fault(mode: str):
    if mode not in FAULT_MODES:
        raise HTTPException(status_code=404, detail=f"unknown fault mode: {mode}")
    physics.set_fault(None if mode == "clear" else mode)
    return {"fault": physics.fault}


@app.get("/state")
async def get_state():
    return {
        "t": physics.t,
        "fault": physics.fault,
        "reported": physics.reported,
        "desired": physics.desired,
        "telemetry": physics.telemetry,
        "ditto_connected": ditto_io.ditto_connected,
    }
