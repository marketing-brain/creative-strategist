from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable

import httpx

from cs.core.config import Settings

log = logging.getLogger("cs.meta")
RETRYABLE_CODES = {4, 17, 32, 613}
MAX_BACKOFF_SECONDS = 900
RawSink = Callable[[str, dict], Awaitable[None]]


class GraphError(RuntimeError):
    pass


class GraphClient:
    def __init__(
        self,
        settings: Settings,
        token: str,
        http: httpx.AsyncClient,
        raw_sink: RawSink | None = None,
    ):
        self.base = f"https://graph.facebook.com/{settings.meta_api_version}"
        self.token = token
        self.http = http
        self.raw_sink = raw_sink

    def _backoff_seconds(self, resp: httpx.Response, attempt: int) -> float:
        usage = resp.headers.get("x-business-use-case-usage")
        if usage:
            try:
                for entries in json.loads(usage).values():
                    for e in entries:
                        if e.get("estimated_time_to_regain_access"):
                            minutes = float(e["estimated_time_to_regain_access"])
                            # Meta can report hours of cooldown; cap the wait and jitter it so
                            # concurrent syncs do not all resume in the same instant.
                            return min(minutes * 60, MAX_BACKOFF_SECONDS) + random.uniform(0, 5)
            except (ValueError, AttributeError):
                pass
        return float(min(30 * (2**attempt), MAX_BACKOFF_SECONDS))

    async def get(self, path: str, params: dict | None = None, url: str | None = None) -> dict:
        # The token travels in the Authorization header, never in the URL: request URLs end up
        # in logs, error messages and Meta's own `paging.next` echoes.
        headers = {"Authorization": f"Bearer {self.token}"}
        for attempt in range(6):
            resp = await self.http.get(
                url or f"{self.base}/{path}", params=None if url else params, headers=headers
            )
            if resp.status_code == 200:
                return resp.json()
            body = (
                resp.json()
                if resp.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            code = (body.get("error") or {}).get("code")
            if resp.status_code == 429 or code in RETRYABLE_CODES:
                delay = self._backoff_seconds(resp, attempt)
                log.warning("meta rate limited", extra={"path": path, "delay": delay})
                await asyncio.sleep(delay)
                continue
            raise GraphError(f"{resp.status_code} {path}: {body.get('error') or resp.text[:200]}")
        raise GraphError(f"rate limit retries exhausted for {path}")

    async def get_all(self, path: str, params: dict | None = None) -> list[dict]:
        out: list[dict] = []
        page = await self.get(path, params)
        while True:
            if self.raw_sink:
                await self.raw_sink(path, page)
            out.extend(page.get("data", []))
            nxt = (page.get("paging") or {}).get("next")
            if not nxt:
                return out
            page = await self.get(path, url=nxt)
