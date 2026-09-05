from __future__ import annotations

from decimal import Decimal


def derived_metrics(
    spend: Decimal, impressions: int, clicks: int, conversions: Decimal, conversion_value: Decimal
) -> dict:
    spend_f, conv_f, val_f = float(spend), float(conversions), float(conversion_value)
    return {
        "ctr": round(clicks / impressions, 6) if impressions else None,
        "cpa": round(spend_f / conv_f, 4) if conv_f else None,
        "roas": round(val_f / spend_f, 4) if spend_f else None,
    }
