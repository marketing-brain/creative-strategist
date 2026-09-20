-- migrate:up
-- `result` is several megabytes per run (every graded test). The Insights page draws a few
-- dozen of them, and reading the whole value on each page load is what pinned the database
-- (2026-09-18, 2026-09-20). `page` holds only what the page draws, written with the run.
-- Runs from before this column are filled in on first read (account_read.repo.latest_run).
ALTER TABLE account_read_run ADD COLUMN page jsonb;

-- migrate:down
ALTER TABLE account_read_run DROP COLUMN page;
