import asyncio
import logging

import uvicorn

from cs.api.app import create_app
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database


async def _serve() -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    app = create_app(settings, db, blob_store_from_settings(settings))
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    await uvicorn.Server(config).serve()
    await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())
