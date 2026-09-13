-- migrate:up
ALTER TABLE creative DROP CONSTRAINT creative_format_source_check;
ALTER TABLE creative ADD CONSTRAINT creative_format_source_check
  CHECK (format_source IN (
    'meta_object_type', 'asset_feed_spec_video', 'video_view_metric',
    'meta_video_id', 'carousel_attachments', 'apify_media_type', 'no_video_views',
    'asset_feed_spec_image', 'image_url', 'video_full_watch',
    'manual_review', 'unresolved', 'legacy_guess'
  ));

-- Safe only before any row uses one of the three new sources; after that it fails atomically.
-- migrate:down
ALTER TABLE creative DROP CONSTRAINT creative_format_source_check;
ALTER TABLE creative ADD CONSTRAINT creative_format_source_check
  CHECK (format_source IN (
    'meta_object_type', 'asset_feed_spec_video', 'video_view_metric',
    'meta_video_id', 'carousel_attachments', 'apify_media_type', 'no_video_views',
    'manual_review', 'unresolved', 'legacy_guess'
  ));
