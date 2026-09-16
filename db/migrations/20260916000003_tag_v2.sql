-- migrate:up
-- Tag taxonomy v2: one tag row can now be one item of a set (multi), one step of an
-- ordered sequence (ordinal), an open-vocabulary slug (raw_value keeps what the model
-- said before canonicalization), a computed numeric, or a text field. Legacy rows are
-- 'single' with the other columns NULL.
ALTER TABLE tag
  ADD COLUMN kind TEXT NOT NULL DEFAULT 'single',
  ADD COLUMN t_start_s REAL,
  ADD COLUMN ordinal INT,
  ADD COLUMN raw_value TEXT;

-- Per-org canonical vocabulary for open dimensions. A slug's canonical_slug is itself
-- until someone merges it; count is bumped for the canonical row on each finished run.
CREATE TABLE tag_vocab (
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  canonical_slug TEXT NOT NULL,
  count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, dimension, slug)
);
CREATE INDEX tag_vocab_count_idx ON tag_vocab (org_id, dimension, count DESC);

-- migrate:down
DROP INDEX tag_vocab_count_idx;
DROP TABLE tag_vocab;
ALTER TABLE tag
  DROP COLUMN raw_value,
  DROP COLUMN ordinal,
  DROP COLUMN t_start_s,
  DROP COLUMN kind;
