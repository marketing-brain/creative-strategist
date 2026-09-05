from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass
class InsightDraft:
    kind: str
    dimension: str
    value: str
    metric: str
    window_start: date
    window_end: date
    sample_creatives: int
    sample_spend: Decimal
    baseline: float
    observed: float
    lift: float
    creative_id: UUID | None = None
    ad_account_id: UUID | None = None
