-- migrate:up
-- A made video, once launched, becomes a test under Tests like a Make batch does.
ALTER TABLE script_video ADD COLUMN experiment_id uuid REFERENCES experiment(id) ON DELETE SET NULL;
ALTER TABLE script_video ADD COLUMN launched_at timestamptz;

-- migrate:down
ALTER TABLE script_video DROP COLUMN launched_at;
ALTER TABLE script_video DROP COLUMN experiment_id;
