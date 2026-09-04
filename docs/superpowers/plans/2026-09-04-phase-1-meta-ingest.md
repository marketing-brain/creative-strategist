# Phase 1: Meta Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect a Meta ad account through OAuth, then backfill and nightly-sync its ads, creatives, original image/video files, and daily metrics into Postgres and blob storage, exposed through org-scoped API endpoints.

**Architecture:** An `AdPlatformConnector` Protocol hides Meta. `connectors/meta` holds OAuth, a Graph API client with rate-limit awareness, and a connector implementation. `ingest/` holds the repository functions and job handlers (`meta.backfill`, `meta.sync_incremental`, `meta.download_creative`). A nightly schedule enqueues incremental syncs. Raw API responses are stored to blob storage before parsing.

**Tech Stack:** httpx, respx (tests), cryptography Fernet, Meta Marketing API `v21.0`.

**Spec:** `docs/superpowers/specs/2026-09-04-creative-strategist-design.md` (sections 4, 5)

## Global Constraints

- Meta scopes: `ads_read`, `ads_management`, `business_management`.
- Long-lived tokens stored encrypted with Fernet using `TOKEN_ENCRYPTION_KEY`.
- Backfill window: trailing 180 days. Incremental window: trailing 3 days.
- Insights request: `level=ad`, `time_increment=1`, `breakdowns=publisher_platform,platform_position`.
- Every raw Graph API page is written to `blobs/<org_id>/raw/meta/<account>/<endpoint>/<timestamp>-<n>.json` before parsing.
- Failures are per creative (`meta.download_creative` jobs) and never abort a whole account sync.
- All Phase 0 constraints apply.

---

## File structure

```
db/migrations/20260904000002_ingest.sql   # ad_account, ad, creative, asset, metric_daily (partitioned)
src/cs/core/crypto.py                     # encrypt_str / decrypt_str (Fernet)
src/cs/connectors/__init__.py
src/cs/connectors/base.py                 # AdPlatformConnector Protocol + dataclasses
src/cs/connectors/fakes.py                # FakeConnector for tests
src/cs/connectors/meta/__init__.py
src/cs/connectors/meta/oauth.py           # authorize URL, code exchange, long-lived exchange, list accounts
src/cs/connectors/meta/client.py          # GraphClient: GET with paging, rate-limit backoff, raw page capture
src/cs/connectors/meta/connector.py       # MetaConnector(AdPlatformConnector)
src/cs/connectors/meta/parse.py           # pure functions: creative format, metric row from insights row
src/cs/ingest/__init__.py
src/cs/ingest/repo.py                     # ad_account / ad / creative / asset / metric_daily persistence
src/cs/ingest/partitions.py               # ensure_metric_partition
src/cs/ingest/jobs.py                     # meta.backfill, meta.sync_incremental, meta.download_creative + schedule
src/cs/api/routers/meta_oauth.py          # /meta/oauth/start, /meta/oauth/callback
src/cs/api/routers/accounts.py            # /accounts CRUD-ish + backfill trigger
src/cs/api/routers/creatives.py           # /creatives list/detail/metrics
tests/fixtures/meta/*.json                # recorded Graph API responses (scrubbed)
tests/connectors/test_meta_oauth.py
tests/connectors/test_meta_client.py
tests/connectors/test_meta_parse.py
tests/ingest/test_repo.py
tests/ingest/test_jobs.py
tests/api/test_accounts_and_creatives.py
```

---

### Task 1: Ingest migration and token crypto

**Files:**
- Create: `db/migrations/20260904000002_ingest.sql`, `src/cs/core/crypto.py`, `src/cs/ingest/__init__.py`, `src/cs/ingest/partitions.py`
- Test: `tests/core/test_crypto.py`, `tests/ingest/test_partitions.py`

**Interfaces:**
- Produces tables `ad_account`, `ad`, `creative`, `asset`, `metric_daily` (partitioned by `day`).
- Produces `encrypt_str(settings, plaintext) -> str`, `decrypt_str(settings, token) -> str`.
- Produces `ensure_metric_partition(db, day: date) -> None` (idempotent, creates `metric_daily_YYYYMM`).

- [ ] **Step 1: Write the migration**

`db/migrations/20260904000002_ingest.sql`:

```sql
-- migrate:up
CREATE TABLE ad_account (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  platform TEXT NOT NULL CHECK (platform IN ('meta')),
  platform_account_id TEXT NOT NULL,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  access_token_enc TEXT NOT NULL,
  token_expires_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'disconnected')),
  sync_cursor JSONB NOT NULL DEFAULT '{"schema_version": 1}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, platform, platform_account_id)
);

CREATE TABLE creative (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  platform_creative_id TEXT NOT NULL,
  format TEXT NOT NULL CHECK (format IN ('image', 'video', 'carousel')),
  headline TEXT,
  primary_text TEXT,
  description TEXT,
  cta_type TEXT,
  link_url TEXT,
  primary_asset_id UUID,
  generated_asset_id UUID,
  raw_path TEXT NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  schema_version INT NOT NULL DEFAULT 1,
  UNIQUE (ad_account_id, platform_creative_id)
);
CREATE INDEX creative_org_idx ON creative (org_id, ad_account_id);

CREATE TABLE ad (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  platform_ad_id TEXT NOT NULL,
  creative_id UUID NOT NULL REFERENCES creative(id),
  adset_id TEXT,
  campaign_id TEXT,
  name TEXT,
  status TEXT,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (ad_account_id, platform_ad_id)
);

CREATE TABLE asset (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  creative_id UUID NOT NULL REFERENCES creative(id),
  kind TEXT NOT NULL CHECK (kind IN ('original_image','original_video','keyframe','audio','transcript','thumbnail')),
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  mime TEXT NOT NULL,
  width INT,
  height INT,
  duration_ms INT,
  seq INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX asset_creative_idx ON asset (creative_id, kind, seq);
ALTER TABLE creative ADD CONSTRAINT creative_primary_asset_fk FOREIGN KEY (primary_asset_id) REFERENCES asset(id);

CREATE TABLE metric_daily (
  org_id UUID NOT NULL,
  ad_account_id UUID NOT NULL,
  creative_id UUID NOT NULL,
  ad_id UUID NOT NULL,
  day DATE NOT NULL,
  placement TEXT NOT NULL,
  spend NUMERIC(14,4) NOT NULL DEFAULT 0,
  impressions BIGINT NOT NULL DEFAULT 0,
  clicks BIGINT NOT NULL DEFAULT 0,
  link_clicks BIGINT NOT NULL DEFAULT 0,
  conversions NUMERIC(14,4) NOT NULL DEFAULT 0,
  conversion_value NUMERIC(14,4) NOT NULL DEFAULT 0,
  video_3s_views BIGINT NOT NULL DEFAULT 0,
  video_thruplay BIGINT NOT NULL DEFAULT 0,
  video_p50 BIGINT NOT NULL DEFAULT 0,
  video_p100 BIGINT NOT NULL DEFAULT 0,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (creative_id, ad_id, day, placement)
) PARTITION BY RANGE (day);
CREATE INDEX metric_daily_org_day_idx ON metric_daily (org_id, ad_account_id, day);

-- migrate:down
DROP TABLE metric_daily;
DROP TABLE asset;
DROP TABLE ad;
DROP TABLE creative;
DROP TABLE ad_account;
```

- [ ] **Step 2: Write failing tests**

`tests/core/test_crypto.py`:

```python
from cryptography.fernet import Fernet

from cs.core.config import Settings
from cs.core.crypto import decrypt_str, encrypt_str


def test_roundtrip(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "x")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    s = Settings()
    token = encrypt_str(s, "secret-token")
    assert token != "secret-token"
    assert decrypt_str(s, token) == "secret-token"
```

`tests/ingest/test_partitions.py`:

```python
from datetime import date

from cs.ingest.partitions import ensure_metric_partition


async def test_ensure_partition_is_idempotent(db):
    await ensure_metric_partition(db, date(2026, 9, 15))
    await ensure_metric_partition(db, date(2026, 9, 1))
    names = await db.fetch("SELECT tablename FROM pg_tables WHERE tablename LIKE 'metric_daily_%'")
    assert [r["tablename"] for r in names] == ["metric_daily_202609"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `make migrate-test && uv run pytest tests/core/test_crypto.py tests/ingest/test_partitions.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement**

`src/cs/core/crypto.py`:

```python
from cryptography.fernet import Fernet

from cs.core.config import Settings


def _fernet(settings: Settings) -> Fernet:
    return Fernet(settings.token_encryption_key.encode())


def encrypt_str(settings: Settings, plaintext: str) -> str:
    return _fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt_str(settings: Settings, token: str) -> str:
    return _fernet(settings).decrypt(token.encode()).decode()
```

`src/cs/ingest/__init__.py`: empty.

`src/cs/ingest/partitions.py`:

```python
from datetime import date

from cs.core.db import Database


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    return start, end


async def ensure_metric_partition(db: Database, day: date) -> None:
    start, end = _month_bounds(day)
    name = f"metric_daily_{start:%Y%m}"
    await db.execute(
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF metric_daily "
        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
    )
```

