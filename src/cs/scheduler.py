import asyncio

from cs.core.logging import configure_logging
from cs.jobs.scheduler import run_scheduler

if __name__ == "__main__":
    configure_logging()
    asyncio.run(run_scheduler())
