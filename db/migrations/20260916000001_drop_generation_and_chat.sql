-- migrate:up
-- Generation, Copilot chat, batch tagging and creative-similarity are out of scope: every
-- one of these tables was empty (checked on the primary DB, 2026-09-15) and no code reads
-- them any more. Their features were removed in the same change.
-- org.active_brand_kit_id is the last reference to brand_kit (no code reads it).
ALTER TABLE org DROP CONSTRAINT IF EXISTS org_active_brand_kit_fk;
ALTER TABLE org DROP COLUMN IF EXISTS active_brand_kit_id;
DROP TABLE IF EXISTS generated_asset;
DROP TABLE IF EXISTS generation;
DROP TABLE IF EXISTS brand_kit;
DROP TABLE IF EXISTS copilot_message;
DROP TABLE IF EXISTS copilot_conversation;
DROP TABLE IF EXISTS creative_embedding;
DROP TABLE IF EXISTS tag_batch_item;
DROP TABLE IF EXISTS tag_batch;
DROP TABLE IF EXISTS tag_batch_provider_result;

-- migrate:down
-- Not reversible: these tables were dropped with their (empty) data and their features.
-- Restore from the migrations that created them if the features come back.
