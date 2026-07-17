"""Device simulator entrypoint (CONTRACTS.md §2, v2: four coupled components).

Run via: uvicorn sim:app --app-dir device-sim --port 9001

The physics loop ticks `SystemPhysics` on its own asyncio task at TELEMETRY_INTERVAL_S
regardless of Ditto's availability; ditto_io's loops sync telemetry/reported state out
(all four things) and desired state in, but never block or crash the physics loop if
Ditto is down.
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
from physics import SystemPhysics

# Repo-root .env (device-sim/ is one level below repo root).
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sim")

TELEMETRY_INTERVAL_S = float(os.environ.get("TELEMETRY_INTERVAL_S", "1.0"))

physics = SystemPhysics()
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


@app.post("/fault/clear")
async def post_fault_clear():
    physics.clear_faults()
    return {"faults": physics.faults}


@app.post("/fault/{component}/{mode}")
async def post_fault(component: str, mode: str):
    try:
        physics.set_fault(component, mode)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"faults": physics.faults}


@app.get("/state")
async def get_state():
    return {
        "t": physics.t,
        "faults": physics.faults,
        "components": {
            component: {
                "telemetry": physics.telemetry(component),
                "reported": physics.reported(component),
                "desired": physics.desired(component),
            }
            for component in SystemPhysics.COMPONENTS
        },
        "ditto_connected": ditto_io.ditto_connected,
    }
