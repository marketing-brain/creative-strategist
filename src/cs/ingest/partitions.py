from datetime import date

from cs.core.db import Database


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


async def ensure_metric_partition(db: Database, day: date) -> None:
    start, end = _month_bounds(day)
    name = f"metric_daily_{start:%Y%m}"
    await db.execute(
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF metric_daily "
        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
    )
