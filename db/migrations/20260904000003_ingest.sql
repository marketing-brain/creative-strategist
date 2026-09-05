-- migrate:up
CREATE TABLE ad_account (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  platform TEXT NOT NULL CHECK (platform IN ('meta')),
  platform_account_id TEXT NOT NULL,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  access_token_enc TEXT NOT NULL,
  token_expires_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'disconnected')),
  sync_cursor JSONB NOT NULL DEFAULT '{"schema_version": 1}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, platform, platform_account_id)
);

CREATE TABLE creative (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  platform_creative_id TEXT NOT NULL,
  format TEXT NOT NULL CHECK (format IN ('image', 'video', 'carousel')),
  headline TEXT,
  primary_text TEXT,
  description TEXT,
  cta_type TEXT,
  link_url TEXT,
  primary_asset_id UUID,
  generated_asset_id UUID,
  raw_path TEXT NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  schema_version INT NOT NULL DEFAULT 1,
  UNIQUE (ad_account_id, platform_creative_id)
);
CREATE INDEX creative_org_idx ON creative (org_id, ad_account_id);

CREATE TABLE ad (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  ad_account_id UUID NOT NULL REFERENCES ad_account(id),
  platform_ad_id TEXT NOT NULL,
  creative_id UUID NOT NULL REFERENCES creative(id),
  adset_id TEXT,
  campaign_id TEXT,
  name TEXT,
  status TEXT,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (ad_account_id, platform_ad_id)
);

CREATE TABLE asset (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  creative_id UUID NOT NULL REFERENCES creative(id),
  kind TEXT NOT NULL CHECK (kind IN ('original_image','original_video','keyframe','audio','transcript','thumbnail')),
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  mime TEXT NOT NULL,
  width INT,
  height INT,
  duration_ms INT,
  seq INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX asset_creative_idx ON asset (creative_id, kind, seq);
ALTER TABLE creative ADD CONSTRAINT creative_primary_asset_fk FOREIGN KEY (primary_asset_id) REFERENCES asset(id);

CREATE TABLE metric_daily (
  org_id UUID NOT NULL,
  ad_account_id UUID NOT NULL,
  creative_id UUID NOT NULL,
  ad_id UUID NOT NULL,
  day DATE NOT NULL,
  placement TEXT NOT NULL,
  spend NUMERIC(14,4) NOT NULL DEFAULT 0,
  impressions BIGINT NOT NULL DEFAULT 0,
  clicks BIGINT NOT NULL DEFAULT 0,
  link_clicks BIGINT NOT NULL DEFAULT 0,
  conversions NUMERIC(14,4) NOT NULL DEFAULT 0,
  conversion_value NUMERIC(14,4) NOT NULL DEFAULT 0,
  video_3s_views BIGINT NOT NULL DEFAULT 0,
  video_thruplay BIGINT NOT NULL DEFAULT 0,
  video_p50 BIGINT NOT NULL DEFAULT 0,
  video_p100 BIGINT NOT NULL DEFAULT 0,
  raw JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (creative_id, ad_id, day, placement)
) PARTITION BY RANGE (day);
CREATE INDEX metric_daily_org_day_idx ON metric_daily (org_id, ad_account_id, day);

-- migrate:down
ALTER TABLE creative DROP CONSTRAINT creative_primary_asset_fk;
DROP TABLE metric_daily;
DROP TABLE asset;
DROP TABLE ad;
DROP TABLE creative;
DROP TABLE ad_account;
