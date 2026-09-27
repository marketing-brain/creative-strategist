-- migrate:up
-- Tests (a batch of variants launched against the original, shown under Tests) versus
-- research (how the analysis itself works, shown only in the admin Lab).
ALTER TABLE experiment ADD COLUMN kind text NOT NULL DEFAULT 'research' CHECK (kind IN ('research', 'test'));
UPDATE experiment SET kind = 'test' WHERE slug IN ('v03-variants', 'v26-hook-edit');

-- migrate:down
ALTER TABLE experiment DROP COLUMN kind;
