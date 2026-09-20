-- migrate:up
-- The creatives grid, its count and the footage panel each summed thirty days of
-- metric_daily for the whole org on every request. The numbers only move when a sync lands
-- or the window slides a day, so they are summed once here and refreshed after each sync and
-- hourly (cs.core.repo.rollups). `current_date` is fixed at refresh time.
CREATE MATERIALIZED VIEW creative_metric_30d AS
SELECT org_id, creative_id,
       sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
       sum(link_clicks) AS link_clicks, sum(video_3s_views) AS video_3s_views,
       sum(purchases) AS purchases, sum(conversions) AS conversions,
       sum(conversion_value) AS conversion_value
FROM metric_daily WHERE day > current_date - 30
GROUP BY org_id, creative_id;
-- REFRESH ... CONCURRENTLY needs a unique index; it also serves the creative_id lookups.
CREATE UNIQUE INDEX creative_metric_30d_creative_uidx ON creative_metric_30d (creative_id);
CREATE INDEX creative_metric_30d_org_idx ON creative_metric_30d (org_id);

CREATE MATERIALIZED VIEW ad_metric_30d AS
SELECT org_id, creative_id, ad_id,
       sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
       sum(conversions) AS conversions, sum(conversion_value) AS conversion_value,
       sum(purchases) AS purchases
FROM metric_daily WHERE day > current_date - 30
GROUP BY org_id, creative_id, ad_id;
CREATE UNIQUE INDEX ad_metric_30d_ad_uidx ON ad_metric_30d (ad_id, creative_id);
CREATE INDEX ad_metric_30d_org_idx ON ad_metric_30d (org_id);
CREATE INDEX ad_metric_30d_creative_idx ON ad_metric_30d (creative_id);

-- Supabase grants its API roles access to new objects in public, and row security does not
-- apply to a materialized view. The app reads these over its own connection only.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON creative_metric_30d, ad_metric_30d FROM anon, authenticated;
  END IF;
END $$;

-- migrate:down
DROP MATERIALIZED VIEW ad_metric_30d;
DROP MATERIALIZED VIEW creative_metric_30d;
