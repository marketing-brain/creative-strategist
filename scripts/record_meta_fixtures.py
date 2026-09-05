"""One-time script to record real Meta Graph API responses as test fixtures.

Usage:
    META_ACCESS_TOKEN=... META_ACCOUNT_ID=... uv run python scripts/record_meta_fixtures.py

Fetches ads, the first image and first video creative, and the last two days
of insights for the given ad account, strips access tokens from any URLs in
the responses, and writes them into tests/fixtures/meta/.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from cs.connectors.meta.client import GraphClient
from cs.connectors.meta.connector import MetaConnector
from cs.core.config import Settings

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests/fixtures/meta"


def _strip_access_token(value):
    if isinstance(value, dict):
        return {k: _strip_access_token(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_access_token(v) for v in value]
    if isinstance(value, str) and "access_token=" in value:
        parts = urlsplit(value)
        query = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "access_token"
        ]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    return value


def _merge_pages(pages: dict[str, list[dict]], endpoint_suffix: str) -> dict:
    matched = [
        page for endpoint, ps in pages.items() if endpoint.endswith(endpoint_suffix) for page in ps
    ]
    data: list[dict] = []
    paging: dict = {}
    for page in matched:
        data.extend(page.get("data", []))
        paging = page.get("paging") or paging
    merged = {"data": data}
    if paging:
        merged["paging"] = paging
    return merged


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(_strip_access_token(payload), indent=2) + "\n")


async def _run(token: str, account_id: str) -> None:
    settings = Settings()
    pages: dict[str, list[dict]] = {}

    async def raw_sink(endpoint: str, page: dict) -> None:
        pages.setdefault(endpoint, []).append(page)

    async with httpx.AsyncClient(timeout=60) as http:
        client = GraphClient(settings, token, http, raw_sink=raw_sink)
        connector = MetaConnector(client)

        ads = await connector.list_ads(account_id)

        by_format: dict[str, object] = {}
        for ad in ads:
            if {"image", "video"} <= by_format.keys():
                break
            creative = await connector.get_creative(ad.platform_creative_id)
            by_format.setdefault(creative.format, creative)

        until = date.today()
        since = until - timedelta(days=1)
        await connector.fetch_metrics(account_id, since, until)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    for fmt in ("image", "video"):
        creative = by_format.get(fmt)
        if creative is None:
            print(f"warning: no {fmt} creative found among fetched ads", file=sys.stderr)
            continue
        out = FIXTURES_DIR / f"recorded_creative_{creative.platform_creative_id}.json"
        _write_json(out, creative.raw)

    _write_json(FIXTURES_DIR / "recorded_ads.json", _merge_pages(pages, "/ads"))
    _write_json(FIXTURES_DIR / "recorded_insights.json", _merge_pages(pages, "/insights"))


def main() -> None:
    token = os.environ.get("META_ACCESS_TOKEN")
    account_id = os.environ.get("META_ACCOUNT_ID")
    if not token or not account_id:
        print(
            "META_ACCESS_TOKEN and META_ACCOUNT_ID environment variables are required",
            file=sys.stderr,
        )
        raise SystemExit(1)
    asyncio.run(_run(token, account_id))


if __name__ == "__main__":
    main()
