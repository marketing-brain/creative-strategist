"""Dry-run the creative insight against real data: build the brief, call the real model,
run the grounding check, print everything. Writes nothing (no insight row, no duration).

Usage:
  uv run python scripts/preview_creative_insight.py --creative-id <uuid>
  uv run python scripts/preview_creative_insight.py --org-id <uuid> --format video
      (picks the highest-spend readable creative of that format in the last 30 days)
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cs.core import repo as ingest_repo
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.queue import Job
from cs.insights.creative_insight import (
    GeminiGatewayInsightWriter,
    grounding_problems,
    is_readable,
    render_brief,
    user_text,
)
from cs.insights.diagnose_creative import WINDOW_DAYS, diagnose_creative
from cs.jobs.registry import JobContext
from cs.tagging.runner import load_media
from cs.tagging.video import probe_duration_ms


async def _pick(db: Database, org_id: UUID, fmt: str, until) -> list[UUID]:
    rows = await db.fetch(
        """
        SELECT c.id FROM creative c JOIN metric_daily m ON m.creative_id = c.id
        WHERE c.org_id=$1 AND c.format=$2 AND c.primary_asset_id IS NOT NULL
          AND m.day BETWEEN $3 AND $4
        GROUP BY c.id ORDER BY sum(m.spend) DESC LIMIT 20
        """,
        org_id,
        fmt,
        until - timedelta(days=WINDOW_DAYS - 1),
        until,
    )
    return [r["id"] for r in rows]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creative-id", type=UUID)
    ap.add_argument("--org-id", type=UUID)
    ap.add_argument("--format", choices=("image", "video", "carousel"), default="image")
    args = ap.parse_args()
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    blobs = blob_store_from_settings(settings)
    until = datetime.now(UTC).date()
    try:
        if args.creative_id:
            org_id = await db.fetchval("SELECT org_id FROM creative WHERE id=$1", args.creative_id)
            candidates = [args.creative_id]
        else:
            org_id = args.org_id
            candidates = await _pick(db, org_id, args.format, until)
        for cid in candidates:
            creative = await ingest_repo.get_creative(db, org_id, cid)
            diagnosed = await diagnose_creative(db, org_id, creative, until)
            if not is_readable(diagnosed):
                print(f"skip {cid}: not readable")
                continue
            ctx = JobContext(
                db=db,
                blobs=blobs,
                settings=settings,
                job=Job(UUID(int=0), org_id, "preview", {}, 1, 1),
            )
            images, video = await load_media(ctx, creative)
            duration_ms = creative["primary_asset_duration_ms"]
            if video is not None and duration_ms is None:
                duration_ms = await asyncio.to_thread(probe_duration_ms, video[0])
                diagnosed = await diagnose_creative(db, org_id, creative, until, duration_ms)
            brief = render_brief(creative, diagnosed, duration_ms)
            print(f"creative {cid}\n\n----- brief -----\n{brief}\n")
            writer = GeminiGatewayInsightWriter(settings)
            result = await writer.write(images, video, user_text(brief))
            print("----- moments -----")
            print(json.dumps(result.moments, indent=2, ensure_ascii=False))
            print(f"\n----- paragraph ({len(result.paragraph.split())} words) -----")
            print(result.paragraph)
            print("\n----- grounding problems -----")
            print(grounding_problems(result.paragraph, brief, creative["format"], duration_ms))
            print(f"\nmodel={result.model} in={result.input_tokens} out={result.output_tokens}")
            return
        print("no readable creative found")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
