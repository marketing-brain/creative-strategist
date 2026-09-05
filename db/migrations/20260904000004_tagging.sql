-- migrate:up
CREATE TABLE taxonomy_version (
  id UUID PRIMARY KEY,
  version TEXT NOT NULL,
  yaml_sha256 TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tag_run (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  creative_id UUID NOT NULL REFERENCES creative(id),
  taxonomy_version_id UUID NOT NULL REFERENCES taxonomy_version(id),
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','running','done','failed')),
  prompt_sha256 TEXT,
  input_tokens INT,
  output_tokens INT,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE (creative_id, taxonomy_version_id, model)
);

CREATE TABLE tag (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  tag_run_id UUID NOT NULL REFERENCES tag_run(id),
  creative_id UUID NOT NULL REFERENCES creative(id),
  dimension TEXT NOT NULL,
  value TEXT NOT NULL,
  confidence REAL NOT NULL,
  evidence TEXT
);
CREATE INDEX tag_org_dim_val_idx ON tag (org_id, dimension, value);
CREATE INDEX tag_creative_idx ON tag (creative_id, tag_run_id);

CREATE TABLE creative_embedding (
  creative_id UUID PRIMARY KEY REFERENCES creative(id),
  org_id UUID NOT NULL REFERENCES org(id),
  tag_run_id UUID NOT NULL REFERENCES tag_run(id),
  embedding vector(1536) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX creative_embedding_hnsw ON creative_embedding USING hnsw (embedding vector_cosine_ops);

-- migrate:down
DROP TABLE creative_embedding;
DROP TABLE tag;
DROP TABLE tag_run;
DROP TABLE taxonomy_version;
