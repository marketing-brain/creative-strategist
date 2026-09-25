"""Writing cards to `next_item` and reading them back.

The baseline is written once, on insert, and deliberately not refreshed: `COALESCE` keeps the
first one. Re-measuring a card against a baseline that moves with it would always report no
change, which is the failure this table exists to avoid.
"""

from __future__ import annotations

from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id
from cs.insights.account_read.types import Unit
from cs.insights.next_plan.ledger import Adoption, Baseline, Now

_UPSERT = """
INSERT INTO next_item (id, org_id, ad_account_id, key, kind, spec, baseline)
VALUES ($1, $2, $3, $4, 'recut', $5, $6)
ON CONFLICT (ad_account_id, key) DO UPDATE
SET last_seen_at = now(),
    spec = EXCLUDED.spec,
    baseline = COALESCE(next_item.baseline, EXCLUDED.baseline)
"""


def baseline_of(card) -> dict:
    e = card.evidence
    return {
        "pid": e.pid,
        "dimension": e.dimension,
        "value": e.value,
        "add": bool(e.add),
        "label": e.label,
        "metric_name": e.metric_name,
        "spend_30d": round(card.spend_30d, 2),
    }


async def record(db: Database, org_id: UUID, ad_account_id: UUID, cards: list) -> None:
    for c in cards:
        spec = {
            "footage_id": c.footage_id,
            "verb": c.verb,
            "title": c.title,
            "pid": c.evidence.pid,
        }
        await db.execute(_UPSERT, new_id(), org_id, ad_account_id, c.key, spec, baseline_of(c))


async def open_cards(db: Database, org_id: UUID, ad_account_id: UUID) -> list[dict]:
    rows = await db.fetch(
        "SELECT key, spec, baseline, status, first_seen_at, ran_at FROM next_item "
        "WHERE org_id=$1 AND ad_account_id=$2 AND kind='recut' AND baseline IS NOT NULL "
        "AND baseline ? 'pid' ORDER BY first_seen_at",
        org_id,
        ad_account_id,
    )
    return [dict(r) for r in rows]


async def set_status(db: Database, ad_account_id: UUID, key: str, status: str) -> None:
    await db.execute(
        "UPDATE next_item SET status=$3, ran_at = CASE WHEN $3='ran' THEN now() ELSE ran_at END "
        "WHERE ad_account_id=$1 AND key=$2 AND status <> $3",
        ad_account_id,
        key,
        status,
    )


def baseline_from(row: dict) -> Baseline:
    b = row["baseline"]
    return Baseline(
        pid=str(b["pid"]),
        dimension=str(b["dimension"]),
        value=str(b["value"]),
        add=bool(b["add"]),
        label=str(b.get("label") or b["dimension"]),
        metric_name=str(b.get("metric_name") or ""),
        spend_30d=float(b.get("spend_30d") or 0),
    )


def _right_side(unit: Unit, base: Baseline) -> bool:
    """Is this file on the side the card asked for?

    `add` means the card wanted the attribute present, so carrying it is the right side; a
    drop card inverts that. Read off the unit's own facets, which come from the nightly
    account read's tagging rather than from anything measured here.
    """
    carries = unit.has(base.dimension, base.value)
    return carries if base.add else not carries


def now_from(unit: Unit | None, spend_30d: float, base: Baseline) -> Now:
    if unit is None:
        return Now(on_right_side=None, spend_30d=spend_30d)
    return Now(on_right_side=_right_side(unit, base), spend_30d=spend_30d)


def adoption_from(
    units_by_sha: dict[str, Unit],
    first_day_by_sha: dict[str, object],
    base: Baseline,
    since,
    exclude: str,
) -> Adoption | None:
    """Files that first delivered after the card was issued, split on the card's attribute.

    "New" is the file's first day of delivery, not when this product noticed it: a file the
    account launched last week and the tagger reached yesterday is still newer work.
    """
    if since is None:
        return None
    right = wrong = 0
    for sha, unit in units_by_sha.items():
        first = first_day_by_sha.get(sha)
        if sha == exclude or first is None or first <= since:
            continue
        if _right_side(unit, base):
            right += 1
        else:
            wrong += 1
    return Adoption(n_right=right, n_wrong=wrong)
