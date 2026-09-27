-- migrate:up
-- A new video made from a cluster's new-creator script: a shot plan, a generated voice, one
-- generated clip per shot (the product passed as a reference image, never a frame of an ad
-- that ran), assembled with captions, then checked against every video of its kind.
-- Each paid step is ledgered in model_call and never re-sent; a clip whose start went through
-- keeps its operation id here so a resumed job only asks for its status.
CREATE TABLE script_video (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL,
  cluster text NOT NULL,
  script jsonb NOT NULL,
  video_model text NOT NULL,
  status text NOT NULL CHECK (status IN ('queued', 'running', 'blocked', 'done', 'failed')),
  stage text,
  plan jsonb,
  voice jsonb,
  product jsonb,
  clips jsonb NOT NULL DEFAULT '[]',
  path text,
  checks jsonb,
  estimated_cost_usd numeric(8, 2),
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX script_video_cluster_idx ON script_video (org_id, cluster, created_at DESC);

-- migrate:down
DROP TABLE script_video;
