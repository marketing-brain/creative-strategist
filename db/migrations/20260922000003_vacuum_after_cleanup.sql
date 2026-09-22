-- migrate:up transaction:false
-- Deleted rows keep their space until the table is rewritten; account_read_run held ~60 MB.
-- One statement: dbmate sends the file as a single query, and several statements in one
-- query run as an implicit transaction, which VACUUM refuses.
VACUUM (FULL, ANALYZE) account_read_run, insight, job;

-- migrate:down transaction:false
SELECT 1;
