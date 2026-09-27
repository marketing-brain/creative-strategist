-- migrate:up
-- A batch: the variants of one video picked to launch together against the original, and the
-- test it became. One drafting batch per video at a time; a launched batch keeps its test.
CREATE TABLE make_batch (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL,
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  status text NOT NULL CHECK (status IN ('drafting', 'launched')),
  experiment_id uuid REFERENCES experiment(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  launched_at timestamptz
);
CREATE UNIQUE INDEX make_batch_one_draft ON make_batch (footage_id) WHERE status = 'drafting';

-- A decision on one idea: in the batch, or skipped. `ref` names the idea:
-- 'brief:<brief_id>:<code>' for a brief variant, 'gen:<generation_id>' for a generated opening.
CREATE TABLE make_batch_item (
  batch_id uuid NOT NULL REFERENCES make_batch(id) ON DELETE CASCADE,
  ref text NOT NULL,
  decision text NOT NULL CHECK (decision IN ('in', 'skipped')),
  decided_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (batch_id, ref)
);

-- migrate:down
DROP TABLE make_batch_item;
DROP TABLE make_batch;
