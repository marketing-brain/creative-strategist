"""Re-enqueue tagging for creatives Phase 1 left behind: their
tagging.tag_creative job dead-lettered before this phase's handler existed,
or (for carousels) they had no asset to tag at all until Task 2 added
carousel card downloads.

Usage: uv run python scripts/reenqueue_tagging.py
"""

import asyncio

from cs.core.config import get_settings
from cs.core.db import Database
from cs.tagging.reconcile import reenqueue_untagged


async def main() -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        n = await reenqueue_untagged(db)
        print(f"re-enqueued {n} tagging job(s)")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
