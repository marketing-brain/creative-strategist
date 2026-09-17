"""Run the ask box's reference questions and paraphrases against one ad account's latest stored
run with the live map and writer, and print how each was read and answered. This is the
real-data gate for the typed query kinds; review the output by hand.

Usage: uv run python scripts/ask_probe.py --ad-account <uuid> [--only <substring>]
"""

import argparse
import asyncio
import json
from uuid import UUID

from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights.account_read import ask, repo

REFERENCE = [
    "Where is our money going versus where the evidence points?",
    "Which proven ingredients have we never combined?",
    "What separates our best CPA video from the ads most like it that failed?",
    "Is the expert effect real, or just where we spent the money?",
    "Which single ingredient is doing the work in our best recipe?",
]
PARAPHRASES = [
    "Are we overspending on anything the data says doesn't work?",
    "What's cheap and working that we should put more budget behind?",
    "What should the next video look like, based on what's proven?",
    "Give me a combination of winning elements we haven't shot yet",
    "Why do the ads that look like our top converter underperform it?",
    "Compare the pharmacist ad to its closest lookalikes",
    "Does expert on camera still win once you control for how much we spent on it?",
    "Is the long-video advantage just a spend artefact?",
    "What makes our biggest-spend ad work?",
    "Which part of the wrong-supplement reveal concept is carrying it?",
]


async def main(ad_account_id: UUID, only: str | None) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        row = await db.fetchrow("SELECT org_id FROM ad_account WHERE id=$1", ad_account_id)
        if row is None:
            raise SystemExit(f"no ad_account {ad_account_id}")
        run = await repo.latest_run(db, row["org_id"], ad_account_id)
        if run is None or run["status"] not in ("done", "rejected"):
            raise SystemExit("no account read run to ask against")
        result = run["result"]
        if isinstance(result, str):
            result = json.loads(result)
    finally:
        await db.close()
    writer = ask.ASK_WRITER_FACTORY(settings)
    questions = [q for q in REFERENCE + PARAPHRASES if not only or only.lower() in q.lower()]
    unmappable = 0
    for q in questions:
        out = await ask.answer(q, result, writer, draws=300)
        print("=" * 100)
        print("Q:", q)
        if "unmappable" in out:
            unmappable += 1
            print("UNMAPPABLE:", out["unmappable"])
            continue
        print("kind:", out["kind"], "| verdict:", out["verdict"])
        print("read as:", out["read_as"])
        for f in out["facts"]:
            print("  ", f["id"], "|", f["text"])
        print("cites:", ", ".join(out["pattern_ids"]) or "none")
        print("A:", out["answer"])
        print("settle:", out["what_would_settle_it"])
    print("=" * 100)
    print(f"{len(questions)} questions, {unmappable} unmappable")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ad-account", required=True, type=UUID)
    p.add_argument("--only", default=None, help="run only questions containing this text")
    args = p.parse_args()
    asyncio.run(main(args.ad_account, args.only))