Note: the Fernet key in `tests/conftest.py` from Phase 0 (`dGVzdC1rZXkt...`) is not a valid Fernet key. Replace that line with a generated one: `Fernet.generate_key().decode()` computed once and pasted, e.g. `"k6r0yV2n7mQ9c1wQ3pX8uZbL4tA6dF0hJ2sK5vN8xE0="` (any 32-byte urlsafe-base64 string). Update `.github/workflows/ci.yml` to the same value.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_crypto.py tests/ingest/test_partitions.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add db/migrations/20260904000002_ingest.sql src/cs/core/crypto.py src/cs/ingest tests/core/test_crypto.py tests/ingest/test_partitions.py tests/conftest.py .github
git commit -m "feat(ingest): schema for accounts, ads, creatives, assets, metrics; token crypto"
```

---

### Task 2: Connector interface and fake

**Files:**
- Create: `src/cs/connectors/__init__.py`, `src/cs/connectors/base.py`, `src/cs/connectors/fakes.py`
- Test: `tests/connectors/test_fake_connector.py`

**Interfaces:**
- Produces dataclasses:
  - `PlatformAd(platform_ad_id: str, name: str, status: str, adset_id: str | None, campaign_id: str | None, platform_creative_id: str)`
  - `PlatformCreative(platform_creative_id: str, format: Literal["image","video","carousel"], headline, primary_text, description, cta_type, link_url, asset_url: str | None, asset_mime: str | None, raw: dict)`
  - `MetricRow(platform_ad_id: str, day: date, placement: str, spend: Decimal, impressions: int, clicks: int, link_clicks: int, conversions: Decimal, conversion_value: Decimal, video_3s_views: int, video_thruplay: int, video_p50: int, video_p100: int, raw: dict)`
- Produces `AdPlatformConnector` Protocol:
  - `async list_ads(account_id: str) -> list[PlatformAd]`
  - `async get_creative(creative_id: str) -> PlatformCreative`
  - `async download_asset(url: str) -> tuple[bytes, str]` (bytes, mime)
  - `async fetch_metrics(account_id: str, since: date, until: date) -> list[MetricRow]`
  - `async upload_image(account_id, data: bytes, name: str) -> str` (image hash)  — implemented in Phase 4
  - `async upload_video(account_id, data: bytes, name: str) -> str` (video id) — Phase 4
  - `async create_ad(account_id, adset_id, name, creative_spec: dict, status: str) -> tuple[str, str]` (creative id, ad id) — Phase 4
- Produces `FakeConnector` with in-memory `ads`, `creatives`, `assets`, `metrics`, `uploads`, `created_ads` attributes.

- [ ] **Step 1: Write failing test**

`tests/connectors/test_fake_connector.py`:

```python
from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.connectors.fakes import FakeConnector


async def test_fake_connector_roundtrip():
    c = FakeConnector()
    c.add_creative(PlatformCreative("cr1", "image", "H", "P", None, "SHOP_NOW", "https://x", "https://img/1.png", "image/png", {}), b"png")
    c.add_ad(PlatformAd("ad1", "Ad 1", "ACTIVE", "as1", "c1", "cr1"))
    c.add_metric(MetricRow("ad1", date(2026, 9, 1), "facebook:feed", Decimal("10"), 1000, 20, 15, Decimal("2"), Decimal("80"), 0, 0, 0, 0, {}))
    assert [a.platform_ad_id for a in await c.list_ads("act_1")] == ["ad1"]
    assert (await c.get_creative("cr1")).headline == "H"
    assert await c.download_asset("https://img/1.png") == (b"png", "image/png")
    assert len(await c.fetch_metrics("act_1", date(2026, 8, 1), date(2026, 9, 30))) == 1
    assert await c.fetch_metrics("act_1", date(2026, 9, 2), date(2026, 9, 30)) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/connectors/test_fake_connector.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement base and fake**

`src/cs/connectors/__init__.py`: empty.

`src/cs/connectors/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

Format = Literal["image", "video", "carousel"]


@dataclass
class PlatformAd:
    platform_ad_id: str
    name: str
    status: str
    adset_id: str | None
    campaign_id: str | None
    platform_creative_id: str


@dataclass
class PlatformCreative:
    platform_creative_id: str
    format: Format
    headline: str | None
    primary_text: str | None
    description: str | None
    cta_type: str | None
    link_url: str | None
    asset_url: str | None
    asset_mime: str | None
    raw: dict = field(default_factory=dict)


@dataclass
class MetricRow:
    platform_ad_id: str
    day: date
    placement: str
    spend: Decimal
    impressions: int
    clicks: int
    link_clicks: int
    conversions: Decimal
    conversion_value: Decimal
    video_3s_views: int
    video_thruplay: int
    video_p50: int
    video_p100: int
    raw: dict = field(default_factory=dict)


class AdPlatformConnector(Protocol):
    async def list_ads(self, account_id: str) -> list[PlatformAd]: ...
    async def get_creative(self, creative_id: str) -> PlatformCreative: ...
    async def download_asset(self, url: str) -> tuple[bytes, str]: ...
    async def fetch_metrics(self, account_id: str, since: date, until: date) -> list[MetricRow]: ...
    async def upload_image(self, account_id: str, data: bytes, name: str) -> str: ...
    async def upload_video(self, account_id: str, data: bytes, name: str) -> str: ...
    async def create_ad(
        self, account_id: str, adset_id: str, name: str, creative_spec: dict, status: str
    ) -> tuple[str, str]: ...
```

`src/cs/connectors/fakes.py`:

```python
from __future__ import annotations

from datetime import date

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative


class FakeConnector:
    def __init__(self) -> None:
        self.ads: list[PlatformAd] = []
        self.creatives: dict[str, PlatformCreative] = {}
        self.assets: dict[str, tuple[bytes, str]] = {}
        self.metrics: list[MetricRow] = []
        self.uploads: list[tuple[str, str, int]] = []
        self.created_ads: list[dict] = []
        self.fail_downloads: set[str] = set()

    def add_creative(self, c: PlatformCreative, asset_bytes: bytes | None = None) -> None:
        self.creatives[c.platform_creative_id] = c
        if asset_bytes is not None and c.asset_url:
            self.assets[c.asset_url] = (asset_bytes, c.asset_mime or "application/octet-stream")

    def add_ad(self, a: PlatformAd) -> None:
        self.ads.append(a)

    def add_metric(self, m: MetricRow) -> None:
        self.metrics.append(m)

    async def list_ads(self, account_id: str) -> list[PlatformAd]:
        return list(self.ads)

    async def get_creative(self, creative_id: str) -> PlatformCreative:
        return self.creatives[creative_id]

    async def download_asset(self, url: str) -> tuple[bytes, str]:
        if url in self.fail_downloads:
            raise RuntimeError(f"download failed: {url}")
        return self.assets[url]

    async def fetch_metrics(self, account_id: str, since: date, until: date) -> list[MetricRow]:
        return [m for m in self.metrics if since <= m.day <= until]

    async def upload_image(self, account_id: str, data: bytes, name: str) -> str:
        self.uploads.append(("image", name, len(data)))
        return f"hash_{len(self.uploads)}"

    async def upload_video(self, account_id: str, data: bytes, name: str) -> str:
        self.uploads.append(("video", name, len(data)))
        return f"vid_{len(self.uploads)}"

    async def create_ad(self, account_id, adset_id, name, creative_spec, status) -> tuple[str, str]:
        n = len(self.created_ads) + 1
        self.created_ads.append({"account_id": account_id, "adset_id": adset_id, "name": name,
                                 "creative_spec": creative_spec, "status": status})
        return f"cr_new_{n}", f"ad_new_{n}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/connectors/test_fake_connector.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cs/connectors tests/connectors/test_fake_connector.py
git commit -m "feat(connectors): AdPlatformConnector protocol and in-memory fake"
```

---

### Task 3: Meta OAuth

**Files:**
- Create: `src/cs/connectors/meta/__init__.py`, `src/cs/connectors/meta/oauth.py`, `src/cs/api/routers/meta_oauth.py`, `src/cs/ingest/repo.py` (account functions only for now)
- Modify: `src/cs/api/app.py` (include router)
- Test: `tests/connectors/test_meta_oauth.py`, `tests/api/test_meta_oauth_routes.py`

**Interfaces:**
- Produces `oauth.authorize_url(settings, state: str) -> str`
- Produces `async oauth.exchange_code(settings, code: str, http: httpx.AsyncClient) -> tuple[str, datetime | None]` (long-lived token, expiry)
- Produces `async oauth.list_ad_accounts(settings, token, http) -> list[dict]` with keys `account_id` (digits only), `name`, `currency`
- Produces `ingest.repo.upsert_ad_account(db, org_id, platform_account_id, name, currency, token_enc, expires_at) -> UUID`, `list_ad_accounts(db, org_id) -> list[Record]`, `get_ad_account(db, org_id, account_id) -> Record | None`
- Routes: `GET /meta/oauth/start` → `{"url": ...}` (state = signed org id); `GET /meta/oauth/callback?code&state` → exchanges, lists accounts, stores each as `ad_account` with status `paused`; returns `{"accounts":[...]}`. Accounts are activated by `POST /accounts/{id}/activate` (Task 6).

