from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import traceback
import uuid

from cs.core import queue
from cs.core.blobs import BlobStore, blob_store_from_settings
from cs.core.config import Settings, get_settings
from cs.core.db import Database
from cs.jobs.registry import HANDLERS, JobContext

log = logging.getLogger("cs.worker")


async def _keep_lease(
    db: Database, job: queue.Job, worker_id: str, interval: float, task: asyncio.Task
) -> bool:
    """Heartbeat a running job until its lease is lost; then cancel the handler."""
    while True:
        await asyncio.sleep(interval)
        if not await queue.heartbeat(db, job.id, worker_id):
            log.warning(
                "job lease lost, cancelling handler",
                extra={"job_id": str(job.id), "job_type": job.job_type, "worker_id": worker_id},
            )
            task.cancel()
            return False


async def run_once(db: Database, blobs: BlobStore, settings: Settings, worker_id: str) -> bool:
    job = await queue.claim(db, worker_id)
    if job is None:
        return False
    fn = HANDLERS.get(job.job_type)
    if fn is None:
        await queue.fail(db, job.id, worker_id, f"no handler registered for {job.job_type}")
        return True
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=job)
    task = asyncio.create_task(fn(ctx))
    lease = asyncio.create_task(
        _keep_lease(db, job, worker_id, max(5, settings.job_lease_seconds // 3), task)
    )
    try:
        await task
        await queue.complete(db, job.id, worker_id)
        log.info(
            "job done",
            extra={"job_id": str(job.id), "job_type": job.job_type, "org_id": str(job.org_id)},
        )
    except asyncio.CancelledError:
        if not lease.done():
            raise  # the worker itself is being shut down
        await queue.fail(db, job.id, worker_id, "lease expired while the handler was running")
    except Exception as exc:  # noqa: BLE001 - worker must survive any handler failure
        await queue.fail(db, job.id, worker_id, f"{exc}\n{traceback.format_exc()}")
        log.exception(
            "job failed",
            extra={"job_id": str(job.id), "job_type": job.job_type, "org_id": str(job.org_id)},
        )
    finally:
        lease.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lease
    return True


async def run_worker(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    import cs.jobs.all_handlers  # noqa: F401 - registers handlers from every module

    db = await Database.connect(settings.database_url)
    blobs = blob_store_from_settings(settings)
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    log.info("worker started", extra={"worker_id": worker_id})
    try:
        while True:
            ran = await run_once(db, blobs, settings, worker_id)
            if not ran:
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await db.close()
