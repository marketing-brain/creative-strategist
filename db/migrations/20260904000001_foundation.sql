-- migrate:up
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE org (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  clerk_org_id TEXT UNIQUE,
  active_brand_kit_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_org (
  user_id TEXT NOT NULL,
  org_id UUID NOT NULL REFERENCES org(id),
  role TEXT NOT NULL CHECK (role IN ('member', 'admin')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, org_id)
);

CREATE TABLE job (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  job_type TEXT NOT NULL,
  dedup_key TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed', 'dead')),
  attempts INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 5,
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_by TEXT,
  locked_at TIMESTAMPTZ,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX job_pending_dedup_idx ON job (org_id, job_type, dedup_key)
  WHERE status IN ('pending', 'running') AND dedup_key IS NOT NULL;
CREATE INDEX job_claim_idx ON job (status, run_after) WHERE status = 'pending';

-- migrate:down
DROP TABLE job;
DROP TABLE user_org;
DROP TABLE org;
