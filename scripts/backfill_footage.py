"""Rebuilds the footage tables by hand (cs.footage.refresh, which also runs nightly).

Usage:
    uv run python scripts/backfill_footage.py                  # dry run, every org
    uv run python scripts/backfill_footage.py --org <uuid>     # dry run, one org
    uv run python scripts/backfill_footage.py --apply          # write it

DRY RUN IS THE DEFAULT: the work runs inside a transaction that is rolled back unless
--apply is passed, so a dry run reports the same counts the real run would write.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.footage.refresh import refresh


class _Rollback(Exception):
    """Raised at the end of a dry run to undo the transaction."""


async def run(org: UUID | None, apply: bool) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        async with db.transaction() as tx:
            for table, count in (await refresh(tx, org)).items():
                print(f"  {table}: {count}")
            if not apply:
                raise _Rollback
    except _Rollback:
        print("\nDRY RUN -- rolled back. Re-run with --apply to keep it.")
    finally:
        await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, default=None, help="one org; default every org")
    p.add_argument("--apply", action="store_true", help="keep the changes")
    args = p.parse_args()
    asyncio.run(run(args.org, args.apply))


if __name__ == "__main__":
    main()
