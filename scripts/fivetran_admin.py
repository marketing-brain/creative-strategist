"""One-off Fivetran connector admin CLI -- table enable/disable, custom report config,
and sync triggering against a single connector. Not imported by the app; exists so these
admin actions are auditable/reviewable commands instead of ad-hoc inline scripts.

Usage:
  python scripts/fivetran_admin.py status <connector_id>
  python scripts/fivetran_admin.py enable-table <connector_id> <schema> <table>
  python scripts/fivetran_admin.py disable-table <connector_id> <schema> <table>
  python scripts/fivetran_admin.py set-custom-report <connector_id> --name NAME --level ad
      --aggregation Day --fields video_p25_watched_actions,video_p50_watched_actions
      [--action-report-time mixed]
  python scripts/fivetran_admin.py sync <connector_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from cs.connectors.fivetran.client import (
    FivetranError,
    _call,
    get_connector,
    set_paused,
)
from cs.core.config import get_settings


async def cmd_status(http: httpx.AsyncClient, args) -> None:
    settings = get_settings()
    data = await get_connector(settings, http, args.connector_id)
    print(json.dumps(data, indent=2))


async def cmd_set_table_enabled(http: httpx.AsyncClient, args, enabled: bool) -> None:
    settings = get_settings()
    data = await _call(
        settings,
        http,
        "PATCH",
        f"/connectors/{args.connector_id}/schemas/{args.schema}/tables/{args.table}",
        {"enabled": enabled},
    )
    tables = data["schemas"][args.schema]["tables"]
    print("Enabled tables now:", sorted(t for t, v in tables.items() if v.get("enabled")))


async def cmd_set_custom_report(http: httpx.AsyncClient, args) -> None:
    settings = get_settings()
    current = await get_connector(settings, http, args.connector_id)
    new_config = dict(current["config"])
    entry = {
        "table_name": args.name,
        "config_type": "Custom",
        "level": args.level,
        "aggregation": args.aggregation,
        "fields": args.fields.split(","),
    }
    if args.action_report_time:
        entry["action_report_time"] = args.action_report_time
    existing = [t for t in new_config.get("custom_tables", []) if t.get("table_name") != args.name]
    new_config["custom_tables"] = existing + [entry]
    data = await _call(
        settings, http, "PATCH", f"/connectors/{args.connector_id}", {"config": new_config}
    )
    print(json.dumps(data.get("config", {}).get("custom_tables", []), indent=2))


async def cmd_sync(http: httpx.AsyncClient, args) -> None:
    settings = get_settings()
    data = await _call(
        settings, http, "POST", f"/connectors/{args.connector_id}/sync", {"force": False}
    )
    print(json.dumps(data, indent=2))


async def cmd_unpause(http: httpx.AsyncClient, args) -> None:
    settings = get_settings()
    await set_paused(settings, http, args.connector_id, False)
    print("unpaused")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status")
    s.add_argument("connector_id")

    for name in ("enable-table", "disable-table"):
        t = sub.add_parser(name)
        t.add_argument("connector_id")
        t.add_argument("schema")
        t.add_argument("table")

    c = sub.add_parser("set-custom-report")
    c.add_argument("connector_id")
    c.add_argument("--name", required=True)
    c.add_argument("--level", required=True, choices=["ad", "adset", "campaign", "account", "none"])
    c.add_argument("--aggregation", default="Day", choices=["Day", "Week", "Month", "Lifetime"])
    c.add_argument("--fields", required=True, help="comma-separated field names")
    c.add_argument(
        "--action-report-time",
        default=None,
        choices=[None, "impression", "mixed", "conversion"],
    )

    y = sub.add_parser("sync")
    y.add_argument("connector_id")

    u = sub.add_parser("unpause")
    u.add_argument("connector_id")

    return p


async def main() -> None:
    args = build_parser().parse_args()
    async with httpx.AsyncClient() as http:
        try:
            if args.cmd == "status":
                await cmd_status(http, args)
            elif args.cmd == "enable-table":
                await cmd_set_table_enabled(http, args, True)
            elif args.cmd == "disable-table":
                await cmd_set_table_enabled(http, args, False)
            elif args.cmd == "set-custom-report":
                await cmd_set_custom_report(http, args)
            elif args.cmd == "sync":
                await cmd_sync(http, args)
            elif args.cmd == "unpause":
                await cmd_unpause(http, args)
        except FivetranError as e:
            print("FivetranError:", e)
            raise SystemExit(1) from e


if __name__ == "__main__":
    asyncio.run(main())
