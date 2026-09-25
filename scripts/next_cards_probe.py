"""Print the `/next` cards for one ad account. The real-data gate for the card generator.

Read the output by hand and answer two questions: is this the file you would have picked, and
is the change the one the account read actually supports? Every claim on a card traces to a
P-id `/insights` already prints, so the second question is checkable against that page.

With --with-model it also runs the narration calls and prints what they cost. Without it,
nothing is billed.

Usage: uv run python scripts/next_cards_probe.py --ad-account <uuid> [--limit 3]
                                                 [--with-model] [--record] [--blocked]
"""

import argparse
import asyncio
import json
import textwrap
from datetime import UTC, datetime
from uuid import UUID

from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights.account_read import repo as read_repo
from cs.insights.account_read.types import unit_from_json
from cs.insights.next_plan import card as card_mod
from cs.insights.next_plan import clip, finding, ledger, ledger_repo, narrate, segment_repo

WIDTH = 96


def wrap(text: str, indent: str = "      ") -> str:
    return textwrap.fill(text, WIDTH, initial_indent=indent, subsequent_indent=indent)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ad-account", required=True)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--with-model", action="store_true", help="narrate the cards (costs money)")
    ap.add_argument("--record", action="store_true", help="write the cards to the ledger")
    ap.add_argument("--blocked", action="store_true", help="show findings the money gate refused")
    args = ap.parse_args()
    aa = UUID(args.ad_account)

    settings = get_settings()
    db = await Database.connect(settings.database_url)
    org_id = await db.fetchval("SELECT org_id FROM ad_account WHERE id=$1", aa)
    run = await read_repo.latest_run(db, org_id, aa)
    if run is None or run["status"] != "done":
        print("no finished account read for this account")
        await db.close()
        return
    result = await read_repo.run_result(db, run["id"])
    read = run.get("read") or {}
    if not isinstance(read, dict):
        read = json.loads(read)

    candidates, blocked = finding.candidates(result, read)
    assets = await segment_repo.load_assets(db, org_id, aa)
    units = {u.sha256: u for u in (unit_from_json(x) for x in (result.get("units") or []))}
    cards, empty = card_mod.build(candidates, assets, units, limit=args.limit)

    print(
        f"\n{len(result.get('units') or [])} files in the read | "
        f"{len(read.get('findings') or [])} findings | {len(candidates)} actionable | "
        f"{len(blocked)} blocked on money | {len(cards)} cards"
    )
    if args.blocked:
        for b in blocked:
            print(wrap(f"BLOCKED {b}", "  "))
    if empty:
        print(f"\n(no cards) {empty}")
        await db.close()
        return

    for i, c in enumerate(cards, start=1):
        print("\n" + "=" * WIDTH)
        print(f"{i}. [{c.verb}] {card_mod.headline(c)}")
        print(f"   {c.title[:88]}")
        for line in (
            card_mod.position_line(c),
            card_mod.evidence_line(c),
            c.money_note,
            c.trade,
            c.evidence.mechanism,
            card_mod.moment_line(c),
            card_mod.job_line(c),
        ):
            if line:
                print(wrap(line))
        for r in c.refs:
            val = card_mod._fmt(c.evidence.metric, r.value) if r.value is not None else "—"
            print(
                f"      copy: {r.title[:52]!r}  {c.evidence.metric_name}={val}  ${r.spend_30d:,.0f}"
            )
        if args.with_model:
            await _narrate(db, org_id, settings, aa, c)

    if args.record:
        await ledger_repo.record(db, org_id, aa, cards)
        print(f"\nrecorded {len(cards)} card(s)")
    await _read_back(db, org_id, aa, result, assets)
    if args.with_model:
        await _cost(db)
    await db.close()


async def _narrate(db, org_id, settings, aa, card) -> None:
    blobs = blob_store_from_settings(settings)
    n = narrate.Narrator(settings)
    clips: dict[str, dict] = {}
    if card.moment is not None:
        paths = await segment_repo.load_asset_paths(db, org_id, aa)
        span = float(card.moment.splice_at)
        targets = [("this file", card.footage_id)]
        targets += [(f"reference {i}", r.footage_id) for i, r in enumerate(card.refs[:2], 1)]
        for who, fid in targets[: narrate.MAX_CLIPS_PER_RUN]:
            if (path := paths.get(fid)) is None:
                continue
            try:
                seg = await clip.cut(await blobs.get(path), 0.0, span)
                note = await n.describe(db, org_id, fid, seg, 0.0, span)
            except Exception as exc:  # noqa: BLE001
                print(f"      [skip ] {who}: {type(exc).__name__}: {str(exc)[:80]}")
                continue
            clips[who] = note
            tag = "cache" if note.get("cached") else "paid "
            print(f"      [{tag}] {who}: {note['on_screen'][:70]}")
    out = await n.narrate(db, org_id, card, clips)
    if isinstance(out, narrate.Refusal):
        print(wrap(f">>> template stands; refused at {out.stage}: {out.detail[:300]}"))
        return
    print()
    for line in (out.title, out.bleed, out.keep, out.cause, out.copy_this, out.job):
        if line:
            print(wrap(line, "    > "))
    print(f"      [critic: {out.verdict}{' (cached)' if out.cached else ''}]")


async def _read_back(db, org_id, aa, result, assets) -> None:
    rows = await ledger_repo.open_cards(db, org_id, aa)
    if not rows:
        return
    units = {u.sha256: u for u in (unit_from_json(x) for x in (result.get("units") or []))}
    spend = {a.sha256: a.spend_30d for a in assets}
    first_day = {a.sha256: a.first_day for a in assets}
    sha_of = {a.footage_id: a.sha256 for a in assets}
    today = datetime.now(UTC).date()
    print("\n" + "=" * WIDTH)
    print(f"LEDGER — {len(rows)} card(s) on record")
    for row in rows:
        base = ledger_repo.baseline_from(row)
        issued = row["first_seen_at"].date()
        sha = sha_of.get(row["spec"].get("footage_id") or "", "")
        rb = ledger.read_back(
            base,
            ledger_repo.now_from(units.get(sha), spend.get(sha, 0.0), base),
            ledger_repo.adoption_from(units, first_day, base, issued, exclude=sha),
            issued,
            today,
        )
        print(f"\n  {row['spec'].get('title', row['key'])[:74]}")
        print(wrap(f"[{rb.outcome}] {rb.detail}", "    "))
        if rb.adoption:
            print(wrap(rb.adoption, "    "))


async def _cost(db) -> None:
    rows = await db.fetch(
        "SELECT purpose, count(*) n, sum(input_tokens) i, sum(output_tokens) o,"
        " count(*) FILTER (WHERE status='failed') f"
        " FROM model_call WHERE purpose LIKE 'next_plan%' GROUP BY 1 ORDER BY 1"
    )
    print("\n" + "=" * WIDTH)
    print("model_call ledger (all runs):")
    for r in rows:
        print(
            f"   {r['purpose']:<22} calls={r['n']:<4} in={r['i']:>8,}"
            f" out={r['o']:>7,} failed={r['f']}"
        )


asyncio.run(main())
