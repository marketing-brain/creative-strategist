import asyncio

from cs.core.logging import configure_logging
from cs.jobs.worker import run_worker

if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_worker())
