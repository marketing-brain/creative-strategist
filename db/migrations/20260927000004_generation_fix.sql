-- migrate:up
-- A generated fix: one Seedance shot laid over a stretch of an ad (its opening, the point viewers
-- drop, or its close) while the original sound plays on, built from a What works finding for the
-- ad's kind. It is planned first ('confirm', with the exact prompt and cost) and generated only
-- once approved.
ALTER TABLE generation DROP CONSTRAINT generation_status_check;
ALTER TABLE generation ADD CONSTRAINT generation_status_check
  CHECK (status IN ('confirm', 'queued', 'running', 'done', 'failed'));
ALTER TABLE generation ADD COLUMN fix jsonb;
ALTER TABLE generation ADD COLUMN plan jsonb;
ALTER TABLE generation ADD COLUMN approved_at timestamptz;

-- migrate:down
ALTER TABLE generation DROP COLUMN approved_at;
ALTER TABLE generation DROP COLUMN plan;
ALTER TABLE generation DROP COLUMN fix;
ALTER TABLE generation DROP CONSTRAINT generation_status_check;
ALTER TABLE generation ADD CONSTRAINT generation_status_check
  CHECK (status IN ('queued', 'running', 'done', 'failed'));
