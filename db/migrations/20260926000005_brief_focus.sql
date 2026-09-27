-- migrate:up
-- The What works finding a brief was written to apply or test (title, body, strength,
-- cluster), so its variants can say which finding they test. NULL for a general brief.
ALTER TABLE creative_brief ADD COLUMN focus jsonb;

-- migrate:down
ALTER TABLE creative_brief DROP COLUMN focus;
