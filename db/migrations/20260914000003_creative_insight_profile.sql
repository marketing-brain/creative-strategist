-- migrate:up
-- The strategist prompt now also returns a compact creative_profile (concept, persuasion,
-- cast, visual style, editing, sound, speech/text, structure) described without performance
-- judgments, kept for the later cross-creative comparison. Rows written before this column
-- existed keep an empty object.
ALTER TABLE creative_insight ADD COLUMN creative_profile JSONB NOT NULL DEFAULT '{}';
-- migrate:down
ALTER TABLE creative_insight DROP COLUMN creative_profile;
