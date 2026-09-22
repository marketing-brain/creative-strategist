"""One observation of the event study: a stretch of one video's retention curve."""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Interval:
    """A file's curve between two of Meta's reported points, and what happens on screen in
    between. `share_from` and `share_to` are the shares of plays still watching at each end,
    so the interval is what the audience did while those scenes played."""

    footage_id: UUID
    bucket: int  # index of the interval's own start point, 0..20
    start_s: float
    end_s: float
    share_from: float
    share_to: float
    cuts: int  # scenes starting inside [start_s, end_s), excluding the opening scene
    cuts_to_product: int
    cuts_to_face: int
    duration_s: float
    plays: int

    @property
    def seconds(self) -> float:
        return self.end_s - self.start_s

    @property
    def exit_rate(self) -> float:
        """Viewers lost per second, as a rate: -ln(S_to / S_from) / seconds.

        A rate rather than a percentage because it adds up over intervals and does not
        depend on how many viewers were left when the interval began -- second 30 of a
        video is judged against the people who reached second 30. Meta rounds each ad-day
        to a whole percent, so a curve can tick upward; that lands as a small negative
        rate and is left in rather than clipped, which would bias every average upward.
        """
        if self.share_from <= 0 or self.share_to <= 0:
            return 0.0
        return -math.log(self.share_to / self.share_from) / self.seconds

    @property
    def cuts_per_second(self) -> float:
        return self.cuts / self.seconds
