-- migrate:up
CREATE TABLE account_read_run (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL REFERENCES ad_account(id),
  format text NOT NULL DEFAULT 'video',
  window_start date,
  window_end date,
  n_units int NOT NULL,
  status text NOT NULL CHECK (status IN ('done','insufficient','rejected','failed')),
  result jsonb NOT NULL,
  read jsonb,
  error text,
  input_tokens int NOT NULL DEFAULT 0,
  output_tokens int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX account_read_run_latest_idx ON account_read_run (ad_account_id, format, created_at DESC);

-- migrate:down
DROP TABLE account_read_run;
