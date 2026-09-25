-- migrate:up
-- The ledger read-back (docs/next-surface.md §5 stage 7). v1 wrote `next_item` on every run
-- and nothing ever read it, so a recommendation had no history and no outcome.
--
-- Two changes. `recut` joins the kinds, and `baseline` freezes the measurement the card was
-- issued on. The baseline has to be stored rather than recomputed: `play_curve_point` is
-- rewritten in place by every footage refresh, so by the time anyone asks whether a file
-- improved, the number it was judged against is already gone.
ALTER TABLE next_item DROP CONSTRAINT next_item_kind_check;
ALTER TABLE next_item ADD CONSTRAINT next_item_kind_check
  CHECK (kind IN ('scale', 'fix', 'replace', 'make', 'explore', 'recut'));

-- Set once, on insert, and never updated: that is the whole point of it.
ALTER TABLE next_item ADD COLUMN baseline jsonb;

-- migrate:down
ALTER TABLE next_item DROP COLUMN baseline;
ALTER TABLE next_item DROP CONSTRAINT next_item_kind_check;
ALTER TABLE next_item ADD CONSTRAINT next_item_kind_check
  CHECK (kind IN ('scale', 'fix', 'replace', 'make', 'explore'));
