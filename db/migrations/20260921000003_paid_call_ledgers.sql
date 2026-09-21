-- migrate:up
-- What was already paid for lives in tables of its own, never in the job queue: `done` jobs
-- are purged after thirty days and `done` used to mean both "found" and "gave up", which
-- left 178 give-ups one sync away from being paid for again.
--
-- One Apify lookup per creative, ever. The row is written as 'started' before the paid call,
-- so a worker that dies mid-call leaves a row and nothing pays twice; 'found' carries the
-- file URL the download job fetches (a free step with its own retries).
CREATE TABLE video_resolution (
  creative_id uuid PRIMARY KEY REFERENCES creative(id),
  org_id uuid NOT NULL REFERENCES org(id),
  source text NOT NULL CHECK (source IN ('instagram', 'facebook')),
  status text NOT NULL CHECK (status IN ('started', 'found', 'failed')),
  video_url text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

-- Every lookup so far, from its latest job: a stored file means it was found.
INSERT INTO video_resolution (creative_id, org_id, source, status, error, created_at, finished_at)
SELECT DISTINCT ON (c.id)
       c.id, c.org_id,
       CASE j.job_type WHEN 'meta.resolve_instagram_video' THEN 'instagram' ELSE 'facebook' END,
       CASE WHEN c.primary_asset_id IS NOT NULL THEN 'found' ELSE 'failed' END,
       CASE WHEN c.primary_asset_id IS NULL
            THEN coalesce(split_part(j.error, E'\n', 1), 'no video found') END,
       j.created_at, coalesce(j.finished_at, j.created_at)
FROM job j
JOIN creative c ON c.id::text = j.dedup_key AND c.org_id = j.org_id
WHERE j.job_type IN ('meta.resolve_instagram_video', 'meta.resolve_facebook_video')
  AND j.status IN ('done', 'dead')
ORDER BY c.id, j.created_at DESC;

-- A write-up whose model call failed is recorded as 'failed' instead of retried, so the
-- nightly sweep reads this table, not dead jobs, to know a creative was already paid for.
ALTER TABLE creative_insight DROP CONSTRAINT creative_insight_status_check;
ALTER TABLE creative_insight ADD CONSTRAINT creative_insight_status_check
  CHECK (status IN ('done', 'rejected', 'failed'));
ALTER TABLE creative_insight ADD COLUMN error text;

-- migrate:down
ALTER TABLE creative_insight DROP COLUMN error;
DELETE FROM creative_insight WHERE status = 'failed';
ALTER TABLE creative_insight DROP CONSTRAINT creative_insight_status_check;
ALTER TABLE creative_insight ADD CONSTRAINT creative_insight_status_check
  CHECK (status IN ('done', 'rejected'));
DROP TABLE video_resolution;
