-- migrate:up
-- Open-vocabulary merge (spec 2026-09-16-vocab-merge-design.md).
-- One embedding per canonical slug per model; vectors are compared in Python, so no
-- HNSW index until a dimension passes ~5,000 slugs.
CREATE TABLE tag_vocab_embedding (
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  model TEXT NOT NULL,
  embedding vector(1536) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, dimension, slug, model)
);

-- Every merge decision, applied or not, so runs are idempotent and reversible.
CREATE TABLE tag_vocab_merge (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  dimension TEXT NOT NULL,
  slug TEXT NOT NULL,
  into_slug TEXT NOT NULL,
  similarity REAL NOT NULL,
  reason TEXT,
  status TEXT NOT NULL CHECK (status IN ('applied', 'review', 'rejected', 'reverted')),
  source TEXT NOT NULL CHECK (source IN ('auto', 'manual')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);
CREATE INDEX tag_vocab_merge_org_dim_idx ON tag_vocab_merge (org_id, dimension, status);

-- migrate:down
DROP INDEX tag_vocab_merge_org_dim_idx;
DROP TABLE tag_vocab_merge;
DROP TABLE tag_vocab_embedding;
