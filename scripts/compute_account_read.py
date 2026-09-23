"""Enqueue insights.compute_account_read for one ad account.

Usage: uv run python scripts/compute_account_read.py --ad-account <uuid>
"""

import argparse
import asyncio
from uuid import UUID

from cs.core import queue
from cs.core.config import get_settings
from cs.core.db import Database


async def main(ad_account_id: UUID) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        row = await db.fetchrow("SELECT org_id FROM ad_account WHERE id=$1", ad_account_id)
        if row is None:
            raise SystemExit(f"no ad_account {ad_account_id}")
        jid = await queue.enqueue(
            db,
            row["org_id"],
            "insights.compute_account_read",
            {"ad_account_id": str(ad_account_id)},
            dedup_key=f"account_read:{ad_account_id}",
            # The writer's call may be billed even when it fails, so the queue never
            # re-runs this on its own (cs.core.model_calls).
            max_attempts=1,
        )
        print("queued" if jid else "already queued", jid or "")
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ad-account", required=True, type=UUID)
    asyncio.run(main(p.parse_args().ad_account))
