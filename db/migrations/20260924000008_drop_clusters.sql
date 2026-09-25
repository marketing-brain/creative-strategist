-- migrate:up
-- The Clusters tab was removed (2026-09-24). Its tables held only duration and launch-month
-- groupings computed from footage, so nothing is lost that the footage tables can't rebuild.
DROP TABLE footage_cluster;
DROP TABLE cluster;
DROP TABLE cluster_codebook;

-- migrate:down
CREATE TABLE cluster_codebook (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  axis text NOT NULL,
  version int NOT NULL,
  source text NOT NULL CHECK (source IN ('computed', 'llm')),
  status text NOT NULL CHECK (status IN ('draft', 'active', 'retired')),
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  approved_at timestamptz,
  UNIQUE (org_id, axis, version)
);
CREATE UNIQUE INDEX cluster_codebook_active_idx ON cluster_codebook (org_id, axis)
  WHERE status = 'active';
CREATE TABLE cluster (
  id uuid PRIMARY KEY,
  codebook_id uuid NOT NULL REFERENCES cluster_codebook(id) ON DELETE CASCADE,
  key text NOT NULL,
  name text NOT NULL,
  definition text NOT NULL,
  ordinal int NOT NULL,
  UNIQUE (codebook_id, key)
);
CREATE TABLE footage_cluster (
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  codebook_id uuid NOT NULL REFERENCES cluster_codebook(id) ON DELETE CASCADE,
  cluster_id uuid NOT NULL REFERENCES cluster(id) ON DELETE CASCADE,
  agreement real,
  PRIMARY KEY (footage_id, codebook_id)
);
CREATE INDEX footage_cluster_cluster_idx ON footage_cluster (cluster_id);
