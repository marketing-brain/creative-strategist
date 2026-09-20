-- migrate:up transaction:false
-- Dropping a column leaves its bytes in every existing row until the row is rewritten.
-- VACUUM FULL rewrites the partitions and hands the space back. It locks the table while it
-- runs (about a second at 32 MB) and cannot run inside a transaction.
VACUUM (FULL, ANALYZE) metric_daily;

-- migrate:down transaction:false
SELECT 1;
