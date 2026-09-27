-- migrate:up
-- A generated opening: the first seconds of a video edited from a prompt by a video model, laid
-- back over the original sound and joined to the untouched rest at a real shot change.
-- One row is one paid call; a failed row is never re-sent (see cs.core.model_calls).
CREATE TABLE generation (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL,
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  kind text NOT NULL DEFAULT 'opening_edit',
  prompt text NOT NULL,
  model text NOT NULL,
  resolution text NOT NULL CHECK (resolution IN ('480p', '720p', '1080p')),
  window_s real NOT NULL,
  status text NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
  stage text,
  estimated_cost_usd numeric(8, 2),
  prediction_id text,
  speed real,
  in_sync_until_s real,
  swap_at_s real,
  raw_path text,
  result_path text,
  compare_path text,
  notes jsonb NOT NULL DEFAULT '[]',
  error text,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE INDEX generation_org_idx ON generation (org_id, created_at DESC);

-- migrate:down
DROP TABLE generation;