- [ ] **Step 1: Write failing tests**

`tests/connectors/test_meta_oauth.py`:

```python
import httpx
import respx

from cs.connectors.meta import oauth


def _settings(settings):
    settings.meta_app_id = "app1"
    settings.meta_app_secret = "sec"
    settings.meta_redirect_uri = "http://localhost:8000/meta/oauth/callback"
    return settings


def test_authorize_url(settings):
    url = oauth.authorize_url(_settings(settings), state="abc")
    assert url.startswith("https://www.facebook.com/v21.0/dialog/oauth?")
    assert "client_id=app1" in url and "state=abc" in url
    assert "scope=ads_read%2Cads_management%2Cbusiness_management" in url


@respx.mock
async def test_exchange_code_returns_long_lived_token(settings):
    s = _settings(settings)
    respx.get("https://graph.facebook.com/v21.0/oauth/access_token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "short"}),
            httpx.Response(200, json={"access_token": "long", "expires_in": 5184000}),
        ]
    )
    async with httpx.AsyncClient() as http:
        token, expires = await oauth.exchange_code(s, "code123", http)
    assert token == "long" and expires is not None


@respx.mock
async def test_list_ad_accounts(settings):
    respx.get("https://graph.facebook.com/v21.0/me/adaccounts").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "act_123", "account_id": "123", "name": "Main", "currency": "USD"}]})
    )
    async with httpx.AsyncClient() as http:
        accts = await oauth.list_ad_accounts(_settings(settings), "tok", http)
    assert accts == [{"account_id": "123", "name": "Main", "currency": "USD"}]
```

`tests/api/test_meta_oauth_routes.py`:

```python
import httpx
import respx

from tests.conftest import dev_headers


async def test_start_returns_url(client):
    r = await client.get("/meta/oauth/start", headers=dev_headers())
    assert r.status_code == 200 and "dialog/oauth" in r.json()["url"]


@respx.mock
async def test_callback_stores_paused_accounts(client, db):
    start = await client.get("/meta/oauth/start", headers=dev_headers())
    state = start.json()["state"]
    respx.get("https://graph.facebook.com/v21.0/oauth/access_token").mock(
        side_effect=[httpx.Response(200, json={"access_token": "s"}),
                     httpx.Response(200, json={"access_token": "l", "expires_in": 100})])
    respx.get("https://graph.facebook.com/v21.0/me/adaccounts").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "act_1", "account_id": "1", "name": "A", "currency": "USD"}]}))
    r = await client.get(f"/meta/oauth/callback?code=c&state={state}")
    assert r.status_code == 200
    row = await db.fetchrow("SELECT status, access_token_enc FROM ad_account WHERE platform_account_id='1'")
    assert row["status"] == "paused" and row["access_token_enc"] != "l"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/connectors/test_meta_oauth.py tests/api/test_meta_oauth_routes.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement oauth**

`src/cs/connectors/meta/__init__.py`: empty.

`src/cs/connectors/meta/oauth.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from cs.core.config import Settings

SCOPES = "ads_read,ads_management,business_management"


def _graph(settings: Settings) -> str:
    return f"https://graph.facebook.com/{settings.meta_api_version}"


def authorize_url(settings: Settings, state: str) -> str:
    q = urlencode({
        "client_id": settings.meta_app_id,
        "redirect_uri": settings.meta_redirect_uri,
        "scope": SCOPES,
        "response_type": "code",
        "state": state,
    })
    return f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth?{q}"


async def exchange_code(settings: Settings, code: str, http: httpx.AsyncClient) -> tuple[str, datetime | None]:
    r = await http.get(f"{_graph(settings)}/oauth/access_token", params={
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "redirect_uri": settings.meta_redirect_uri,
        "code": code,
    })
    r.raise_for_status()
    short = r.json()["access_token"]
    r = await http.get(f"{_graph(settings)}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "fb_exchange_token": short,
    })
    r.raise_for_status()
    body = r.json()
    expires = None
    if body.get("expires_in"):
        expires = datetime.now(UTC) + timedelta(seconds=int(body["expires_in"]))
    return body["access_token"], expires


