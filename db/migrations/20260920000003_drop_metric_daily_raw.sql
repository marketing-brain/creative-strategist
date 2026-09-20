-- migrate:up
-- `raw` was a copy of the Fivetran source row on every daily metric row. Nothing reads it,
-- the upsert stopped writing it (4c021d1), and the source rows stay in the connector's own
-- schema in this database (basic_ad; 18,531 source rows against 17,305 here on 2026-09-20).
-- It was about a third of the table's bytes, read past by every aggregate.
ALTER TABLE metric_daily DROP COLUMN raw;

-- migrate:down
ALTER TABLE metric_daily ADD COLUMN raw JSONB NOT NULL DEFAULT '{}'::jsonb;
