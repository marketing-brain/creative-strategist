-- migrate:up
ALTER TABLE creative DROP CONSTRAINT creative_format_check;
ALTER TABLE creative ADD CONSTRAINT creative_format_check
  CHECK (format IN ('image', 'video', 'carousel', 'unresolved'));

ALTER TABLE creative ADD COLUMN format_source TEXT NOT NULL DEFAULT 'legacy_guess';
ALTER TABLE creative ADD COLUMN format_verified_at TIMESTAMPTZ;

ALTER TABLE creative ADD CONSTRAINT creative_format_source_check
  CHECK (format_source IN (
    'meta_object_type', 'asset_feed_spec_video', 'video_view_metric',
    'meta_video_id', 'carousel_attachments', 'apify_media_type',
    'manual_review', 'unresolved', 'legacy_guess'
  ));

ALTER TABLE creative ADD CONSTRAINT creative_unresolved_consistency
  CHECK ((format = 'unresolved') = (format_source = 'unresolved'));

CREATE INDEX creative_format_source_idx ON creative (org_id, format_source);

-- migrate:down
DROP INDEX creative_format_source_idx;
ALTER TABLE creative DROP CONSTRAINT creative_unresolved_consistency;
ALTER TABLE creative DROP CONSTRAINT creative_format_source_check;
ALTER TABLE creative DROP COLUMN format_verified_at;
ALTER TABLE creative DROP COLUMN format_source;
ALTER TABLE creative DROP CONSTRAINT creative_format_check;
ALTER TABLE creative ADD CONSTRAINT creative_format_check
  CHECK (format IN ('image', 'video', 'carousel'));
