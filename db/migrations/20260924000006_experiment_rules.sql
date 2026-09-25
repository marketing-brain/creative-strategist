-- migrate:up
-- The rules that picked an experiment's files, each with who decided it and what it removed,
-- written by whatever builds the file list so the page never describes a rule the data
-- does not follow.
ALTER TABLE experiment ADD COLUMN rules jsonb NOT NULL DEFAULT '[]'::jsonb;

-- migrate:down
ALTER TABLE experiment DROP COLUMN rules;
