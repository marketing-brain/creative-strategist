from __future__ import annotations

import asyncio
import logging
import socket
import traceback

from cs.core import queue
from cs.core.blobs import BlobStore, blob_store_from_settings
from cs.core.config import Settings, get_settings
from cs.core.db import Database
from cs.jobs.registry import HANDLERS, JobContext

log = logging.getLogger("cs.worker")


async def run_once(db: Database, blobs: BlobStore, settings: Settings, worker_id: str) -> bool:
    job = await queue.claim(db, worker_id)
    if job is None:
        return False
    fn = HANDLERS.get(job.job_type)
    if fn is None:
        await queue.fail(db, job.id, f"no handler registered for {job.job_type}")
        return True
    try:
        await fn(JobContext(db=db, blobs=blobs, settings=settings, job=job))
        await queue.complete(db, job.id)
        log.info(
            "job done",
            extra={"job_id": str(job.id), "job_type": job.job_type, "org_id": str(job.org_id)},
        )
    except Exception as exc:  # noqa: BLE001 - worker must survive any handler failure
        await queue.fail(db, job.id, f"{exc}\n{traceback.format_exc()}")
        log.exception("job failed", extra={"job_id": str(job.id), "job_type": job.job_type})
    return True


async def run_worker(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    import cs.jobs.all_handlers  # noqa: F401 - registers handlers from every module

    db = await Database.connect(settings.database_url)
    blobs = blob_store_from_settings(settings)
    worker_id = f"{socket.gethostname()}:{id(db)}"
    log.info("worker started", extra={"worker_id": worker_id})
    try:
        while True:
            ran = await run_once(db, blobs, settings, worker_id)
            if not ran:
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await db.close()
