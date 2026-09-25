-- migrate:up
-- Campaign and ad set names beside their ids, so the grid can filter and label by them
-- without a lookup into the Fivetran schema on every request. Campaign names come from
-- campaign_history; ad set names from basic_ad's adset_name (adset_history is not synced).
-- Display only: names change, ids are what anything joins on.
ALTER TABLE ad ADD COLUMN campaign_name text;
ALTER TABLE ad ADD COLUMN adset_name text;

-- migrate:down
ALTER TABLE ad DROP COLUMN adset_name;
ALTER TABLE ad DROP COLUMN campaign_name;
