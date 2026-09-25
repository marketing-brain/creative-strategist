-- migrate:up
-- One label per file per axis in an experiment (format, content, funnel stage...), with the
-- model's one-line reason so a reviewer can see why it chose it.
CREATE TABLE experiment_label (
  experiment_id uuid NOT NULL REFERENCES experiment(id) ON DELETE CASCADE,
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  axis text NOT NULL,
  value text NOT NULL,
  reason text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (experiment_id, footage_id, axis)
);

-- migrate:down
DROP TABLE experiment_label;
