-- migrate:up
-- Clusters: each footage file gets one label per axis (angle, format, duration, ...). The
-- axes are fixed questions the buyer chose; the values on an axis are a codebook, either
-- computed from facts (duration, launch month) or discovered by a model and edited by hand.
--
-- A codebook is versioned so an edited or re-discovered one never rewrites what an earlier
-- comparison was read against; one codebook per org and axis is active at a time.
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

-- One value on the axis. `definition` is what the assigner is told and the page shows; a
-- computed value states its rule ("30 to 60 seconds").
CREATE TABLE cluster (
  id uuid PRIMARY KEY,
  codebook_id uuid NOT NULL REFERENCES cluster_codebook(id) ON DELETE CASCADE,
  key text NOT NULL,
  name text NOT NULL,
  definition text NOT NULL,
  ordinal int NOT NULL,
  UNIQUE (codebook_id, key)
);

-- A file's one value on the axis. `agreement` is the share of assignment runs that chose it
-- (a model axis runs three times and keeps the majority); NULL for a computed axis.
CREATE TABLE footage_cluster (
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  codebook_id uuid NOT NULL REFERENCES cluster_codebook(id) ON DELETE CASCADE,
  cluster_id uuid NOT NULL REFERENCES cluster(id) ON DELETE CASCADE,
  agreement real,
  PRIMARY KEY (footage_id, codebook_id)
);
CREATE INDEX footage_cluster_cluster_idx ON footage_cluster (cluster_id);

-- migrate:down
DROP TABLE footage_cluster;
DROP TABLE cluster;
DROP TABLE cluster_codebook;
