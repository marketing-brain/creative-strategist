-- migrate:up
-- A computed result an experiment step stores for its page to read (e.g. the Insights tab's
-- findings re-checked within clusters), when computing it on every page load is too slow.
CREATE TABLE experiment_result (
  experiment_id uuid NOT NULL REFERENCES experiment(id) ON DELETE CASCADE,
  key text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (experiment_id, key)
);

-- migrate:down
DROP TABLE experiment_result;
