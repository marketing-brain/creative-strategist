import asyncio
import os

import uvicorn

from cs.api.app import create_app
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.core.logging import configure_logging


async def _serve() -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    app = create_app(settings, db, blob_store_from_settings(settings))
    # Render (and most PaaS hosts) assign the listen port via $PORT at runtime;
    # 8000 stays the default so local dev and Makefile targets are unaffected.
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    await uvicorn.Server(config).serve()
    await db.close()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(_serve())
