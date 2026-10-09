import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Health:
    ok: bool  # HTTP 200
    model_loaded: bool | None = None  # None when the body does not say


class ModelProbe(Protocol):
    async def health(self, url: str) -> Health: ...

    async def info(self, url: str) -> dict | None: ...

    async def schema(self, url: str) -> dict | None: ...


class HttpProbe:
    """Talks to a model container's /health, /info and /schema."""

    def __init__(self, timeout: float = 5.0):
        self._timeout = timeout

    async def _get(self, url: str, path: str) -> httpx.Response | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.get(f"{url.rstrip('/')}{path}")
        except httpx.HTTPError:
            return None

    async def health(self, url: str) -> Health:
        response = await self._get(url, "/health")
        if response is None or response.status_code != 200:
            return Health(ok=False)
        try:
            body = response.json()
            loaded = body.get("model_loaded") if isinstance(body, dict) else None
        except ValueError:
            loaded = None
        return Health(ok=True, model_loaded=loaded if isinstance(loaded, bool) else None)

    async def info(self, url: str) -> dict | None:
        return await self._json(url, "/info")

    async def schema(self, url: str) -> dict | None:
        return await self._json(url, "/schema")

    async def _json(self, url: str, path: str) -> dict | None:
        response = await self._get(url, path)
        if response is None or response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None
