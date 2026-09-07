-- migrate:up
CREATE TABLE meta_connection (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL UNIQUE REFERENCES org(id),
  fivetran_connector_id TEXT NOT NULL,
  schema_name TEXT NOT NULL,
  sync_status TEXT NOT NULL DEFAULT 'pending' CHECK (sync_status IN ('pending', 'syncing', 'ready', 'error')),
  last_synced_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE ad_account
  DROP COLUMN access_token_enc,
  DROP COLUMN token_expires_at,
  ADD COLUMN meta_connection_id UUID REFERENCES meta_connection(id);

-- migrate:down
ALTER TABLE ad_account
  DROP COLUMN meta_connection_id,
  ADD COLUMN access_token_enc TEXT NOT NULL DEFAULT '',
  ADD COLUMN token_expires_at TIMESTAMPTZ;

DROP TABLE meta_connection;
