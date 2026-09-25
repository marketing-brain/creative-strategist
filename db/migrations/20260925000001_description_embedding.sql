-- migrate:up
-- One embedding per description answer, so grouping can be re-run with other settings without
-- paying again. Keyed on the description's prompt version and the answer's field.
CREATE TABLE footage_description_embedding (
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  prompt_version text NOT NULL,
  field text NOT NULL,
  model text NOT NULL,
  vector real[] NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (footage_id, prompt_version, field, model)
);

-- migrate:down
DROP TABLE footage_description_embedding;
