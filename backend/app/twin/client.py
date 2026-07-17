"""Ditto HTTP client (reads + desired-property writes) for the 4-component system.

``put_desired`` never raises on HTTP or transport errors — it returns the status
and body so the tool executor (the safety boundary) stays in control of the flow.
It also returns the exact request description dict the UI renders.

``get_all_things`` gathers one GET per component; a 404 / transport error for any
single component yields ``None`` for that component and never raises, so the read
paths degrade gracefully when Ditto is partially or fully down (CONTRACTS §1).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

import httpx

from ..config import settings


class DittoClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url or settings.ditto_base_url
        self._auth = (user or settings.ditto_user, password or settings.ditto_pass)
        self._client = httpx.AsyncClient(
            base_url=self.base_url, auth=self._auth, timeout=timeout
        )

    @staticmethod
    def thing_path(thing_id: str) -> str:
        return f"/api/2/things/{thing_id}"

    @staticmethod
    def desired_path(thing_id: str, feature: str, prop: str) -> str:
        return f"/api/2/things/{thing_id}/features/{feature}/desiredProperties/{prop}"

    async def get_thing(self, thing_id: str) -> dict:
        """GET one whole twin.  Raises on non-2xx / transport error (callers retry)."""
        resp = await self._client.get(self.thing_path(thing_id))
        resp.raise_for_status()
        return resp.json()

    async def get_all_things(self) -> Dict[str, Optional[dict]]:
        """GET all four things concurrently, keyed by component id.

        A 404 or any error for a single component yields ``None`` for it (never raises),
        so callers can render nulls gracefully when Ditto is down or a thing is missing.
        """
        items = list(settings.thing_ids.items())  # [(component, thing_id), ...]

        async def _one(tid: str) -> Optional[dict]:
            try:
                return await self.get_thing(tid)
            except Exception:  # noqa: BLE001 — a missing/failed component just reads as None
                return None

        results = await asyncio.gather(*[_one(tid) for _, tid in items])
        return {comp: res for (comp, _), res in zip(items, results)}

    async def put_desired(
        self, thing_id: str, feature: str, prop: str, value: Any
    ) -> Tuple[int, Optional[Any], dict]:
        """PUT a single desired property (bare JSON value).

        Returns ``(status_code, response_json_or_none, request_description)``.
        Never raises: 4xx/5xx come back as their status; a transport failure comes
        back as status ``0`` with an ``{"error": ...}`` body.
        """
        path = self.desired_path(thing_id, feature, prop)
        request_desc = {"method": "PUT", "path": path, "body": value}
        try:
            resp = await self._client.put(path, json=value)
        except httpx.RequestError as exc:
            return 0, {"error": f"Ditto unreachable: {exc}"}, request_desc
        body: Optional[Any] = None
        if resp.content:
            try:
                body = resp.json()
            except ValueError:
                body = None
        return resp.status_code, body, request_desc

    async def aclose(self) -> None:
        await self._client.aclose()


# Module-level shared client used by the tool executor and read paths.
# Tests monkeypatch ``app.agents.tools.ditto_client`` with a stub.
ditto_client = DittoClient()
