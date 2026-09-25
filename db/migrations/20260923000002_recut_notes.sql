-- migrate:up
-- Cache for the model output behind a `/next` recut card, so the nightly job pays for a
-- sentence once rather than every night.
--
-- Two kinds, cached on different keys because they go stale for different reasons:
--   'clip' -- what is literally on screen in one segment of one file. The pixels never
--            change, so this is keyed by the segment and kept indefinitely.
--   'card' -- the card's prose. Keyed by a hash of the *rounded* measurements behind it, so
--            a file whose numbers drift a tenth of a point reuses yesterday's sentence and
--            one that genuinely moved gets rewritten.
--
-- The `model_call` ledger still records every request that is actually sent; this table
-- records what came back, which is what makes the second night free.
CREATE TABLE recut_note (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  kind text NOT NULL CHECK (kind IN ('clip', 'card')),
  cache_key text NOT NULL,
  model text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, cache_key, model)
);
CREATE INDEX recut_note_org_idx ON recut_note (org_id, kind);

-- migrate:down
DROP TABLE recut_note;
