"""Single source of truth for which Fivetran-landed Meta Ads tables this app reads.

fivetran/transform.py, fivetran/insights.py, and fivetran/fixtures.py all reference these
names instead of their own string literals. A table not listed here (e.g. ad_set_history)
may still be landed by Fivetran's facebook_ads connector, but nothing in this app ever reads
it -- a candidate for disabling on the connector's schema config.
"""

from __future__ import annotations

CAMPAIGN_HISTORY = "campaign_history"
AD_HISTORY = "ad_history"
CREATIVE_HISTORY = "creative_history"
AD_VIDEO_HISTORY = "ad_video_history"

ENTITY_TABLES = (CAMPAIGN_HISTORY, AD_HISTORY, CREATIVE_HISTORY, AD_VIDEO_HISTORY)

AD_INSIGHTS = "ad_insights"

AD_INSIGHTS_ACTIONS = "ad_insights_actions"
AD_INSIGHTS_ACTION_VALUES = "ad_insights_action_values"
AD_INSIGHTS_VIDEO_PLAY_ACTIONS = "ad_insights_video_play_actions"
AD_INSIGHTS_VIDEO_THRUPLAY_WATCHED_ACTIONS = "ad_insights_video_thruplay_watched_actions"
AD_INSIGHTS_VIDEO_P50_WATCHED_ACTIONS = "ad_insights_video_p50_watched_actions"
AD_INSIGHTS_VIDEO_P100_WATCHED_ACTIONS = "ad_insights_video_p100_watched_actions"

INSIGHTS_ACTION_TABLES = (
    AD_INSIGHTS_ACTIONS,
    AD_INSIGHTS_ACTION_VALUES,
    AD_INSIGHTS_VIDEO_PLAY_ACTIONS,
    AD_INSIGHTS_VIDEO_THRUPLAY_WATCHED_ACTIONS,
    AD_INSIGHTS_VIDEO_P50_WATCHED_ACTIONS,
    AD_INSIGHTS_VIDEO_P100_WATCHED_ACTIONS,
)

ALL_TABLES = ENTITY_TABLES + (AD_INSIGHTS,) + INSIGHTS_ACTION_TABLES