async def list_ad_accounts(settings: Settings, token: str, http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(f"{_graph(settings)}/me/adaccounts", params={
        "fields": "id,account_id,name,currency", "limit": 200, "access_token": token,
    })
    r.raise_for_status()
    return [{"account_id": a["account_id"], "name": a["name"], "currency": a.get("currency", "USD")}
            for a in r.json().get("data", [])]
```

- [ ] **Step 4: Implement account repo functions**

`src/cs/ingest/repo.py` (initial; later tasks append):

```python
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


async def upsert_ad_account(
    db: Database, org_id: UUID, platform_account_id: str, name: str, currency: str,
    token_enc: str, expires_at: datetime | None,
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO ad_account (id, org_id, platform, platform_account_id, name, currency,
                                access_token_enc, token_expires_at, status)
        VALUES ($1, $2, 'meta', $3, $4, $5, $6, $7, 'paused')
        ON CONFLICT (org_id, platform, platform_account_id) DO UPDATE
          SET name = EXCLUDED.name, currency = EXCLUDED.currency,
              access_token_enc = EXCLUDED.access_token_enc, token_expires_at = EXCLUDED.token_expires_at
        RETURNING id
        """,
        new_id(), org_id, platform_account_id, name, currency, token_enc, expires_at,
    )
    return row["id"]


async def list_ad_accounts(db: Database, org_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT id, platform, platform_account_id, name, currency, status, sync_cursor, created_at "
        "FROM ad_account WHERE org_id = $1 ORDER BY created_at", org_id)


async def get_ad_account(db: Database, org_id: UUID, account_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM ad_account WHERE org_id = $1 AND id = $2", org_id, account_id)


async def set_ad_account_status(db: Database, org_id: UUID, account_id: UUID, status: str) -> None:
    await db.execute("UPDATE ad_account SET status = $3 WHERE org_id = $1 AND id = $2", org_id, account_id, status)
```

- [ ] **Step 5: Implement routes**

`src/cs/api/routers/meta_oauth.py`:

```python
import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, require_admin
from cs.api.deps import get_db, get_settings_dep
from cs.connectors.meta import oauth
from cs.core.config import Settings
from cs.core.crypto import encrypt_str
from cs.core.db import Database
from cs.ingest import repo

router = APIRouter(prefix="/meta/oauth")


def _sign_state(settings: Settings, org_id: str) -> str:
    return jwt.encode({"org_id": org_id}, settings.token_encryption_key, algorithm="HS256")


def _read_state(settings: Settings, state: str) -> str:
    try:
        return jwt.decode(state, settings.token_encryption_key, algorithms=["HS256"])["org_id"]
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "bad state") from exc


@router.get("/start")
async def start(auth: AuthContext = Depends(require_admin), settings: Settings = Depends(get_settings_dep)) -> dict:
    state = _sign_state(settings, str(auth.org_id))
    return {"url": oauth.authorize_url(settings, state), "state": state}


@router.get("/callback")
async def callback(code: str, state: str, db: Database = Depends(get_db),
                   settings: Settings = Depends(get_settings_dep)) -> dict:
    from uuid import UUID

    org_id = UUID(_read_state(settings, state))
    async with httpx.AsyncClient(timeout=30) as http:
        token, expires = await oauth.exchange_code(settings, code, http)
        accounts = await oauth.list_ad_accounts(settings, token, http)
    enc = encrypt_str(settings, token)
    stored = []
    for a in accounts:
        aid = await repo.upsert_ad_account(db, org_id, a["account_id"], a["name"], a["currency"], enc, expires)
        stored.append({"id": str(aid), **a})
    return {"accounts": stored}
```

In `src/cs/api/app.py` add `from cs.api.routers import meta_oauth` and `app.include_router(meta_oauth.router)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/connectors/test_meta_oauth.py tests/api/test_meta_oauth_routes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add src/cs/connectors/meta src/cs/api/routers/meta_oauth.py src/cs/api/app.py src/cs/ingest/repo.py tests/connectors/test_meta_oauth.py tests/api/test_meta_oauth_routes.py
git commit -m "feat(meta): OAuth connect flow storing encrypted long-lived tokens"
```

---

### Task 4: Meta Graph client, parsers, and connector

**Files:**
- Create: `src/cs/connectors/meta/client.py`, `src/cs/connectors/meta/parse.py`, `src/cs/connectors/meta/connector.py`, `tests/fixtures/meta/ads_page1.json`, `tests/fixtures/meta/creative_image.json`, `tests/fixtures/meta/creative_video.json`, `tests/fixtures/meta/insights_page1.json`
- Test: `tests/connectors/test_meta_parse.py`, `tests/connectors/test_meta_client.py`

**Interfaces:**
- Produces `GraphClient(settings, token, http: httpx.AsyncClient, raw_sink: Callable[[str, dict], Awaitable[None]] | None = None)` with `async get(path, params) -> dict` (adds token, retries 429/`code 17`/`code 32` with backoff from `x-business-use-case-usage`), `async get_all(path, params) -> list[dict]` (follows `paging.next`, calls `raw_sink(endpoint_name, page)` per page).
- Produces `parse.creative_from_graph(obj: dict) -> PlatformCreative`, `parse.metric_from_insight(row: dict) -> MetricRow`, `parse.CONVERSION_ACTION_TYPES`.
- Produces `MetaConnector(client: GraphClient)` implementing the Phase 1 half of `AdPlatformConnector` (`list_ads`, `get_creative`, `download_asset`, `fetch_metrics`); the Phase 4 methods raise `NotImplementedError` until Phase 4 fills them.

- [ ] **Step 1: Create fixtures**

`tests/fixtures/meta/ads_page1.json`:

```json
{"data": [
  {"id": "1001", "name": "Ad A", "status": "ACTIVE", "adset_id": "2001", "campaign_id": "3001", "creative": {"id": "5001"}},
  {"id": "1002", "name": "Ad B", "status": "PAUSED", "adset_id": "2001", "campaign_id": "3001", "creative": {"id": "5002"}}
], "paging": {"cursors": {"after": "x"}}}
```

`tests/fixtures/meta/creative_image.json`:

```json
{"id": "5001", "name": "Static A", "title": "Sleep better", "body": "Tired of waking up tired?",
 "call_to_action_type": "SHOP_NOW", "link_url": "https://example.com/p",
 "image_url": "https://scontent.example/img5001.jpg", "image_hash": "abc",
 "object_story_spec": {"link_data": {"message": "Tired of waking up tired?", "name": "Sleep better",
   "description": "Free shipping", "link": "https://example.com/p", "call_to_action": {"type": "SHOP_NOW"}}}}
```

`tests/fixtures/meta/creative_video.json`:

```json
{"id": "5002", "name": "Video B", "video_id": "7002", "thumbnail_url": "https://scontent.example/thumb.jpg",
 "object_story_spec": {"video_data": {"message": "Watch this", "title": "Real results",
   "call_to_action": {"type": "LEARN_MORE", "value": {"link": "https://example.com/v"}}, "video_id": "7002"}}}
```

`tests/fixtures/meta/insights_page1.json`:

```json
{"data": [
  {"ad_id": "1001", "date_start": "2026-09-01", "date_stop": "2026-09-01",
   "publisher_platform": "facebook", "platform_position": "feed",
   "spend": "12.50", "impressions": "1000", "clicks": "30", "inline_link_clicks": "25",
   "actions": [{"action_type": "purchase", "value": "2"}, {"action_type": "link_click", "value": "25"}],
   "action_values": [{"action_type": "purchase", "value": "90.00"}],
   "video_play_actions": [{"action_type": "video_view", "value": "400"}],
   "video_thruplay_watched_actions": [{"action_type": "video_view", "value": "120"}],
   "video_p50_watched_actions": [{"action_type": "video_view", "value": "150"}],
   "video_p100_watched_actions": [{"action_type": "video_view", "value": "60"}]}
]}
```

- [ ] **Step 2: Write failing parse tests**

`tests/connectors/test_meta_parse.py`:

```python
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from cs.connectors.meta import parse

FX = Path(__file__).resolve().parents[1] / "fixtures/meta"


def test_creative_image():
    c = parse.creative_from_graph(json.loads((FX / "creative_image.json").read_text()))
    assert c.format == "image" and c.headline == "Sleep better"
    assert c.primary_text == "Tired of waking up tired?" and c.cta_type == "SHOP_NOW"
    assert c.asset_url == "https://scontent.example/img5001.jpg" and c.asset_mime == "image/jpeg"


def test_creative_video_needs_source_lookup():
    c = parse.creative_from_graph(json.loads((FX / "creative_video.json").read_text()))
    assert c.format == "video" and c.asset_url is None and c.raw["video_id"] == "7002"
    assert c.link_url == "https://example.com/v" and c.cta_type == "LEARN_MORE"


def test_metric_from_insight():
    row = json.loads((FX / "insights_page1.json").read_text())["data"][0]
    m = parse.metric_from_insight(row)
    assert m.platform_ad_id == "1001" and m.day == date(2026, 9, 1)
    assert m.placement == "facebook:feed" and m.spend == Decimal("12.50")
    assert m.conversions == Decimal("2") and m.conversion_value == Decimal("90.00")
    assert (m.video_3s_views, m.video_thruplay, m.video_p50, m.video_p100) == (400, 120, 150, 60)
```

- [ ] **Step 3: Write failing client tests**

`tests/connectors/test_meta_client.py`:

```python
import json
from datetime import date
from pathlib import Path

import httpx
import respx

from cs.connectors.meta.client import GraphClient
from cs.connectors.meta.connector import MetaConnector

FX = Path(__file__).resolve().parents[1] / "fixtures/meta"
G = "https://graph.facebook.com/v21.0"


@respx.mock
async def test_get_all_follows_paging_and_sinks_raw(settings):
    page1 = json.loads((FX / "ads_page1.json").read_text())
    page1["paging"]["next"] = f"{G}/act_1/ads?after=x"
    respx.get(f"{G}/act_1/ads").mock(side_effect=[
        httpx.Response(200, json=page1),
        httpx.Response(200, json={"data": [{"id": "1003", "creative": {"id": "5003"}}]}),
    ])
    sunk = []

    async def sink(name, page):
        sunk.append(name)

    async with httpx.AsyncClient() as http:
        gc = GraphClient(settings, "tok", http, raw_sink=sink)
        rows = await gc.get_all("act_1/ads", {"fields": "id"})
    assert [r["id"] for r in rows] == ["1001", "1002", "1003"] and sunk == ["act_1/ads", "act_1/ads"]


@respx.mock
async def test_rate_limit_retries(settings, monkeypatch):
    calls = []
    respx.get(f"{G}/act_1/ads").mock(side_effect=[
        httpx.Response(400, json={"error": {"code": 17, "message": "User request limit reached"}}),
        httpx.Response(200, json={"data": []}),
    ])
    import cs.connectors.meta.client as mod

    async def fake_sleep(s):
        calls.append(s)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient() as http:
        rows = await GraphClient(settings, "tok", http).get_all("act_1/ads", {})
    assert rows == [] and calls and calls[0] >= 30


@respx.mock
async def test_connector_video_creative_resolves_source(settings):
    respx.get(f"{G}/5002").mock(return_value=httpx.Response(200, json=json.loads((FX / "creative_video.json").read_text())))
    respx.get(f"{G}/7002").mock(return_value=httpx.Response(200, json={"source": "https://video.example/7002.mp4"}))
    async with httpx.AsyncClient() as http:
        c = await MetaConnector(GraphClient(settings, "tok", http)).get_creative("5002")
    assert c.asset_url == "https://video.example/7002.mp4" and c.asset_mime == "video/mp4"


@respx.mock
async def test_connector_fetch_metrics(settings):
    respx.get(f"{G}/act_1/insights").mock(return_value=httpx.Response(200, json=json.loads((FX / "insights_page1.json").read_text())))
    async with httpx.AsyncClient() as http:
        rows = await MetaConnector(GraphClient(settings, "tok", http)).fetch_metrics("1", date(2026, 9, 1), date(2026, 9, 1))
    assert len(rows) == 1 and rows[0].placement == "facebook:feed"
```

- [ ] **Step 4: Run to verify they fail**

Run: `uv run pytest tests/connectors/test_meta_parse.py tests/connectors/test_meta_client.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 5: Implement parse**

`src/cs/connectors/meta/parse.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformCreative

CONVERSION_ACTION_TYPES = (
    "omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase",
    "lead", "complete_registration", "onsite_conversion.lead_grouped",
)


def _first_action(items: list[dict] | None, types: tuple[str, ...]) -> Decimal:
    by_type = {i["action_type"]: Decimal(str(i["value"])) for i in (items or [])}
    for t in types:
        if t in by_type:
            return by_type[t]
    return Decimal(0)


def _video_count(items: list[dict] | None) -> int:
    return int(sum(Decimal(str(i["value"])) for i in (items or [])))


def creative_from_graph(obj: dict) -> PlatformCreative:
    spec = obj.get("object_story_spec") or {}
    link = spec.get("link_data") or {}
    video = spec.get("video_data") or {}
    feed = obj.get("asset_feed_spec") or {}

    if obj.get("video_id") or video.get("video_id"):
        fmt = "video"
    elif link.get("child_attachments") or len(feed.get("images", [])) > 1:
        fmt = "carousel"
    else:
        fmt = "image"

    cta = (link.get("call_to_action") or video.get("call_to_action") or {})
    link_url = link.get("link") or (cta.get("value") or {}).get("link") or obj.get("link_url")
    asset_url = None if fmt == "video" else obj.get("image_url")
    return PlatformCreative(
        platform_creative_id=str(obj["id"]),
        format=fmt,
        headline=obj.get("title") or link.get("name") or video.get("title"),
        primary_text=obj.get("body") or link.get("message") or video.get("message"),
        description=link.get("description"),
        cta_type=obj.get("call_to_action_type") or cta.get("type"),
        link_url=link_url,
        asset_url=asset_url,
        asset_mime="image/jpeg" if asset_url else None,
        raw={"video_id": obj.get("video_id") or video.get("video_id"), "thumbnail_url": obj.get("thumbnail_url"), **obj},
    )


def metric_from_insight(row: dict) -> MetricRow:
    return MetricRow(
        platform_ad_id=str(row["ad_id"]),
        day=date.fromisoformat(row["date_start"]),
        placement=f"{row.get('publisher_platform', 'unknown')}:{row.get('platform_position', 'unknown')}",
        spend=Decimal(str(row.get("spend", "0"))),
        impressions=int(row.get("impressions", 0)),
        clicks=int(row.get("clicks", 0)),
        link_clicks=int(row.get("inline_link_clicks", 0)),
        conversions=_first_action(row.get("actions"), CONVERSION_ACTION_TYPES),
        conversion_value=_first_action(row.get("action_values"), CONVERSION_ACTION_TYPES),
        video_3s_views=_video_count(row.get("video_play_actions")),
        video_thruplay=_video_count(row.get("video_thruplay_watched_actions")),
        video_p50=_video_count(row.get("video_p50_watched_actions")),
        video_p100=_video_count(row.get("video_p100_watched_actions")),
        raw=row,
    )
```

- [ ] **Step 6: Implement client**

`src/cs/connectors/meta/client.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import httpx

from cs.core.config import Settings

log = logging.getLogger("cs.meta")
RETRYABLE_CODES = {4, 17, 32, 613}
RawSink = Callable[[str, dict], Awaitable[None]]


class GraphError(RuntimeError):
    pass


class GraphClient:
    def __init__(self, settings: Settings, token: str, http: httpx.AsyncClient, raw_sink: RawSink | None = None):
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
                            return float(e["estimated_time_to_regain_access"]) * 60
            except (ValueError, AttributeError):
                pass
        return float(min(30 * (2 ** attempt), 900))

    async def get(self, path: str, params: dict | None = None, url: str | None = None) -> dict:
        params = {**(params or {}), "access_token": self.token}
        for attempt in range(6):
            resp = await self.http.get(url or f"{self.base}/{path}", params=None if url else params)
            if resp.status_code == 200:
                return resp.json()
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
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
```

Note: `paging.next` already includes the access token as a query parameter, which is why `get(url=...)` sends no params.

- [ ] **Step 7: Implement connector**

`src/cs/connectors/meta/connector.py`:

```python
from __future__ import annotations

import json
from datetime import date

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.connectors.meta import parse
from cs.connectors.meta.client import GraphClient

AD_FIELDS = "id,name,status,adset_id,campaign_id,creative{id}"
CREATIVE_FIELDS = ("id,name,body,title,call_to_action_type,link_url,image_url,image_hash,video_id,"
                   "thumbnail_url,object_story_spec,asset_feed_spec")
INSIGHT_FIELDS = ("ad_id,date_start,date_stop,spend,impressions,clicks,inline_link_clicks,actions,"
                  "action_values,video_play_actions,video_thruplay_watched_actions,"
                  "video_p50_watched_actions,video_p100_watched_actions")


class MetaConnector:
    def __init__(self, client: GraphClient):
        self.client = client

    async def list_ads(self, account_id: str) -> list[PlatformAd]:
        rows = await self.client.get_all(f"act_{account_id}/ads", {"fields": AD_FIELDS, "limit": 200})
        return [PlatformAd(str(r["id"]), r.get("name", ""), r.get("status", ""), r.get("adset_id"),
                           r.get("campaign_id"), str(r["creative"]["id"])) for r in rows if r.get("creative")]

    async def get_creative(self, creative_id: str) -> PlatformCreative:
        obj = await self.client.get(creative_id, {"fields": CREATIVE_FIELDS})
        c = parse.creative_from_graph(obj)
        if c.format == "video" and c.raw.get("video_id"):
            v = await self.client.get(str(c.raw["video_id"]), {"fields": "source,length"})
            c.asset_url, c.asset_mime = v.get("source"), "video/mp4"
        return c

    async def download_asset(self, url: str) -> tuple[bytes, str]:
        resp = await self.client.http.get(url, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "application/octet-stream").split(";")[0]

    async def fetch_metrics(self, account_id: str, since: date, until: date) -> list[MetricRow]:
        rows = await self.client.get_all(f"act_{account_id}/insights", {
            "level": "ad", "time_increment": 1,
            "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
            "breakdowns": "publisher_platform,platform_position",
            "fields": INSIGHT_FIELDS, "limit": 500,
        })
        return [parse.metric_from_insight(r) for r in rows]

    async def upload_image(self, account_id: str, data: bytes, name: str) -> str:
        raise NotImplementedError("Phase 4")

    async def upload_video(self, account_id: str, data: bytes, name: str) -> str:
        raise NotImplementedError("Phase 4")

    async def create_ad(self, account_id, adset_id, name, creative_spec, status) -> tuple[str, str]:
        raise NotImplementedError("Phase 4")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/connectors -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/cs/connectors/meta tests/connectors tests/fixtures/meta
git commit -m "feat(meta): graph client with paging and rate-limit backoff, parsers, connector"
```

---

### Task 5: Ingest repository and jobs

**Files:**
- Modify: `src/cs/ingest/repo.py` (append creative/ad/asset/metric functions)
- Create: `src/cs/ingest/jobs.py`, `src/cs/ingest/service.py`
- Modify: `src/cs/jobs/all_handlers.py` (add `import cs.ingest.jobs`)
- Test: `tests/ingest/test_repo.py`, `tests/ingest/test_jobs.py`

**Interfaces:**
- Produces repo functions (all `(db, org_id, ...)`):
  - `upsert_creative(db, org_id, ad_account_id, c: PlatformCreative, raw_path: str) -> tuple[UUID, bool]` (id, created)
  - `upsert_ad(db, org_id, ad_account_id, a: PlatformAd, creative_id) -> UUID`
  - `insert_asset(db, org_id, creative_id, kind, path, sha256, mime, width=None, height=None, duration_ms=None, seq=0) -> UUID`
  - `set_primary_asset(db, org_id, creative_id, asset_id) -> None`
  - `creative_has_asset(db, org_id, creative_id, kind) -> bool`
  - `upsert_metrics(db, org_id, ad_account_id, rows: list[MetricRow]) -> int` (maps `platform_ad_id` → `ad.id`/`creative_id`; skips unknown ads; ensures partitions)
  - `list_creatives(db, org_id, ad_account_id=None, format=None, limit=50, offset=0) -> list[Record]`
  - `get_creative(db, org_id, creative_id) -> Record | None`
  - `creative_metrics(db, org_id, creative_id, since, until) -> list[Record]` (per-day sums across placements)
- Produces `service.connector_for_account(settings, account_row, http, blobs) -> MetaConnector` (decrypts token, wires raw sink to blobs).
- Produces handlers `meta.backfill` (payload `{"ad_account_id"}`), `meta.sync_incremental` (same), `meta.download_creative` (payload `{"creative_id"}`), and schedule `ingest.nightly_sync` every 86400s enqueuing `meta.sync_incremental` per active account with `dedup_key=str(account id)`.
- Produces `service.sync_account(ctx, account_row, connector, since, until) -> dict` used by both backfill and incremental: lists ads, upserts creatives and ads, enqueues download for creatives lacking an original asset, upserts metrics, updates `sync_cursor.last_synced_until`.
- Produces `tests` fixtures: `account_id` (an `ad_account` row for `org_id`).
- Produces a testing seam: `cs.ingest.service.CONNECTOR_FACTORY` (module-level callable `(settings, account_row, http, blobs) -> connector`) that tests replace with a lambda returning a `FakeConnector`.

- [ ] **Step 1: Write failing repo tests**

`tests/ingest/test_repo.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core.crypto import encrypt_str
from cs.ingest import repo


@pytest.fixture
async def account_id(db, org_id, settings):
    return await repo.upsert_ad_account(db, org_id, "1", "Main", "USD", encrypt_str(settings, "tok"), None)


def _creative(cid="c1", fmt="image"):
    return PlatformCreative(cid, fmt, "H", "P", None, "SHOP_NOW", "https://x", "https://img", "image/png", {})


async def test_upsert_creative_and_ad_and_metrics(db, org_id, account_id):
    cid, created = await repo.upsert_creative(db, org_id, account_id, _creative(), "raw/c1.json")
    assert created
    cid2, created2 = await repo.upsert_creative(db, org_id, account_id, _creative(), "raw/c1.json")
    assert cid2 == cid and not created2

    aid = await repo.upsert_ad(db, org_id, account_id, PlatformAd("ad1", "A", "ACTIVE", "as", "cp", "c1"), cid)
    n = await repo.upsert_metrics(db, org_id, account_id, [
        MetricRow("ad1", date(2026, 9, 1), "facebook:feed", Decimal("10"), 100, 5, 4, Decimal("1"), Decimal("50"), 0, 0, 0, 0, {}),
        MetricRow("ad1", date(2026, 9, 1), "instagram:reels", Decimal("5"), 50, 2, 2, Decimal("0"), Decimal("0"), 0, 0, 0, 0, {}),
        MetricRow("unknown", date(2026, 9, 1), "facebook:feed", Decimal("1"), 1, 0, 0, Decimal("0"), Decimal("0"), 0, 0, 0, 0, {}),
    ])
    assert n == 2
    days = await repo.creative_metrics(db, org_id, cid, date(2026, 8, 1), date(2026, 9, 30))
    assert len(days) == 1 and days[0]["spend"] == Decimal("15") and days[0]["impressions"] == 150
    assert aid is not None


async def test_assets_and_listing(db, org_id, account_id, blobs):
    cid, _ = await repo.upsert_creative(db, org_id, account_id, _creative(), "raw/c1.json")
    assert not await repo.creative_has_asset(db, org_id, cid, "original_image")
    asset_id = await repo.insert_asset(db, org_id, cid, "original_image", "p/x.png", "sha", "image/png", 10, 10)
    await repo.set_primary_asset(db, org_id, cid, asset_id)
    assert await repo.creative_has_asset(db, org_id, cid, "original_image")
    rows = await repo.list_creatives(db, org_id)
    assert len(rows) == 1 and rows[0]["primary_asset_path"] == "p/x.png"
    assert (await repo.get_creative(db, org_id, cid))["headline"] == "H"
```

- [ ] **Step 2: Write failing job tests**

`tests/ingest/test_jobs.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.connectors.fakes import FakeConnector
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.ingest import repo, service
from cs.jobs.worker import run_once


@pytest.fixture
async def account_id(db, org_id, settings):
    aid = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD", encrypt_str(settings, "tok"), None)
    await repo.set_ad_account_status(db, org_id, aid, "active")
    return aid


@pytest.fixture
def fake(monkeypatch):
    f = FakeConnector()
    f.add_creative(PlatformCreative("c1", "image", "H", "P", None, "SHOP_NOW", "https://x", "https://img/1", "image/png", {}), b"PNGDATA")
    f.add_creative(PlatformCreative("c2", "video", "V", "P2", None, "LEARN_MORE", "https://y", "https://vid/2", "video/mp4", {"video_id": "7"}), b"MP4DATA")
    f.add_ad(PlatformAd("ad1", "A", "ACTIVE", "as1", "cp1", "c1"))
    f.add_ad(PlatformAd("ad2", "B", "ACTIVE", "as1", "cp1", "c2"))
    f.add_metric(MetricRow("ad1", date.today(), "facebook:feed", Decimal("3"), 30, 1, 1, Decimal("0"), Decimal("0"), 0, 0, 0, 0, {}))
    monkeypatch.setattr(service, "CONNECTOR_FACTORY", lambda settings, acct, http, blobs: f)
    return f


async def _drain(db, blobs, settings):
    while await run_once(db, blobs, settings, "w"):
        pass


async def test_backfill_ingests_and_downloads(db, blobs, settings, org_id, account_id, fake):
    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)
    creatives = await repo.list_creatives(db, org_id)
    assert {c["format"] for c in creatives} == {"image", "video"}
    assert all(c["primary_asset_path"] for c in creatives)
    assert await db.fetchval("SELECT count(*) FROM metric_daily") == 1
    assert await db.fetchval("SELECT count(*) FROM job WHERE status <> 'done'") == 0
    cursor = await db.fetchval("SELECT sync_cursor FROM ad_account WHERE id=$1", account_id)
    assert cursor["last_synced_until"] == date.today().isoformat()


async def test_download_failure_is_isolated(db, blobs, settings, org_id, account_id, fake):
    fake.fail_downloads.add("https://vid/2")
    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)
    ok = await db.fetchval("SELECT count(*) FROM creative WHERE primary_asset_id IS NOT NULL")
    assert ok == 1
    assert await db.fetchval("SELECT count(*) FROM job WHERE job_type='meta.download_creative' AND status='pending'") == 1


async def test_nightly_schedule_enqueues_incremental(db, settings, org_id, account_id):
    from cs.ingest.jobs import nightly_sync

    await nightly_sync(db, settings)
    row = await db.fetchrow("SELECT job_type, dedup_key FROM job")
    assert row["job_type"] == "meta.sync_incremental" and row["dedup_key"] == str(account_id)
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/ingest -v`
Expected: FAIL (missing functions / modules)

- [ ] **Step 4: Append repo functions**

Append to `src/cs/ingest/repo.py`:

```python
from datetime import date  # noqa: E402

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative  # noqa: E402
from cs.ingest.partitions import ensure_metric_partition  # noqa: E402


async def upsert_creative(db: Database, org_id: UUID, ad_account_id: UUID, c: PlatformCreative, raw_path: str) -> tuple[UUID, bool]:
    row = await db.fetchrow(
        """
        INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id, format, headline, primary_text,
                              description, cta_type, link_url, raw_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (ad_account_id, platform_creative_id) DO UPDATE SET last_seen_at = now()
        RETURNING id, (xmax = 0) AS created
        """,
        new_id(), org_id, ad_account_id, c.platform_creative_id, c.format, c.headline, c.primary_text,
        c.description, c.cta_type, c.link_url, raw_path,
    )
    return row["id"], row["created"]


async def upsert_ad(db: Database, org_id: UUID, ad_account_id: UUID, a: PlatformAd, creative_id: UUID) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO ad (id, org_id, ad_account_id, platform_ad_id, creative_id, adset_id, campaign_id, name, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (ad_account_id, platform_ad_id) DO UPDATE
          SET status = EXCLUDED.status, name = EXCLUDED.name, creative_id = EXCLUDED.creative_id, last_seen_at = now()
        RETURNING id
        """,
        new_id(), org_id, ad_account_id, a.platform_ad_id, creative_id, a.adset_id, a.campaign_id, a.name, a.status,
    )
    return row["id"]


async def insert_asset(db: Database, org_id: UUID, creative_id: UUID, kind: str, path: str, sha256: str, mime: str,
                       width: int | None = None, height: int | None = None, duration_ms: int | None = None, seq: int = 0) -> UUID:
    aid = new_id()
    await db.execute(
        "INSERT INTO asset (id, org_id, creative_id, kind, path, sha256, mime, width, height, duration_ms, seq) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        aid, org_id, creative_id, kind, path, sha256, mime, width, height, duration_ms, seq,
    )
    return aid


async def set_primary_asset(db: Database, org_id: UUID, creative_id: UUID, asset_id: UUID) -> None:
    await db.execute("UPDATE creative SET primary_asset_id = $3 WHERE org_id = $1 AND id = $2", org_id, creative_id, asset_id)


async def creative_has_asset(db: Database, org_id: UUID, creative_id: UUID, kind: str) -> bool:
    return bool(await db.fetchval(
        "SELECT 1 FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind=$3 LIMIT 1", org_id, creative_id, kind))


async def upsert_metrics(db: Database, org_id: UUID, ad_account_id: UUID, rows: list[MetricRow]) -> int:
    if not rows:
        return 0
    ads = await db.fetch("SELECT id, platform_ad_id, creative_id FROM ad WHERE org_id=$1 AND ad_account_id=$2", org_id, ad_account_id)
    by_pid = {a["platform_ad_id"]: a for a in ads}
    for day in {r.day.replace(day=1) for r in rows}:
        await ensure_metric_partition(db, day)
    n = 0
    async with db.transaction() as tx:
        for r in rows:
            a = by_pid.get(r.platform_ad_id)
            if not a:
                continue
            await tx.execute(
                """
                INSERT INTO metric_daily (org_id, ad_account_id, creative_id, ad_id, day, placement, spend, impressions,
                    clicks, link_clicks, conversions, conversion_value, video_3s_views, video_thruplay, video_p50, video_p100, raw)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                ON CONFLICT (creative_id, ad_id, day, placement) DO UPDATE SET
                    spend=EXCLUDED.spend, impressions=EXCLUDED.impressions, clicks=EXCLUDED.clicks,
                    link_clicks=EXCLUDED.link_clicks, conversions=EXCLUDED.conversions,
                    conversion_value=EXCLUDED.conversion_value, video_3s_views=EXCLUDED.video_3s_views,
                    video_thruplay=EXCLUDED.video_thruplay, video_p50=EXCLUDED.video_p50, video_p100=EXCLUDED.video_p100,
                    raw=EXCLUDED.raw
                """,
                org_id, ad_account_id, a["creative_id"], a["id"], r.day, r.placement, r.spend, r.impressions,
                r.clicks, r.link_clicks, r.conversions, r.conversion_value, r.video_3s_views, r.video_thruplay,
                r.video_p50, r.video_p100, r.raw,
            )
            n += 1
    return n


async def list_creatives(db: Database, org_id: UUID, ad_account_id: UUID | None = None, format: str | None = None,
                         limit: int = 50, offset: int = 0) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT c.id, c.ad_account_id, c.platform_creative_id, c.format, c.headline, c.primary_text, c.cta_type,
               c.link_url, c.first_seen_at, c.last_seen_at, a.path AS primary_asset_path, a.mime AS primary_asset_mime
        FROM creative c LEFT JOIN asset a ON a.id = c.primary_asset_id
        WHERE c.org_id = $1 AND ($2::uuid IS NULL OR c.ad_account_id = $2) AND ($3::text IS NULL OR c.format = $3)
        ORDER BY c.first_seen_at DESC LIMIT $4 OFFSET $5
        """,
        org_id, ad_account_id, format, limit, offset,
    )


async def get_creative(db: Database, org_id: UUID, creative_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow(
        """
        SELECT c.*, a.path AS primary_asset_path, a.mime AS primary_asset_mime
        FROM creative c LEFT JOIN asset a ON a.id = c.primary_asset_id
        WHERE c.org_id = $1 AND c.id = $2
        """, org_id, creative_id)


async def creative_metrics(db: Database, org_id: UUID, creative_id: UUID, since: date, until: date) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT day, sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
               sum(link_clicks) AS link_clicks, sum(conversions) AS conversions, sum(conversion_value) AS conversion_value,
               sum(video_3s_views) AS video_3s_views, sum(video_thruplay) AS video_thruplay
        FROM metric_daily WHERE org_id=$1 AND creative_id=$2 AND day BETWEEN $3 AND $4
        GROUP BY day ORDER BY day
        """, org_id, creative_id, since, until)
