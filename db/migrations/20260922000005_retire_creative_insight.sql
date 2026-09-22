-- migrate:up
-- The per-creative write-up was retired (2026-09-22): nothing displayed it, the account read
-- was its only reader, and the per-format cap left seven paragraphs in all.
DROP TABLE creative_insight;

-- The schedule that swept for write-ups is gone with it.
DELETE FROM schedule_state WHERE name = 'insights.nightly_creative_insights';

-- Finished jobs of a type that no longer has a handler.
DELETE FROM job WHERE job_type = 'insights.creative_insight' AND status IN ('done', 'dead');

-- migrate:down
-- The table comes back empty; the dropped paragraphs are not recoverable.
CREATE TABLE creative_insight (
  creative_id UUID PRIMARY KEY REFERENCES creative(id),
  org_id UUID NOT NULL REFERENCES org(id),
  status TEXT NOT NULL CHECK (status IN ('done', 'rejected', 'failed')),
  paragraph TEXT NOT NULL,
  moments JSONB NOT NULL DEFAULT '[]',
  problems JSONB NOT NULL DEFAULT '[]',
  brief TEXT NOT NULL,
  window_start DATE NOT NULL,
  window_end DATE NOT NULL,
  model TEXT NOT NULL,
  prompt_sha256 TEXT NOT NULL,
  input_tokens INT NOT NULL DEFAULT 0,
  output_tokens INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  creative_profile JSONB NOT NULL DEFAULT '{}',
  error TEXT
);
CREATE INDEX creative_insight_org_idx ON creative_insight (org_id);
