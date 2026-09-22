-- migrate:up
-- Durable tables for the thing the analysis is about: one video (or image) file in one ad
-- account, its tags, its scenes, its metrics per window and its retention curve.
--
-- Today every account-read run recomputes all of this in Python and stores it inside a 5 MB
-- `result` blob, so nothing can be queried across runs, a test has to carry its own list of
-- member files, and a page load needs a second copy of the payload to stay off the blob.
-- These tables hold each fact once; a run's output then references them.
--
-- Nothing reads them yet: `scripts/backfill_footage.py` fills them, the new analysis engine
-- reads them. They are a materialised view of `creative`/`asset`/`tag`/`metric_daily` and the
-- org's Fivetran schema, and the backfill is idempotent, so they can always be rebuilt.

-- One file in one ad account, the same grouping as cs.core.repo.footage: an unresolved
-- creative has no proven format and a carousel's first card says nothing about the rest, so
-- neither groups and neither belongs here.
CREATE TABLE footage (
  id uuid PRIMARY KEY,
  org_id uuid NOT NULL REFERENCES org(id),
  ad_account_id uuid NOT NULL REFERENCES ad_account(id),
  format text NOT NULL CHECK (format IN ('image', 'video')),
  sha256 text NOT NULL,
  duration_s numeric(9,3),
  n_creatives int NOT NULL DEFAULT 0,
  -- The copy that stands for the group: highest spend, ties broken on id, the same rule
  -- tagging.repo.footage_latest_tags uses so both pick the same tags.
  rep_creative_id uuid REFERENCES creative(id) ON DELETE SET NULL,
  first_day date,
  last_day date,
  refreshed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (ad_account_id, format, sha256)
);
CREATE INDEX footage_account_idx ON footage (org_id, ad_account_id, format);

-- Meta mints a new creative whenever an ad is copied, so a file has many. A creative runs
-- exactly one file, which is what the primary key says.
CREATE TABLE footage_creative (
  creative_id uuid PRIMARY KEY REFERENCES creative(id) ON DELETE CASCADE,
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE
);
CREATE INDEX footage_creative_footage_idx ON footage_creative (footage_id);

-- Metrics pooled over the file's copies. 'lifetime' is every day the file ever ran;
-- 'first_7d' is its first seven days, so files launched months apart are compared at the
-- same age instead of by how long the optimiser kept paying for them; 'last_30d' is the
-- trailing window the pages already use.
CREATE TABLE footage_metric_window (
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  window_kind text NOT NULL CHECK (window_kind IN ('lifetime', 'first_7d', 'last_30d')),
  day_from date NOT NULL,
  day_to date NOT NULL,
  spend numeric(14,4) NOT NULL DEFAULT 0,
  impressions bigint NOT NULL DEFAULT 0,
  plays bigint NOT NULL DEFAULT 0,
  views_3s bigint NOT NULL DEFAULT 0,
  thruplays bigint NOT NULL DEFAULT 0,
  p25 bigint NOT NULL DEFAULT 0,
  p50 bigint NOT NULL DEFAULT 0,
  p75 bigint NOT NULL DEFAULT 0,
  p100 bigint NOT NULL DEFAULT 0,
  watch_seconds numeric(14,2) NOT NULL DEFAULT 0,
  link_clicks bigint NOT NULL DEFAULT 0,
  landing_page_views bigint NOT NULL DEFAULT 0,
  add_to_carts bigint NOT NULL DEFAULT 0,
  purchases bigint NOT NULL DEFAULT 0,
  purchase_value numeric(14,4) NOT NULL DEFAULT 0,
  n_days int NOT NULL DEFAULT 0,
  PRIMARY KEY (footage_id, window_kind)
);

-- One row per tag of the representative copy's run. `kind` is the taxonomy's kind; a numeric
-- dimension also carries its number, so terciles are a query rather than a Python pass.
-- Attributes read off the copy text (headline, primary text) do not belong here: they vary
-- between copies of one file and will get their own table when something needs them.
CREATE TABLE footage_attribute (
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  dimension text NOT NULL,
  value text NOT NULL,
  kind text NOT NULL,
  numeric_value double precision,
  source_tag_run_id uuid REFERENCES tag_run(id) ON DELETE SET NULL,
  taxonomy_major int NOT NULL,
  PRIMARY KEY (footage_id, dimension, value)
);
CREATE INDEX footage_attribute_lookup_idx ON footage_attribute (dimension, value);

-- The v2 profile's ordered scenes, with the end of each taken from the next one's start.
-- `shows_face` / `shows_product` are read off the scene text the same way the account read
-- reads them, so an opening is described by independent facts rather than one forced choice.
CREATE TABLE scene (
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  ordinal int NOT NULL,
  t_start_s real NOT NULL,
  t_end_s real,
  label text NOT NULL,
  raw text,
  shows_face boolean NOT NULL DEFAULT false,
  shows_product boolean NOT NULL DEFAULT false,
  PRIMARY KEY (footage_id, ordinal)
);

-- Meta's per-second retention curve, pooled over the file's copies and weighted by plays:
-- 22 buckets, one per second to 15s and then widening. Read live today, materialised here so
-- scene boundaries and drop-offs can be joined across every file in one query.
CREATE TABLE play_curve_point (
  footage_id uuid NOT NULL REFERENCES footage(id) ON DELETE CASCADE,
  window_kind text NOT NULL CHECK (window_kind IN ('lifetime', 'first_7d', 'last_30d')),
  bucket int NOT NULL CHECK (bucket BETWEEN 0 AND 21),
  start_s int NOT NULL,
  end_s int,
  share double precision NOT NULL,
  plays bigint NOT NULL,
  PRIMARY KEY (footage_id, window_kind, bucket)
);

-- migrate:down
DROP TABLE play_curve_point;
DROP TABLE scene;
DROP TABLE footage_attribute;
DROP TABLE footage_metric_window;
DROP TABLE footage_creative;
DROP TABLE footage;