```

- [ ] **Step 5: Implement service and jobs**

`src/cs/ingest/service.py`:

```python
from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import asyncpg
import httpx

from cs.connectors.base import AdPlatformConnector
from cs.connectors.meta.client import GraphClient
from cs.connectors.meta.connector import MetaConnector
from cs.core import queue
from cs.core.blobs import BlobStore, sha256_hex
from cs.core.config import Settings
from cs.core.crypto import decrypt_str
from cs.ingest import repo
from cs.jobs.registry import JobContext

BACKFILL_DAYS = 180
INCREMENTAL_DAYS = 3


def _raw_sink(blobs: BlobStore, org_id: UUID, account: str):
    async def sink(endpoint: str, page: dict) -> None:
        key = f"{org_id}/raw/meta/{account}/{endpoint.replace('/', '_')}/{int(time.time() * 1000)}.json"
        await blobs.put(key, json.dumps(page).encode(), "application/json")
    return sink


def default_connector_factory(settings: Settings, account: asyncpg.Record, http: httpx.AsyncClient, blobs: BlobStore) -> AdPlatformConnector:
    token = decrypt_str(settings, account["access_token_enc"])
    sink = _raw_sink(blobs, account["org_id"], account["platform_account_id"])
    return MetaConnector(GraphClient(settings, token, http, raw_sink=sink))


