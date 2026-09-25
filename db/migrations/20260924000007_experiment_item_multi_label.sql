-- migrate:up
-- A file can carry several labels: one per TESTING ad set it ran in, as the buyer set them
-- up. Scoring counts the file once for each.
ALTER TABLE experiment_item DROP CONSTRAINT experiment_item_pkey;
ALTER TABLE experiment_item ADD PRIMARY KEY (experiment_id, footage_id, label);

-- migrate:down
DELETE FROM experiment_item a USING experiment_item b
  WHERE a.experiment_id = b.experiment_id AND a.footage_id = b.footage_id AND a.label > b.label;
ALTER TABLE experiment_item DROP CONSTRAINT experiment_item_pkey;
ALTER TABLE experiment_item ADD PRIMARY KEY (experiment_id, footage_id);
