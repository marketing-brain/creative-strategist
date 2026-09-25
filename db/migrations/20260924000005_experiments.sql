-- migrate:up
-- Experiments: a question tested on a fixed set of files, one visible step at a time, so the
-- buyer can watch each step run and decide on the next. The first one asks whether blind
-- discovery rediscovers the concepts the buyer already sorted into TESTING ad sets.
CREATE TABLE experiment (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  slug text NOT NULL,
  title text NOT NULL,
  question text NOT NULL,
  method text NOT NULL,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'done', 'abandoned')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, slug)
);

-- The steps in order. `waiting_on` says in words what the next move needs, from whom; a
-- step's numbers (progress, cost) are written by whatever runs it, as it runs.
CREATE TABLE experiment_step (
  experiment_id uuid NOT NULL REFERENCES experiment(id) ON DELETE CASCADE,
  ordinal int NOT NULL,
  key text NOT NULL,
  title text NOT NULL,
  detail text NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'waiting', 'running', 'done', 'failed')),
  waiting_on text,
  progress_done int NOT NULL DEFAULT 0,
  progress_total int NOT NULL DEFAULT 0,
  cost_usd numeric(10,4) NOT NULL DEFAULT 0,
  result_note text,
  started_at timestamptz,
  finished_at timestamptz,
  PRIMARY KEY (experiment_id, ordinal),
  UNIQUE (experiment_id, key)
);

-- The files the experiment runs on, each with the label it is judged against (here, the
-- concept family its ad sets name) and where that label came from.
CREATE TABLE experiment_item (
  experiment_id uuid NOT NULL REFERENCES experiment(id) ON DELETE CASCADE,
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  label text NOT NULL,
  label_source text,
  PRIMARY KEY (experiment_id, footage_id)
);

-- What discovery found: groups and their members, per run so settings can be compared.
CREATE TABLE experiment_group (
  id uuid PRIMARY KEY,
  experiment_id uuid NOT NULL REFERENCES experiment(id) ON DELETE CASCADE,
  run text NOT NULL,
  ordinal int NOT NULL,
  name text NOT NULL,
  definition text,
  UNIQUE (experiment_id, run, ordinal)
);
CREATE TABLE experiment_member (
  group_id uuid NOT NULL REFERENCES experiment_group(id) ON DELETE CASCADE,
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, footage_id)
);

-- migrate:down
DROP TABLE experiment_member;
DROP TABLE experiment_group;
DROP TABLE experiment_item;
DROP TABLE experiment_step;
DROP TABLE experiment;
