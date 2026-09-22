-- migrate:up
-- The thirty-day rollups end on each ad account's newest day with delivery, the same anchor
-- every window on the creative page uses (repo.account_last_metric_day), not on the wall
-- clock. Anchored on `current_date` they slid off the data whenever syncs stopped (by
-- 2026-09-21 the grid's "30 days" held 23 days of delivery) and needed an hourly refresh
-- just to keep sliding. Now they only move when metric_daily does, so the sync's own
-- refresh is the only one (the core.refresh_metric_rollups schedule is removed).
DROP MATERIALIZED VIEW ad_metric_30d;
DROP MATERIALIZED VIEW creative_metric_30d;

CREATE MATERIALIZED VIEW creative_metric_30d AS
WITH last AS (
  SELECT ad_account_id, max(day) AS day FROM metric_daily WHERE impressions > 0
  GROUP BY ad_account_id
)
SELECT m.org_id, m.creative_id,
       sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
       sum(link_clicks) AS link_clicks, sum(video_3s_views) AS video_3s_views,
       sum(purchases) AS purchases, sum(conversions) AS conversions,
       sum(conversion_value) AS conversion_value
FROM metric_daily m JOIN last l ON l.ad_account_id = m.ad_account_id
WHERE m.day > l.day - 30 AND m.day <= l.day
GROUP BY m.org_id, m.creative_id;
CREATE UNIQUE INDEX creative_metric_30d_creative_uidx ON creative_metric_30d (creative_id);
CREATE INDEX creative_metric_30d_org_idx ON creative_metric_30d (org_id);

CREATE MATERIALIZED VIEW ad_metric_30d AS
WITH last AS (
  SELECT ad_account_id, max(day) AS day FROM metric_daily WHERE impressions > 0
  GROUP BY ad_account_id
)
SELECT m.org_id, m.creative_id, m.ad_id,
       sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
       sum(conversions) AS conversions, sum(conversion_value) AS conversion_value,
       sum(purchases) AS purchases
FROM metric_daily m JOIN last l ON l.ad_account_id = m.ad_account_id
WHERE m.day > l.day - 30 AND m.day <= l.day
GROUP BY m.org_id, m.creative_id, m.ad_id;
CREATE UNIQUE INDEX ad_metric_30d_ad_uidx ON ad_metric_30d (ad_id, creative_id);
CREATE INDEX ad_metric_30d_org_idx ON ad_metric_30d (org_id);
CREATE INDEX ad_metric_30d_creative_idx ON ad_metric_30d (creative_id);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON creative_metric_30d, ad_metric_30d FROM anon, authenticated;
  END IF;
END $$;

DELETE FROM schedule_state WHERE name = 'core.refresh_metric_rollups';

-- migrate:down
DROP MATERIALIZED VIEW ad_metric_30d;
DROP MATERIALIZED VIEW creative_metric_30d;
CREATE MATERIALIZED VIEW creative_metric_30d AS
SELECT org_id, creative_id,
       sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
       sum(link_clicks) AS link_clicks, sum(video_3s_views) AS video_3s_views,
       sum(purchases) AS purchases, sum(conversions) AS conversions,
       sum(conversion_value) AS conversion_value
FROM metric_daily WHERE day > current_date - 30
GROUP BY org_id, creative_id;
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
