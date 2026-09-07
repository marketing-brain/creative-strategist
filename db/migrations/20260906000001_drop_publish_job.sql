-- migrate:up
DROP TABLE publish_job;

-- migrate:down
CREATE TABLE publish_job (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  generated_asset_id UUID NOT NULL REFERENCES generated_asset(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  campaign_id TEXT NOT NULL,
  adset_id TEXT NOT NULL,
  ad_status_requested TEXT NOT NULL CHECK (ad_status_requested IN ('PAUSED', 'ACTIVE')),
  form JSONB NOT NULL,
  platform_creative_id TEXT,
  platform_ad_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE INDEX publish_job_platform_creative_idx ON publish_job (org_id, platform_creative_id);
