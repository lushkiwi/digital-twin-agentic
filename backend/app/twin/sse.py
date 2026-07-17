"""Reusable Ditto SSE consumer (CONTRACTS §1).

An async generator that yields parsed JSON **partials** of the thing.  Handles:
  * exponential backoff 1s -> 30s cap on connection failure,
  * a 60s no-data watchdog that forces a reconnect (Ditto sends keep-alives, so
    total silence means a dead connection),
  * ``on_connect`` / ``on_disconnect`` callbacks so the caller can re-GET the full
    thing after every (re)connection and flip its connectivity status.

Empty ``data:`` lines are keep-alives and are ignored (but they DO reset the
watchdog — they prove the connection is alive).
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator, Awaitable, Callable, Optional

import httpx


async def ditto_sse(
    *,
    base_url: str,
    auth: tuple[str, str],
    thing_id: str,
    on_connect: Optional[Callable[[], Awaitable[None]]] = None,
    on_disconnect: Optional[Callable[[], Awaitable[None]]] = None,
    watchdog_s: float = 60.0,
    backoff_start: float = 1.0,
    backoff_cap: float = 30.0,
) -> AsyncGenerator[dict, None]:
    url = "/api/2/things"
    params = {"ids": thing_id, "fields": "thingId,features"}
    headers = {"Accept": "text/event-stream"}
    backoff = backoff_start

    while True:
        connected = False
        try:
            async with httpx.AsyncClient(
                base_url=base_url, auth=auth, timeout=None
            ) as client:
                async with client.stream(
                    "GET", url, params=params, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    connected = True
                    backoff = backoff_start  # reset on a healthy connection
                    if on_connect is not None:
                        await on_connect()

                    line_iter = resp.aiter_lines()
                    while True:
                        try:
                            line = await asyncio.wait_for(
                                line_iter.__anext__(), timeout=watchdog_s
                            )
                        except asyncio.TimeoutError:
                            # Watchdog: no bytes (not even a keep-alive) -> dead.
                            break
                        except StopAsyncIteration:
                            break  # stream closed by server

                        if not line or not line.startswith("data:"):
                            # Keep-alives / SSE comments / blank separators.
                            continue
                        payload = line[len("data:") :].strip()
                        if not payload:
                            continue  # empty data line = keep-alive
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        yield event
        except asyncio.CancelledError:
            raise
        except Exception:
            # Any connect/stream error -> fall through to backoff + retry.
            pass
        finally:
            if connected and on_disconnect is not None:
                await on_disconnect()

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, backoff_cap)
