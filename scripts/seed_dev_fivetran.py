"""Seed a fake Fivetran-landed schema plus a meta_connection for the dev org, so the e2e
flow can activate an account and exercise the real transform without a live Fivetran
connector or Meta credentials."""

import asyncio
import os

from cs.connectors.fivetran.connect import schema_for
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.ingest.fivetran_fixtures import (
    create_fixture_schema,
    insert_campaign_ad_creative,
    insert_daily_insight,
)
from cs.ingest.fivetran_transform import discover_and_sync_entities
from cs.ingest.repo import upsert_meta_connection
from cs.jobs.registry import JobContext
from cs.orgs.repo import get_or_create_org


async def main() -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    org = await get_or_create_org(db, os.environ.get("NEXT_PUBLIC_DEV_ORG", "org_dev"), "Dev org")
    schema = schema_for(org)

    await create_fixture_schema(db, schema)
    for name, headline in (("demo_a", "Headline demo_a"), ("demo_b", "Headline demo_b")):
        await insert_campaign_ad_creative(
            db,
            schema,
            campaign_id=f"c_{name}",
            account_id="1",
            adset_id=f"as_{name}",
            ad_id=f"ad_{name}",
            creative_id=name,
            name=name,
            headline=headline,
            image_url=f"https://placehold.co/600x600.png?text={name}",
        )
        await insert_daily_insight(
            db,
            schema,
            ad_id=f"ad_{name}",
            day="2026-09-01",
            spend=10,
            impressions=1000,
            clicks=20,
            link_clicks=20,
        )

    conn_id = await upsert_meta_connection(db, org, "dev_fake_connector", schema)
    await db.execute("UPDATE meta_connection SET sync_status='ready' WHERE id=$1", conn_id)
    connection = await db.fetchrow("SELECT * FROM meta_connection WHERE id=$1", conn_id)
    ctx = JobContext(db=db, blobs=blob_store_from_settings(s), settings=s, job=None)
    await discover_and_sync_entities(ctx, org, connection)

    await db.close()
    print("seeded")


if __name__ == "__main__":
    asyncio.run(main())
