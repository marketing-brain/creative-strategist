-- migrate:up
-- format is what analysis uses: image/video only once the file is stored. evidence_format
-- is what the platform data proves, file or not, so the UI can say "video, no file"
-- instead of presenting a proven video as an unknown.
ALTER TABLE creative ADD COLUMN evidence_format TEXT, ADD COLUMN evidence_source TEXT;

UPDATE creative SET evidence_format = format, evidence_source = format_source;

-- A creative still waiting on its file has its proven verdict in the latest job that
-- carries it; the next sync rewrites every row from fresh evidence anyway.
UPDATE creative c
SET evidence_format = j.payload->>'format', evidence_source = j.payload->>'format_source'
FROM (
  SELECT DISTINCT ON (payload->>'creative_id') payload->>'creative_id' AS creative_id, payload
  FROM job
  WHERE job_type IN ('meta.download_creative', 'meta.resolve_instagram_video',
                     'meta.resolve_facebook_video')
    AND payload ? 'format' AND payload ? 'format_source'
  ORDER BY payload->>'creative_id', created_at DESC
) j
WHERE c.format = 'unresolved' AND c.id::text = j.creative_id
  AND j.payload->>'format' IN ('image', 'video', 'carousel');

ALTER TABLE creative ALTER COLUMN evidence_format SET NOT NULL;
ALTER TABLE creative ALTER COLUMN evidence_source SET NOT NULL;

ALTER TABLE creative ADD CONSTRAINT creative_evidence_format_check
  CHECK (evidence_format IN ('image', 'video', 'carousel', 'unresolved'));
ALTER TABLE creative ADD CONSTRAINT creative_evidence_source_check
  CHECK (evidence_source IN (
    'meta_object_type', 'asset_feed_spec_video', 'video_view_metric',
    'meta_video_id', 'carousel_attachments', 'apify_media_type', 'no_video_views',
    'asset_feed_spec_image', 'image_url', 'video_full_watch',
    'manual_review', 'unresolved', 'legacy_guess'
  ));
ALTER TABLE creative ADD CONSTRAINT creative_evidence_unresolved_consistency
  CHECK ((evidence_format = 'unresolved') = (evidence_source = 'unresolved'));

-- migrate:down
ALTER TABLE creative DROP CONSTRAINT creative_evidence_unresolved_consistency;
ALTER TABLE creative DROP CONSTRAINT creative_evidence_source_check;
ALTER TABLE creative DROP CONSTRAINT creative_evidence_format_check;
ALTER TABLE creative DROP COLUMN evidence_source, DROP COLUMN evidence_format;
