"""List pending vocabulary merges, approve or reject them by id, or revert an applied one.

Usage: uv run python scripts/review_vocab_merges.py --org <uuid>
       uv run python scripts/review_vocab_merges.py --org <uuid> --approve <id> [<id> ...]
       uv run python scripts/review_vocab_merges.py --org <uuid> --reject <id> [<id> ...]
       uv run python scripts/review_vocab_merges.py --org <uuid> --revert <dimension> <slug>
"""

import argparse
import asyncio
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.tagging import vocab_merge


async def main(
    org_id: UUID, approve: list[UUID], reject: list[UUID], revert: list[list[str]]
) -> None:
    db = await Database.connect(get_settings().database_url)
    try:
        for mid in approve:
            await vocab_merge.decide_review(db, mid, approve=True)
            print(f"approved {mid}", flush=True)
        for mid in reject:
            await vocab_merge.decide_review(db, mid, approve=False)
            print(f"rejected {mid}", flush=True)
        for dimension, slug in revert:
            await vocab_merge.revert(db, org_id, dimension, slug)
            print(f"reverted {dimension}/{slug}", flush=True)
        pending = await vocab_merge.list_review(db, org_id)
        print(f"\n{len(pending)} pending review", flush=True)
        for r in pending:
            print(
                f"  {r['id']}  {r['dimension']:<20} {r['slug']} -> {r['into_slug']}  "
                f"({r['similarity']:.2f}) {r['reason'] or '-'}",
                flush=True,
            )
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True, type=UUID)
    p.add_argument("--approve", type=UUID, nargs="*", default=[])
    p.add_argument("--reject", type=UUID, nargs="*", default=[])
    p.add_argument(
        "--revert",
        nargs=2,
        action="append",
        metavar=("DIMENSION", "SLUG"),
        default=[],
        help="undo an applied merge: point SLUG back at itself and restore its tag values",
    )
    a = p.parse_args()
    asyncio.run(main(a.org, a.approve, a.reject, a.revert))
