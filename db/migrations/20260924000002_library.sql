-- migrate:up
-- The Library browses campaign > ad set > ad, so an ad needs two things it lacked.
--
-- `effective_status` is Meta's delivery state, not the ad's own switch. `status` is what the
-- buyer set on the ad; 372 of org 3's ACTIVE ads sit under a paused ad set and 49 under a
-- paused campaign, so `status` alone would call them live. Ad sets have no synced status
-- (adset_history is not synced), so an ad set or campaign is live when any ad under it is.
ALTER TABLE ad ADD COLUMN effective_status text;

-- Per-ad rates for the Library's cards: link CTR and hook rate need link clicks and 3-second
-- views, which the per-ad rollup did not carry. Same window as 20260922000001.
DROP MATERIALIZED VIEW ad_metric_30d;
CREATE MATERIALIZED VIEW ad_metric_30d AS
WITH last AS (
  SELECT ad_account_id, max(day) AS day FROM metric_daily WHERE impressions > 0
  GROUP BY ad_account_id
)
SELECT m.org_id, m.creative_id, m.ad_id,
       sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
       sum(conversions) AS conversions, sum(conversion_value) AS conversion_value,
       sum(purchases) AS purchases, sum(link_clicks) AS link_clicks,
       sum(video_3s_views) AS video_3s_views
FROM metric_daily m JOIN last l ON l.ad_account_id = m.ad_account_id
WHERE m.day > l.day - 30 AND m.day <= l.day
GROUP BY m.org_id, m.creative_id, m.ad_id;
CREATE UNIQUE INDEX ad_metric_30d_ad_uidx ON ad_metric_30d (ad_id, creative_id);
CREATE INDEX ad_metric_30d_org_idx ON ad_metric_30d (org_id);
CREATE INDEX ad_metric_30d_creative_idx ON ad_metric_30d (creative_id);

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ad_metric_30d FROM anon, authenticated;
  END IF;
END $$;

-- migrate:down
DROP MATERIALIZED VIEW ad_metric_30d;
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
ALTER TABLE ad DROP COLUMN effective_status;