CONNECTOR_FACTORY = default_connector_factory


async def sync_account(ctx: JobContext, account: asyncpg.Record, connector: AdPlatformConnector, since: date, until: date) -> dict:
    org_id, acct_id = account["org_id"], account["id"]
    ads = await connector.list_ads(account["platform_account_id"])
    new_creatives = 0
    for ad in ads:
        c = await connector.get_creative(ad.platform_creative_id)
        raw_key = f"{org_id}/raw/meta/{account['platform_account_id']}/creative/{c.platform_creative_id}.json"
        await ctx.blobs.put(raw_key, json.dumps(c.raw, default=str).encode(), "application/json")
        creative_id, created = await repo.upsert_creative(ctx.db, org_id, acct_id, c, raw_key)
        new_creatives += int(created)
        await repo.upsert_ad(ctx.db, org_id, acct_id, ad, creative_id)
        kind = "original_video" if c.format == "video" else "original_image"
        if c.asset_url and not await repo.creative_has_asset(ctx.db, org_id, creative_id, kind):
            await queue.enqueue(ctx.db, org_id, "meta.download_creative",
                                {"creative_id": str(creative_id), "asset_url": c.asset_url, "kind": kind},
                                dedup_key=str(creative_id))
    rows = await connector.fetch_metrics(account["platform_account_id"], since, until)
    n_metrics = await repo.upsert_metrics(ctx.db, org_id, acct_id, rows)
    cursor = dict(account["sync_cursor"])
    cursor.update({"schema_version": 1, "last_synced_until": until.isoformat(),
                   "last_synced_at": datetime.now(UTC).isoformat()})
    await ctx.db.execute("UPDATE ad_account SET sync_cursor=$2 WHERE id=$1", acct_id, cursor)
    return {"ads": len(ads), "new_creatives": new_creatives, "metrics": n_metrics}


