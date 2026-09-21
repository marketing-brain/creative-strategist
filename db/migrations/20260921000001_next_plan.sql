-- migrate:up
-- The `/next` plan: the account's standing answer to "what should I run next", assembled by
-- `insights.next_plan` from the latest account-read run plus the 30-day rollups. Stored, not
-- computed per request: the allocation block runs `diagnose_creative` per footage group, which
-- is several queries each, and fanning that across live ads on a page load is the shape that
-- pinned the instance twice (2026-09-18, 2026-09-20).
--
-- `plan` is the whole payload and it is small by construction -- a dozen allocation rows, five
-- briefs, the explore list -- so unlike `account_read_run.result` it is safe to read whole.
CREATE TABLE next_plan_run (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL REFERENCES ad_account(id),
  format text NOT NULL DEFAULT 'video',
  account_read_run_id uuid REFERENCES account_read_run(id) ON DELETE SET NULL,
  status text NOT NULL CHECK (status IN ('done','insufficient','failed')),
  plan jsonb NOT NULL,
  error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX next_plan_run_latest_idx ON next_plan_run (ad_account_id, format, created_at DESC);

-- The ledger. Phase 1 writes and upserts rows so every recommendation has a stable identity
-- from the first run it appears in; phase 2 closes them by matching new footage against a
-- `make` item's attributes. `key` is a hash of the kind plus the item's sorted attribute spec
-- (next_plan.types.item_key), so the same brief keeps its row across runs even as its stake,
-- its evidence and its rank move.
CREATE TABLE next_item (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL REFERENCES ad_account(id),
  key text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('scale','fix','replace','make','explore')),
  spec jsonb NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','ran','expired')),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  ran_at timestamptz,
  ran_creative_ids uuid[] NOT NULL DEFAULT '{}',
  UNIQUE (ad_account_id, key)
);
CREATE INDEX next_item_open_idx ON next_item (ad_account_id, status, kind);

-- migrate:down
DROP TABLE next_item;
DROP TABLE next_plan_run;
