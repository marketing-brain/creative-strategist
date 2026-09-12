from __future__ import annotations

CAMPAIGN_HISTORY = "campaign_history"
AD_HISTORY = "ad_history"
CREATIVE_HISTORY = "creative_history"
AD_VIDEO_HISTORY = "ad_video_history"
AD_ASSET_FEED_SPEC_VIDEO = "ad_asset_feed_spec_video"

ENTITY_TABLES = (CAMPAIGN_HISTORY, AD_HISTORY, CREATIVE_HISTORY, AD_VIDEO_HISTORY)

BASIC_AD = "basic_ad"
BASIC_AD_ACTIONS = "basic_ad_actions"
BASIC_AD_ACTION_VALUES = "basic_ad_action_values"

INSIGHTS_ACTION_TABLES = (BASIC_AD_ACTIONS, BASIC_AD_ACTION_VALUES)

ALL_TABLES = ENTITY_TABLES + (BASIC_AD,) + INSIGHTS_ACTION_TABLES