async def download_creative(ctx: JobContext, connector: AdPlatformConnector, creative_id: UUID, asset_url: str, kind: str) -> None:
    org_id = ctx.job.org_id
    data, mime = await connector.download_asset(asset_url)
    ext = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}.get(mime, "bin")
    key = f"{org_id}/creatives/{creative_id}/original.{ext}"
    await ctx.blobs.put(key, data, mime)
    asset_id = await repo.insert_asset(ctx.db, org_id, creative_id, kind, key, sha256_hex(data), mime)
    await repo.set_primary_asset(ctx.db, org_id, creative_id, asset_id)
    await queue.enqueue(ctx.db, org_id, "tagging.tag_creative", {"creative_id": str(creative_id)},
                        dedup_key=f"tag:{creative_id}")


def window(days: int, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    return today - timedelta(days=days), today
```

`src/cs/ingest/jobs.py`:

```python
from __future__ import annotations

from uuid import UUID

import httpx

from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.ingest import repo, service
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule


async def _account(ctx: JobContext):
    acct = await repo.get_ad_account(ctx.db, ctx.job.org_id, UUID(ctx.job.payload["ad_account_id"]))
    if acct is None:
        raise RuntimeError("ad_account not found")
    return acct


@handler("meta.backfill")
async def backfill(ctx: JobContext) -> None:
    acct = await _account(ctx)
    since, until = service.window(service.BACKFILL_DAYS)
    async with httpx.AsyncClient(timeout=60) as http:
        connector = service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
        await service.sync_account(ctx, acct, connector, since, until)


@handler("meta.sync_incremental")
async def sync_incremental(ctx: JobContext) -> None:
    acct = await _account(ctx)
    since, until = service.window(service.INCREMENTAL_DAYS)
    async with httpx.AsyncClient(timeout=60) as http:
        connector = service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
        await service.sync_account(ctx, acct, connector, since, until)


@handler("meta.download_creative")
async def download_creative(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    acct = await ctx.db.fetchrow(
        "SELECT a.* FROM ad_account a JOIN creative c ON c.ad_account_id = a.id WHERE c.id = $1 AND c.org_id = $2",
        creative_id, ctx.job.org_id)
    async with httpx.AsyncClient(timeout=120) as http:
        connector = service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
        await service.download_creative(ctx, connector, creative_id, p["asset_url"], p["kind"])


@register_schedule("ingest.nightly_sync", every_seconds=86400)
async def nightly_sync(db: Database, settings: Settings) -> None:
    accounts = await db.fetch("SELECT id, org_id FROM ad_account WHERE status = 'active'")
    for a in accounts:
        await queue.enqueue(db, a["org_id"], "meta.sync_incremental", {"ad_account_id": str(a["id"])},
                            dedup_key=str(a["id"]))
```

Add to `src/cs/jobs/all_handlers.py`:

```python
import cs.ingest.jobs  # noqa: F401
```

For tests, `tests/conftest.py` must import handlers too. Add at its top: `import cs.jobs.all_handlers  # noqa: F401`.

Note: `download_creative` enqueues `tagging.tag_creative`, which has no handler until Phase 2. The worker marks such jobs failed → they retry with backoff and then die. In `tests/ingest/test_jobs.py::test_backfill_ingests_and_downloads` the assertion `count(*) FROM job WHERE status <> 'done'` is therefore `2` (one per creative) until Phase 2 registers the handler. Write the assertion as `assert await db.fetchval("SELECT count(*) FROM job WHERE status <> 'done' AND job_type <> 'tagging.tag_creative'") == 0` so it is true in both phases.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/ingest -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/cs/ingest src/cs/jobs/all_handlers.py tests/ingest tests/conftest.py
git commit -m "feat(ingest): backfill and incremental sync jobs, per-creative downloads, nightly schedule"
```

---

### Task 6: Accounts and creatives API

**Files:**
- Create: `src/cs/api/routers/accounts.py`, `src/cs/api/routers/creatives.py`
- Modify: `src/cs/api/app.py`
- Test: `tests/api/test_accounts_and_creatives.py`

**Interfaces:**
- Routes:
  - `GET /accounts` → `[{id, platform, platform_account_id, name, currency, status, sync_cursor}]`
  - `POST /accounts/{id}/activate` (admin) → sets `active`, enqueues `meta.backfill` with `dedup_key=f"backfill:{id}"`; returns `{"job_id"}`
  - `POST /accounts/{id}/sync` (admin) → enqueues `meta.sync_incremental`
  - `GET /creatives?account_id&format&limit&offset` → list with `primary_asset_url` (via `blobs.url_for`)
  - `GET /creatives/{id}` → detail
  - `GET /creatives/{id}/metrics?since&until` → daily rows with derived `ctr`, `cpa`, `roas`

- [ ] **Step 1: Write failing tests**

`tests/api/test_accounts_and_creatives.py`:

```python
from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core.crypto import encrypt_str
from cs.ingest import repo
from tests.conftest import dev_headers

H = dev_headers(org="org_api")


async def _seed(db, settings, client):
    me = (await client.get("/me", headers=H)).json()
    from uuid import UUID
    org_id = UUID(me["org_id"])
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD", encrypt_str(settings, "t"), None)
    cid, _ = await repo.upsert_creative(db, org_id, acct, PlatformCreative("c1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}), "raw")
    aid = await repo.insert_asset(db, org_id, cid, "original_image", "o/c1/original.png", "sha", "image/png")
    await repo.set_primary_asset(db, org_id, cid, aid)
    ad = await repo.upsert_ad(db, org_id, acct, PlatformAd("ad1", "A", "ACTIVE", None, None, "c1"), cid)
    await repo.upsert_metrics(db, org_id, acct, [MetricRow("ad1", date(2026, 9, 1), "facebook:feed", Decimal("10"), 1000, 20, 20, Decimal("2"), Decimal("100"), 0, 0, 0, 0, {})])
    return org_id, acct, cid, ad


async def test_accounts_activate_enqueues_backfill(client, db, settings):
    _, acct, _, _ = await _seed(db, settings, client)
    r = await client.get("/accounts", headers=H)
    assert r.json()[0]["status"] == "paused"
    r = await client.post(f"/accounts/{acct}/activate", headers=H)
    assert r.status_code == 200 and r.json()["job_id"]
    assert await db.fetchval("SELECT job_type FROM job") == "meta.backfill"
    assert (await client.get("/accounts", headers=H)).json()[0]["status"] == "active"


async def test_member_cannot_activate(client, db, settings):
    _, acct, _, _ = await _seed(db, settings, client)
    r = await client.post(f"/accounts/{acct}/activate", headers=dev_headers(org="org_api", user="u2", role="member"))
    assert r.status_code == 403


async def test_creatives_list_detail_metrics(client, db, settings):
    _, _, cid, _ = await _seed(db, settings, client)
    r = await client.get("/creatives", headers=H)
    assert r.status_code == 200 and r.json()[0]["primary_asset_url"].endswith("/blobs/o/c1/original.png")
    r = await client.get(f"/creatives/{cid}", headers=H)
    assert r.json()["headline"] == "H"
    r = await client.get(f"/creatives/{cid}/metrics?since=2026-08-01&until=2026-09-30", headers=H)
    row = r.json()[0]
    assert row["ctr"] == 0.02 and row["cpa"] == 5.0 and row["roas"] == 10.0


async def test_other_org_cannot_see_creatives(client, db, settings):
    _, _, cid, _ = await _seed(db, settings, client)
    r = await client.get(f"/creatives/{cid}", headers=dev_headers(org="org_other"))
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/api/test_accounts_and_creatives.py -v`
Expected: FAIL with 404s / import errors

- [ ] **Step 3: Implement routers**

`src/cs/api/routers/accounts.py`:

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_db
from cs.core import queue
from cs.core.db import Database
from cs.ingest import repo

router = APIRouter(prefix="/accounts")


@router.get("")
async def list_accounts(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    rows = await repo.list_ad_accounts(db, auth.org_id)
    return [{**dict(r), "id": str(r["id"])} for r in rows]


@router.post("/{account_id}/activate")
async def activate(account_id: UUID, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)) -> dict:
    if not await repo.get_ad_account(db, auth.org_id, account_id):
        raise HTTPException(404)
    await repo.set_ad_account_status(db, auth.org_id, account_id, "active")
    jid = await queue.enqueue(db, auth.org_id, "meta.backfill", {"ad_account_id": str(account_id)},
                              dedup_key=f"backfill:{account_id}")
    return {"job_id": str(jid) if jid else None}


@router.post("/{account_id}/sync")
async def sync(account_id: UUID, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)) -> dict:
    if not await repo.get_ad_account(db, auth.org_id, account_id):
        raise HTTPException(404)
    jid = await queue.enqueue(db, auth.org_id, "meta.sync_incremental", {"ad_account_id": str(account_id)},
                              dedup_key=str(account_id))
    return {"job_id": str(jid) if jid else None}
```

`src/cs/api/routers/creatives.py`:

```python
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.ingest import repo

router = APIRouter(prefix="/creatives")


def _derived(r) -> dict:
    spend, imp, clicks, conv, val = float(r["spend"]), int(r["impressions"]), int(r["clicks"]), float(r["conversions"]), float(r["conversion_value"])
    return {
        **{k: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v) for k, v in dict(r).items()},
        "day": r["day"].isoformat(),
        "ctr": round(clicks / imp, 4) if imp else 0.0,
        "cpa": round(spend / conv, 2) if conv else None,
        "roas": round(val / spend, 2) if spend else None,
    }


@router.get("")
async def list_creatives(account_id: UUID | None = None, format: str | None = None, limit: int = 50, offset: int = 0,
                         auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                         blobs: BlobStore = Depends(get_blobs)) -> list[dict]:
    rows = await repo.list_creatives(db, auth.org_id, account_id, format, min(limit, 200), offset)
    out = []
    for r in rows:
        d = {**dict(r), "id": str(r["id"]), "ad_account_id": str(r["ad_account_id"])}
        d["primary_asset_url"] = await blobs.url_for(r["primary_asset_path"]) if r["primary_asset_path"] else None
        out.append(d)
    return out


@router.get("/{creative_id}")
async def get_creative(creative_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                       blobs: BlobStore = Depends(get_blobs)) -> dict:
    r = await repo.get_creative(db, auth.org_id, creative_id)
    if not r:
        raise HTTPException(404)
    d = {k: (str(v) if isinstance(v, UUID) else v) for k, v in dict(r).items()}
    d["primary_asset_url"] = await blobs.url_for(r["primary_asset_path"]) if r["primary_asset_path"] else None
    return d


@router.get("/{creative_id}/metrics")
async def creative_metrics(creative_id: UUID, since: date, until: date, auth: AuthContext = Depends(current_auth),
                           db: Database = Depends(get_db)) -> list[dict]:
    if not await repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    return [_derived(r) for r in await repo.creative_metrics(db, auth.org_id, creative_id, since, until)]
```

In `src/cs/api/app.py` include `accounts.router` and `creatives.router`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/api -v`
Expected: PASS

- [ ] **Step 5: Record real fixtures (manual, one-time)**

Create `scripts/record_meta_fixtures.py` that, given `META_ACCESS_TOKEN` and `META_ACCOUNT_ID` env vars, calls `MetaConnector.list_ads`, `get_creative` for the first image and first video creative, and `fetch_metrics` for the last 2 days, then writes the raw JSON (with `access_token` query params stripped and ids left intact) into `tests/fixtures/meta/recorded_*.json`. Run it once against the real account and commit the files. These recorded files are used by the parse tests as additional parametrized cases:

```python
# append to tests/connectors/test_meta_parse.py
import pytest

@pytest.mark.parametrize("name", [p.name for p in FX.glob("recorded_creative_*.json")])
def test_recorded_creatives_parse(name):
    c = parse.creative_from_graph(json.loads((FX / name).read_text()))
    assert c.format in ("image", "video", "carousel") and c.platform_creative_id
```

- [ ] **Step 6: Run the full suite and commit**

Run: `make lint && make test`
Expected: all PASS

```bash
git add src/cs/api tests/api scripts tests/fixtures/meta
git commit -m "feat(api): accounts activation/sync and creatives list, detail, metrics"
```

---

## Self-review

- **Spec coverage:** Section 5 connect flow → Task 3; backfill/incremental/rate limits/raw capture → Tasks 4, 5; connector interface → Task 2; `creative`, `asset`, `metric_daily` partitioning → Task 1; per-creative failure isolation → Task 5 test. The spec did not list an `ad` table; one is added here because metrics arrive per ad and the ad→creative mapping must be stored. Update spec section 4 to mention `ad` when this phase lands.
- **Placeholder scan:** Phase 4 methods on `MetaConnector` raise `NotImplementedError` by design and are filled in Phase 4 Task 6.
- **Type consistency:** `repo.list_creatives` returns `primary_asset_path`, used by both `service` and `routers/creatives`. `service.CONNECTOR_FACTORY` signature `(settings, account_row, http, blobs)` is what tests monkeypatch. Job payload keys: `ad_account_id`, `creative_id`, `asset_url`, `kind`.
