import logging
import time

import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cs.api.routers import (
    accounts,
    blobs,
    briefs,
    connections,
    creatives,
    experiments,
    generate,
    health,
    insights,
    library,
    make,
    tags,
    what_works,
)
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database

SLOW_S = 2.0


def create_app(settings: Settings, db: Database, blobs_store: BlobStore) -> FastAPI:
    app = FastAPI(title="creative-strategist")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Any request slower than SLOW_S is logged with its path and time, so a slow page can be
    # traced to the call behind it instead of guessed at.
    slow_log = logging.getLogger("cs.api.slow")

    @app.middleware("http")
    async def _time_requests(request: Request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        took = time.perf_counter() - t0
        if took >= SLOW_S:
            slow_log.warning("slow request %s %s took %.1fs", request.method, request.url.path, took)
        response.headers["Server-Timing"] = f"app;dur={took * 1000:.0f}"
        return response

    # A query that outran the pool's statement timeout was cancelled to protect the
    # database. Say so, rather than a bare 500, so the page can ask for a retry.
    async def _query_too_slow(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse({"detail": "That took too long to load. Try again."}, status_code=503)

    app.add_exception_handler(asyncpg.exceptions.QueryCanceledError, _query_too_slow)
    app.add_exception_handler(TimeoutError, _query_too_slow)
    app.state.settings = settings
    app.state.db = db
    app.state.blobs = blobs_store
    app.include_router(health.router)
    app.include_router(blobs.router)
    app.include_router(connections.router)
    app.include_router(accounts.router)
    app.include_router(creatives.router)
    app.include_router(library.router)
    app.include_router(experiments.router)
    app.include_router(briefs.router)
    app.include_router(generate.router)
    app.include_router(make.router)
    app.include_router(what_works.router)
    app.include_router(tags.router)
    app.include_router(insights.router)
    return app
