-- migrate:up
-- Placement and audience breakdowns, kept out of metric_daily so the funnel's existing
-- sums cannot double-count the same impressions. Grain follows the Fivetran reports:
-- placement weekly (weeks sum to ~30 days, the diagnosis window), audience monthly.
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

CREATE TABLE metric_audience (
  org_id UUID NOT NULL REFERENCES org(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  creative_id UUID NOT NULL REFERENCES creative(id),
  ad_id UUID NOT NULL REFERENCES ad(id),
  month DATE NOT NULL,
  age TEXT NOT NULL,
  gender TEXT NOT NULL,
  impressions BIGINT NOT NULL DEFAULT 0,
  reach BIGINT NOT NULL DEFAULT 0,
  spend NUMERIC(14,4) NOT NULL DEFAULT 0,
  link_clicks BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (creative_id, ad_id, month, age, gender)
);
CREATE INDEX metric_audience_account_month_idx ON metric_audience (ad_account_id, month);

-- migrate:down
DROP TABLE metric_audience;
DROP TABLE metric_placement;
