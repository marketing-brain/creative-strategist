"""Import every module that registers job handlers so the worker sees them."""

import cs.briefs.jobs  # noqa: F401
import cs.footage.refresh  # noqa: F401
import cs.generate.jobs  # noqa: F401
import cs.ingest.durations  # noqa: F401
import cs.ingest.jobs  # noqa: F401
import cs.insights.account_read.jobs  # noqa: F401
import cs.insights.jobs  # noqa: F401
import cs.insights.next_plan.jobs  # noqa: F401
import cs.make.fixes  # noqa: F401
import cs.make.script_video  # noqa: F401
import cs.tagging.jobs  # noqa: F401
