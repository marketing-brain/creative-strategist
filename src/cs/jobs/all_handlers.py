"""Import every module that registers job handlers so the worker sees them."""

import cs.generation.jobs  # noqa: F401
import cs.ingest.jobs  # noqa: F401
import cs.insights.jobs  # noqa: F401
import cs.publish.jobs  # noqa: F401
import cs.tagging.jobs  # noqa: F401
