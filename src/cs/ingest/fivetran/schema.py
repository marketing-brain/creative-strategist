from __future__ import annotations

CAMPAIGN_HISTORY = "campaign_history"
AD_HISTORY = "ad_history"
CREATIVE_HISTORY = "creative_history"
AD_VIDEO_HISTORY = "ad_video_history"

ENTITY_TABLES = (CAMPAIGN_HISTORY, AD_HISTORY, CREATIVE_HISTORY, AD_VIDEO_HISTORY)

BASIC_AD = "basic_ad"
BASIC_AD_ACTIONS = "basic_ad_actions"
BASIC_AD_ACTION_VALUES = "basic_ad_action_values"

INSIGHTS_ACTION_TABLES = (BASIC_AD_ACTIONS, BASIC_AD_ACTION_VALUES)

ALL_TABLES = ENTITY_TABLES + (BASIC_AD,) + INSIGHTS_ACTION_TABLES

VIDEO_RETENTION_AD = "video_retention_ad"

# Fivetran unpivots each list<AdsActionStats> field of the custom report into its own
# child table named <report>_<field>. Maps child-table suffix -> metric_daily column.
VIDEO_RETENTION_CHILD_COLUMNS = {
    "video_play_actions": "video_plays",
    "video_p_25_watched_actions": "video_p25",
    "video_p_50_watched_actions": "video_p50",
    "video_p_75_watched_actions": "video_p75",
    "video_p_95_watched_actions": "video_p95",
    "video_p_100_watched_actions": "video_p100",
    "video_thruplay_watched_actions": "video_thruplay",
    "video_avg_time_watched_actions": "video_avg_time_watched",  # seconds, per-day average
}
