-- migrate:up
CREATE TABLE brand_kit (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  version INT NOT NULL,
  colors JSONB NOT NULL DEFAULT '[]'::jsonb,
  fonts JSONB NOT NULL DEFAULT '[]'::jsonb,
  logo_path TEXT,
  logo_sha256 TEXT,
  voice_rules TEXT NOT NULL DEFAULT '',
  prohibited_claims TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, version)
);
ALTER TABLE org ADD CONSTRAINT org_active_brand_kit_fk FOREIGN KEY (active_brand_kit_id) REFERENCES brand_kit(id);

CREATE TABLE generation (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  brand_kit_id UUID NOT NULL REFERENCES brand_kit(id),
  source_insight_id UUID REFERENCES insight(id),
  source_creative_ids UUID[] NOT NULL DEFAULT '{}',
  concept TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('static', 'video')),
  variants INT NOT NULL DEFAULT 4,
  model TEXT,
  prompt TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
  error TEXT,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE TABLE generated_asset (
  id UUID PRIMARY KEY,
  generation_id UUID NOT NULL REFERENCES generation(id),
  org_id UUID NOT NULL REFERENCES org(id),
  kind TEXT NOT NULL CHECK (kind IN ('image', 'video')),
  aspect TEXT NOT NULL CHECK (aspect IN ('1:1', '4:5', '9:16')),
  variant INT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  headline TEXT,
  primary_text TEXT,
  cta_type TEXT,
  review_state TEXT NOT NULL DEFAULT 'draft' CHECK (review_state IN ('draft', 'approved', 'rejected')),
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX generated_asset_review_idx ON generated_asset (org_id, review_state, created_at DESC);

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

-- migrate:down
DROP TABLE publish_job;
DROP TABLE generated_asset;
DROP TABLE generation;
ALTER TABLE org DROP CONSTRAINT org_active_brand_kit_fk;
DROP TABLE brand_kit;
