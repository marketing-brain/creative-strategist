-- migrate:up
-- Two decisions that were stored as the date 2100-01-01 become real state (2026-09-22).

-- A schedule is paused by a flag the scheduler reads every tick, not by a last run in the
-- future that only took effect after a restart and that every reader had to recognise.
-- The paused rows' real last run is unknown; now() means a resumed schedule waits one period.
ALTER TABLE schedule_state ADD COLUMN paused boolean NOT NULL DEFAULT false;
UPDATE schedule_state SET paused = true, last_run_at = now() WHERE last_run_at > now();

-- A lookup someone decided not to pay for is a ledger row like any other outcome, so no sync
-- queues it again. The 229 held on 2026-09-21 came from Fivetran re-landing
-- ad_asset_feed_spec_video; the first 69 of that batch found 1 video.
ALTER TABLE video_resolution DROP CONSTRAINT video_resolution_status_check;
ALTER TABLE video_resolution ADD CONSTRAINT video_resolution_status_check
  CHECK (status IN ('started', 'found', 'failed', 'skipped'));

INSERT INTO video_resolution (creative_id, org_id, source, status, error, finished_at)
SELECT DISTINCT ON (c.id)
       c.id, c.org_id,
       CASE j.job_type WHEN 'meta.resolve_instagram_video' THEN 'instagram' ELSE 'facebook' END,
       'skipped', 'not looked up: held on 2026-09-21 after 1 of 69 in the batch found a video',
       now()
FROM job j JOIN creative c ON c.id::text = j.dedup_key AND c.org_id = j.org_id
WHERE j.job_type IN ('meta.resolve_instagram_video', 'meta.resolve_facebook_video')
  AND j.status = 'pending' AND j.run_after > now() + interval '1 year'
ORDER BY c.id
ON CONFLICT (creative_id) DO NOTHING;

DELETE FROM job
WHERE job_type IN ('meta.resolve_instagram_video', 'meta.resolve_facebook_video')
  AND status = 'pending' AND run_after > now() + interval '1 year';

-- migrate:down
DELETE FROM video_resolution WHERE status = 'skipped';
ALTER TABLE video_resolution DROP CONSTRAINT video_resolution_status_check;
ALTER TABLE video_resolution ADD CONSTRAINT video_resolution_status_check
  CHECK (status IN ('started', 'found', 'failed'));
UPDATE schedule_state SET last_run_at = '2100-01-01' WHERE paused;
ALTER TABLE schedule_state DROP COLUMN paused;
