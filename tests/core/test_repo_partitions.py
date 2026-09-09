from datetime import date

import asyncpg
import pytest

from cs.core.repo.partitions import ensure_metric_partition


async def test_ensure_partition_is_idempotent(db):
    await db.execute("DROP TABLE IF EXISTS metric_daily_202609")
    await ensure_metric_partition(db, date(2026, 9, 15))
    await ensure_metric_partition(db, date(2026, 9, 1))
    names = await db.fetch("SELECT tablename FROM pg_tables WHERE tablename LIKE 'metric_daily_%'")
    tablenames = [r["tablename"] for r in names]
    assert tablenames.count("metric_daily_202609") == 1


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.exceptions.DuplicateTableError("already exists"),
        asyncpg.exceptions.UniqueViolationError("duplicate key"),
    ],
)
async def test_ensure_partition_tolerates_a_concurrent_creator(error):
    class RacingDb:
        async def execute(self, sql, *args):
            raise error

    # A concurrent sync won the race; the partition exists, so this must not raise.
    await ensure_metric_partition(RacingDb(), date(2026, 9, 15))
