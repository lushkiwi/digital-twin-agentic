"""Ditto HTTP client (reads + desired-property writes).

``put_desired`` never raises on HTTP or transport errors — it returns the status
and body so the tool executor (the safety boundary) stays in control of the flow.
It also returns the exact request description dict the UI renders.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

import httpx

from ..config import settings


class DittoClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        thing_id: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url or settings.ditto_base_url
        self.thing_id = thing_id or settings.thing_id
        self._auth = (user or settings.ditto_user, password or settings.ditto_pass)
        self._client = httpx.AsyncClient(
            base_url=self.base_url, auth=self._auth, timeout=timeout
        )

    @property
    def thing_path(self) -> str:
        return f"/api/2/things/{self.thing_id}"

    def desired_path(self, prop: str) -> str:
        return f"{self.thing_path}/features/pump/desiredProperties/{prop}"

    async def get_thing(self) -> dict:
        """GET the whole twin.  Raises on non-2xx / transport error (callers retry)."""
        resp = await self._client.get(self.thing_path)
        resp.raise_for_status()
        return resp.json()

    async def put_desired(
        self, prop: str, value: Any
    ) -> Tuple[int, Optional[Any], dict]:
        """PUT a single desired property (bare JSON value).

        Returns ``(status_code, response_json_or_none, request_description)``.
        Never raises: 4xx/5xx come back as their status; a transport failure comes
        back as status ``0`` with an ``{"error": ...}`` body.
        """
        path = self.desired_path(prop)
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
