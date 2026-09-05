from datetime import date

from cs.ingest.partitions import ensure_metric_partition


async def test_ensure_partition_is_idempotent(db):
    await db.execute("DROP TABLE IF EXISTS metric_daily_202609")
    await ensure_metric_partition(db, date(2026, 9, 15))
    await ensure_metric_partition(db, date(2026, 9, 1))
    names = await db.fetch("SELECT tablename FROM pg_tables WHERE tablename LIKE 'metric_daily_%'")
    tablenames = [r["tablename"] for r in names]
    assert tablenames.count("metric_daily_202609") == 1
