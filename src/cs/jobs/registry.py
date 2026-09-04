from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.queue import Job


@dataclass
class JobContext:
    db: Database
    blobs: BlobStore
    settings: Settings
    job: Job


Handler = Callable[[JobContext], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco
