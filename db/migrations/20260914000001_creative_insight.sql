-- migrate:up
-- One free-text insight per creative: the model watches the ad against its diagnosis
-- brief and writes a paragraph. Written once (creative_id is the key). 'rejected' keeps a
-- draft that cited numbers or timestamps absent from the brief, for inspection only; it is
-- never shown and never retried automatically.
CREATE TABLE creative_insight (
  creative_id UUID PRIMARY KEY REFERENCES creative(id),
  org_id UUID NOT NULL REFERENCES org(id),
  status TEXT NOT NULL CHECK (status IN ('done', 'rejected')),
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
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX creative_insight_org_idx ON creative_insight (org_id);
-- migrate:down
DROP TABLE creative_insight;
