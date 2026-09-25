import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cs.api.routers import (
    accounts,
    blobs,
    connections,
    creatives,
    experiments,
    health,
    insights,
    library,
    tags,
)
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database


def create_app(settings: Settings, db: Database, blobs_store: BlobStore) -> FastAPI:
    app = FastAPI(title="creative-strategist")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
    app.include_router(tags.router)
    app.include_router(insights.router)
    return app
