"""Structure the reviewed cluster findings into the What works playbook (cs.experiments.playbook).
One paid gemini-3-flash call, logged; --dry-run prints the input size and costs nothing.

Usage:
    uv run python scripts/write_playbook.py --org <uuid> --dry-run
    uv run python scripts/write_playbook.py --org <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from uuid import UUID

import openai

from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import playbook as pb


async def run(org: UUID, dry: bool, fill: bool = False) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    prior = await db.fetchval(
        "SELECT r.payload FROM experiment_result r JOIN experiment e ON e.id = r.experiment_id"
        " WHERE e.org_id=$1 AND e.slug=$2 AND r.key='playbook'",
        org,
        pb.SLUG,
    )
    only = None
    if fill:
        _, allf, _ = await pb.inputs(db, org)
        only = {fid for fid in allf if fid not in (prior or {}).get("findings", {})}
        print(f"filling {len(only)} findings the first call left out")
        if not only:
            return
    eid, findings, text = await pb.inputs(
        db, org, only, (prior or {}).get("choices") if fill else None
    )
    print(
        f"{len(findings)} findings, {len(text):,} characters of evidence (~{len(text) // 4:,} tokens)"
    )
    if dry:
        print(text[:1500])
        return
    key = f"{eid}:{pb.VERSION}:{'fill:' if fill else ''}{hashlib.sha1(text.encode()).hexdigest()[:10]}"
    client = openai.AsyncOpenAI(
        api_key=s.ai_gateway_api_key,
        base_url=f"{s.ai_gateway_base_url}/v1",
        max_retries=0,
        timeout=900,
    )
    out, usage = await pb.call(db, client, s.account_read_model, org, key, text)
    if fill and prior:
        raw = prior.get("raw") or {"choices": [], "findings": []}
        known = {c["key"] for c in raw["choices"]}
        out = {
            "choices": raw["choices"]
            + [c for c in out.get("choices", []) if c["key"] not in known],
            "findings": raw["findings"] + out.get("findings", []),
        }
        usage = {k: usage[k] + (prior.get("usage") or {}).get(k, 0) for k in usage}
    book = pb.build(out, findings)
    book["usage"] = usage
    book["raw"] = out
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1, 'playbook', $2)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload = EXCLUDED.payload, created_at = now()",
        eid,
        book,
    )
    print(
        json.dumps(
            {"usage": usage, "choices": len(book["choices"]), "dropped": book["dropped"]}, indent=1
        )
    )
    for k, c in book["choices"].items():
        fs = [
            (f["tier"], f["direction"], fid)
            for fid, f in book["findings"].items()
            if f["choice_key"] == k
        ]
        print(f"  {c['element']:8} {c['name'][:40]:40} {book['verdicts'][k]:6} {fs}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--fill", action="store_true", help="send only the findings the stored playbook is missing"
    )
    a = p.parse_args()
    asyncio.run(run(a.org, a.dry_run, a.fill))


if __name__ == "__main__":
    main()
