-- migrate:up
-- A rendered variant of a brief: the edit it was cut from, the source it was cut from and
-- where the MP4 is. Rendering is free (ffmpeg on the worker), so a new edit is a new row and
-- the page shows the newest per variant code.
CREATE TABLE brief_render (
  id uuid PRIMARY KEY,
  brief_id uuid NOT NULL REFERENCES creative_brief(id) ON DELETE CASCADE,
  org_id uuid NOT NULL REFERENCES org(id),
  code text NOT NULL,
  status text NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
  edit jsonb NOT NULL,
  source text NOT NULL,
  path text,
  width integer,
  height integer,
  duration_s real,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX brief_render_brief_idx ON brief_render (brief_id, code, created_at DESC);

-- A higher-quality copy of the parent a person uploaded. Renders cut from it instead of the
-- stored copy; the API refuses a file whose length differs from the ad's, since every cut
-- time was measured on the stored copy.
ALTER TABLE creative_brief ADD COLUMN original_path text;

-- migrate:down
ALTER TABLE creative_brief DROP COLUMN original_path;
DROP TABLE brief_render;
