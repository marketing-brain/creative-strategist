-- migrate:up
-- Clean-up of objects and rows nothing reads any more (2026-09-22).

-- Created with the first insights phase; no code or view has read it since.
DROP VIEW creative_latest_tag;

-- The pattern job that wrote these was removed; only fatigue rows are read (by /next).
DELETE FROM insight WHERE kind = 'pattern';

-- Each run carries a ~5 MB result. The app reads the newest done run per account (falling
-- back to the newest of any status) and /next reads the run its latest plan was built on;
-- everything else is history nothing opens.
DELETE FROM account_read_run r
WHERE r.id NOT IN (
  SELECT DISTINCT ON (ad_account_id) id FROM account_read_run
  ORDER BY ad_account_id, created_at DESC
)
AND r.id NOT IN (
  SELECT DISTINCT ON (ad_account_id) id FROM account_read_run WHERE status = 'done'
  ORDER BY ad_account_id, created_at DESC
)
AND r.id NOT IN (
  SELECT DISTINCT ON (ad_account_id) account_read_run_id FROM next_plan_run
  WHERE account_read_run_id IS NOT NULL
  ORDER BY ad_account_id, created_at DESC
);

-- Finished jobs of types that no longer have a handler.
DELETE FROM job WHERE job_type IN ('insights.compute_patterns', 'insights.weekly_brief')
  AND status IN ('done', 'dead');

-- migrate:down
CREATE VIEW creative_latest_tag AS
SELECT t.org_id, t.creative_id, t.dimension, t.value, t.confidence
FROM tag t
JOIN (
  SELECT DISTINCT ON (creative_id) id, creative_id
  FROM tag_run WHERE status = 'done'
  ORDER BY creative_id, finished_at DESC
) r ON r.id = t.tag_run_id;
