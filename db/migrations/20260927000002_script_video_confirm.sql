-- migrate:up
-- The clips are the expensive step, so a video stops after planning, voicing and finding the
-- product ('confirm') with the exact clip cost, and generates clips only once approved.
ALTER TABLE script_video DROP CONSTRAINT script_video_status_check;
ALTER TABLE script_video ADD CONSTRAINT script_video_status_check
  CHECK (status IN ('queued', 'running', 'confirm', 'blocked', 'done', 'failed'));
ALTER TABLE script_video ADD COLUMN clip_cost_usd numeric(8, 2);
ALTER TABLE script_video ADD COLUMN approved_at timestamptz;

-- migrate:down
ALTER TABLE script_video DROP COLUMN approved_at;
ALTER TABLE script_video DROP COLUMN clip_cost_usd;
ALTER TABLE script_video DROP CONSTRAINT script_video_status_check;
ALTER TABLE script_video ADD CONSTRAINT script_video_status_check
  CHECK (status IN ('queued', 'running', 'blocked', 'done', 'failed'));
