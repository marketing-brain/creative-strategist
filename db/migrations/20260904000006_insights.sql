-- migrate:up
CREATE VIEW creative_latest_tag AS
SELECT t.org_id, t.creative_id, t.dimension, t.value, t.confidence
FROM tag t
JOIN (
  SELECT DISTINCT ON (creative_id) id, creative_id
  FROM tag_run WHERE status = 'done'
  ORDER BY creative_id, finished_at DESC
) r ON r.id = t.tag_run_id;

CREATE TABLE insight (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  ad_account_id UUID REFERENCES ad_account(id),
  kind TEXT NOT NULL CHECK (kind IN ('pattern', 'fatigue')),
  dimension TEXT NOT NULL,
  value TEXT NOT NULL,
  metric TEXT NOT NULL CHECK (metric IN ('ctr', 'cpa', 'roas')),
  creative_id UUID REFERENCES creative(id),
  window_start DATE NOT NULL,
  window_end DATE NOT NULL,
  sample_creatives INT NOT NULL,
  sample_spend NUMERIC(14,4) NOT NULL,
  baseline DOUBLE PRECISION NOT NULL,
  observed DOUBLE PRECISION NOT NULL,
  lift DOUBLE PRECISION NOT NULL,
  explanation TEXT NOT NULL,
  suggested_test TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX insight_dedup_idx ON insight
  (org_id, kind, dimension, value, metric, window_start, window_end, COALESCE(creative_id, '00000000-0000-0000-0000-000000000000'::uuid));
CREATE INDEX insight_org_kind_idx ON insight (org_id, kind, created_at DESC);

CREATE TABLE brief (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  week_start DATE NOT NULL,
  content_md TEXT NOT NULL,
  insight_ids UUID[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, week_start)
);

CREATE TABLE copilot_conversation (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  user_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT 'New conversation',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE copilot_message (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  conversation_id UUID NOT NULL REFERENCES copilot_conversation(id),
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX copilot_message_conv_idx ON copilot_message (conversation_id, created_at);

CREATE UNIQUE INDEX asset_creative_kind_seq_uidx ON asset (creative_id, kind, seq);

-- migrate:down
DROP INDEX asset_creative_kind_seq_uidx;
DROP TABLE copilot_message;
DROP TABLE copilot_conversation;
DROP TABLE brief;
DROP TABLE insight;
DROP VIEW creative_latest_tag;
