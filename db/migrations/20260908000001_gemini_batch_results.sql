-- migrate:up
-- Hand-off store between GeminiGatewayBatchTagger.submit() and .poll(): unlike
-- Anthropic's Message Batches API, AI Gateway has no async batch endpoint for
-- Gemini, so submit() runs every request concurrently right away and persists
-- the full results list here under a fresh id; poll() (a separate job, on a
-- separate BatchTagger instance) reads it back by that id.
CREATE TABLE tag_batch_provider_result (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  results JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- migrate:down
DROP TABLE tag_batch_provider_result;
