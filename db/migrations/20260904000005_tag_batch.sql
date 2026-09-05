-- migrate:up
CREATE TABLE tag_batch (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  provider_batch_id TEXT NOT NULL,
  taxonomy_version_id UUID NOT NULL REFERENCES taxonomy_version(id),
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('submitted', 'ended')),
  request_count INT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ
);
CREATE TABLE tag_batch_item (
  tag_batch_id UUID NOT NULL REFERENCES tag_batch(id),
  custom_id TEXT NOT NULL,
  tag_run_id UUID NOT NULL REFERENCES tag_run(id),
  PRIMARY KEY (tag_batch_id, custom_id)
);
-- migrate:down
DROP TABLE tag_batch_item;
DROP TABLE tag_batch;
