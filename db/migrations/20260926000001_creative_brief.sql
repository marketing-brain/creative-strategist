-- migrate:up
-- A variant brief for one winning video: the numbers and slot map code measured (`facts`),
-- the variants, script and claims list a person reads and edits (`content`), and the model's
-- untouched first draft (`generated`) so an edit never loses what was generated.
CREATE TABLE creative_brief (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL,
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  title text NOT NULL,
  status text NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
  error text,
  facts jsonb,
  content jsonb,
  generated jsonb,
  version integer NOT NULL DEFAULT 1,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX creative_brief_org_idx ON creative_brief (org_id, created_at DESC);

-- migrate:down
DROP TABLE creative_brief;
