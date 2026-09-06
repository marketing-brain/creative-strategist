"""Insert a paused Meta ad_account for the dev org so the e2e flow can activate it with the fake
connector."""

import asyncio
import os

from cs.core.config import get_settings
from cs.core.crypto import encrypt_str
from cs.core.db import Database
from cs.ingest.repo import upsert_ad_account
from cs.orgs.repo import get_or_create_org


async def main() -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    org = await get_or_create_org(db, os.environ.get("NEXT_PUBLIC_DEV_ORG", "org_dev"), "Dev org")
    await upsert_ad_account(db, org, "1", "Demo account", "USD", encrypt_str(s, "fake"), None)
    await db.close()
    print("seeded")


if __name__ == "__main__":
    asyncio.run(main())
