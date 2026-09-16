"""Adds the custom reports in `cs.connectors.fivetran.client.CUSTOM_TABLES` to an org's
existing Fivetran connector and enables their tables.

New connectors get every report at creation. Connectors created before a report was
added to CUSTOM_TABLES (org 2 and org 3 as of 2026-09-14) do not, and
lock_down_and_activate's ALLOW_COLUMNS setting blocks any table Fivetran discovers
later -- so a new report's tables stay disabled until something enables them.

Usage:
    uv run python scripts/apply_fivetran_custom_reports.py --org <uuid>           # dry run
    uv run python scripts/apply_fivetran_custom_reports.py --org <uuid> --apply   # change it

DRY RUN IS THE DEFAULT: it only reads the connector config from Fivetran. --apply
PATCHes the connector's custom_tables (missing reports added, our non-frozen reports gain
missing fields, nothing is removed), runs Fivetran's setup tests and restores the previous
reports if the connector is no longer `connected`, then enables the reports' tables and any
disabled REQUIRED_TABLES. Fivetran may list a report's child tables only after its next
sync, so re-run with --apply after that sync; it is idempotent.

Cost: every new report adds Fivetran monthly active rows (placement_ad multiplies
ad-days by placements; each list field is its own child table).
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

import httpx

from cs.connectors.fivetran import client as fivetran_client
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.repo import get_meta_connection


async def run(org_id: UUID, apply: bool) -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    try:
        conn = await get_meta_connection(db, org_id)
    finally:
        await db.close()
    if conn is None:
        raise SystemExit(f"org {org_id} has no Meta connection")
    connector_id, schema = conn["fivetran_connector_id"], conn["schema_name"]

    async with httpx.AsyncClient(timeout=300) as http:
        connector = await fivetran_client.get_connector(settings, http, connector_id)
        plan = fivetran_client.plan_custom_reports(connector)
        print(f"connector {connector_id} (schema {schema})")
        print(f"  reports present: {', '.join(plan['present']) or '(none)'}")
        print(f"  reports missing: {', '.join(plan['missing']) or '(none)'}")
        for name, fields in plan["extend"].items():
            print(f"  extend {name}: + {', '.join(fields)}")
        if plan["conflicts"]:
            print(f"  conflicts (breakdowns differ, left alone): {', '.join(plan['conflicts'])}")
        if not apply:
            print("dry run -- pass --apply to add missing reports and enable their tables")
            return
        result = await fivetran_client.apply_custom_reports(settings, http, connector_id, schema)
    print(f"  added reports:  {', '.join(result['added_reports']) or '(none)'}")
    for name, fields in result["extended_reports"].items():
        print(f"  extended {name}: + {', '.join(fields)}")
    print(f"  enabled tables: {', '.join(result['enabled_tables']) or '(none)'}")
    if result["added_reports"] or result["extended_reports"]:
        print("re-run with --apply after the next Fivetran sync to enable child tables")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", required=True, help="org UUID")
    p.add_argument(
        "--apply", action="store_true", help="change the connector (default: dry run, read-only)"
    )
    args = p.parse_args()
    asyncio.run(run(UUID(args.org), args.apply))


if __name__ == "__main__":
    main()
