-- migrate:up
ALTER TABLE creative DROP CONSTRAINT creative_format_source_check;
ALTER TABLE creative ADD CONSTRAINT creative_format_source_check
  CHECK (format_source IN (
    'meta_object_type', 'asset_feed_spec_video', 'video_view_metric',
    'meta_video_id', 'carousel_attachments', 'apify_media_type', 'no_video_views',
    'manual_review', 'unresolved', 'legacy_guess'
  ));

-- Safe only before any row uses format_source='no_video_views'; after that it fails atomically.
-- migrate:down
ALTER TABLE creative DROP CONSTRAINT creative_format_source_check;
ALTER TABLE creative ADD CONSTRAINT creative_format_source_check
  CHECK (format_source IN (
    'meta_object_type', 'asset_feed_spec_video', 'video_view_metric',
    'meta_video_id', 'carousel_attachments', 'apify_media_type',
    'manual_review', 'unresolved', 'legacy_guess'
  ));
