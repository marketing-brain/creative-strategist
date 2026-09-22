"""Enqueue tagging.tag_creative for specific creatives.

The gate in cs.tagging.gates decides whether each one is worth a paid call: a file with too
little delivery to compare, or whose tag budget is already spent, is skipped and the reason
printed. --force pays anyway, which is how a file is deliberately tagged more than once to
measure how far the tagger agrees with itself.

Usage: uv run python scripts/enqueue_tagging.py [--force] <creative_id> [<creative_id> ...]
"""

import argparse
import asyncio
from uuid import UUID

from cs.core import queue
from cs.core import repo as core_repo
from cs.core.config import get_settings
from cs.core.db import Database
from cs.tagging.gates import TAG_JOB, explain, tag_blocker


async def main(ids: list[UUID], force: bool) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        for cid in ids:
            row = await db.fetchrow("SELECT org_id FROM creative WHERE id=$1", cid)
            if row is None:
                print(f"{cid}: skipped (missing)")
                continue
            org_id = row["org_id"]
            creative = await core_repo.get_creative(db, org_id, cid)
            assert creative is not None
            if not force:
                blocker, facts = await tag_blocker(db, settings, org_id, creative)
                if blocker is not None:
                    print(f"{cid}: skipped ({blocker}) -- {explain(blocker, facts)}")
                    continue
            jid = await queue.enqueue(
                db,
                org_id,
                TAG_JOB,
                {"creative_id": str(cid), "force": force},
                dedup_key=f"tag:{cid}",
            )
            print(f"{cid}: {'queued ' + str(jid) if jid else 'already queued'}")
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("creative_ids", nargs="+", type=UUID)
    p.add_argument("--force", action="store_true", help="pay even if the gate refuses")
    a = p.parse_args()
    asyncio.run(main(a.creative_ids, a.force))
