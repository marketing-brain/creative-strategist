-- migrate:up
CREATE TABLE schedule_state (
  name TEXT PRIMARY KEY,
  last_run_at TIMESTAMPTZ NOT NULL
);

-- migrate:down
DROP TABLE schedule_state;
