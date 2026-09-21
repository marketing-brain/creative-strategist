-- migrate:up
-- Placement data is retired (2026-09-21): under Advantage+ Meta picks the placements, the
-- creative page no longer showed placement fit, and ingest stopped writing this table in
-- 85cba4e. The rows were derived from Fivetran's placement_ad report and can be rebuilt from
-- it while that report's tables exist. metric_audience (demographics) stays.
DROP TABLE IF EXISTS metric_placement;

-- migrate:down
CREATE TABLE metric_placement (
  org_id UUID NOT NULL REFERENCES org(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  creative_id UUID NOT NULL REFERENCES creative(id),
  ad_id UUID NOT NULL REFERENCES ad(id),
  week DATE NOT NULL,
  publisher_platform TEXT NOT NULL,
  platform_position TEXT NOT NULL,
  impressions BIGINT NOT NULL DEFAULT 0,
  reach BIGINT NOT NULL DEFAULT 0,
  spend NUMERIC(14,4) NOT NULL DEFAULT 0,
  link_clicks BIGINT NOT NULL DEFAULT 0,
  video_3s_views BIGINT NOT NULL DEFAULT 0,
  video_plays BIGINT NOT NULL DEFAULT 0,
  video_thruplay BIGINT NOT NULL DEFAULT 0,
  video_p25 BIGINT NOT NULL DEFAULT 0,
  video_p50 BIGINT NOT NULL DEFAULT 0,
  video_p75 BIGINT NOT NULL DEFAULT 0,
  video_p100 BIGINT NOT NULL DEFAULT 0,
  purchases BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (creative_id, ad_id, week, publisher_platform, platform_position)
);
CREATE INDEX metric_placement_account_week_idx ON metric_placement (ad_account_id, week);
