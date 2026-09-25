-- migrate:up
-- The first step of clustering: a model's plain-words answer to each axis question for one
-- file, with no labels offered. Discovery groups these; assignment does not read them (it
-- judges each file from its own evidence, so a weak description never compounds).
--
-- Keyed on the prompt version: a changed prompt is a new description, and the old one stays
-- for comparison. A row is written only for a call that answered; the paid attempt itself is
-- in model_call either way.
CREATE TABLE footage_description (
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  prompt_version text NOT NULL,
  model text NOT NULL,
  answers jsonb NOT NULL,
  input_tokens int,
  output_tokens int,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (footage_id, prompt_version)
);

-- migrate:down
DROP TABLE footage_description;
