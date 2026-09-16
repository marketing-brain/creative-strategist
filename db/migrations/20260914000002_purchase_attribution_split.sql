-- migrate:up
-- Purchases split by Meta attribution window. basic_ad_actions / basic_ad_action_values
-- already carry _7_d_click and _1_d_view per action row; in org 2's data (2026-09-14) the
-- two sum exactly to `value` for every purchase row. View-through purchases say nothing
-- about click quality, so click-side claims need the click-attributed count.
ALTER TABLE metric_daily
  ADD COLUMN purchases_click_7d BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN purchases_view_1d BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN purchase_value_click_7d NUMERIC(14,4) NOT NULL DEFAULT 0,
  ADD COLUMN purchase_value_view_1d NUMERIC(14,4) NOT NULL DEFAULT 0;

-- migrate:down
ALTER TABLE metric_daily
  DROP COLUMN purchases_click_7d, DROP COLUMN purchases_view_1d,
  DROP COLUMN purchase_value_click_7d, DROP COLUMN purchase_value_view_1d;
