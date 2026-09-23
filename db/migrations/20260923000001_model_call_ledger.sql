-- migrate:up
-- Every request to a paid model, claimed before it is sent.
--
-- The account read spent the night proving why. Its writer call sends a 23k-token brief and
-- asks for a long structured answer; the gateway disconnects mid-request after about five
-- and a half minutes (RemoteProtocolError, "Server disconnected without sending a response").
-- The OpenAI SDK retried underneath us, the job retried above us, and each layer paid again
-- for a generation the model may well have produced. Nothing recorded any of it, so the only
-- evidence that money had gone was the gateway's own budget running out.
--
-- A row is written before the request leaves, so a call that dies without answering is still
-- on the books. `purpose` and `key` say what was being paid for (one draft of one run), and
-- the unique index makes a second attempt at the same thing a decision, never an accident.
CREATE TABLE model_call (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  purpose text NOT NULL,
  key text NOT NULL,
  model text NOT NULL,
  attempt int NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'started' CHECK (status IN ('started', 'done', 'failed')),
  input_tokens int,
  output_tokens int,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  UNIQUE (purpose, key, attempt)
);
CREATE INDEX model_call_org_idx ON model_call (org_id, purpose, created_at DESC);

-- migrate:down
DROP TABLE model_call;
