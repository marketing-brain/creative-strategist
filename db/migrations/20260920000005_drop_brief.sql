-- migrate:up
-- The weekly brief is gone: the Briefs tab, its two routes, the renderer and the
-- `insights.weekly_brief` job and schedule were all removed. It restated fatigue insights
-- that the Fatigue page already shows, and nothing read it. `compute_fatigue` and the
-- `insight` rows it writes stay -- they still feed that page.
DELETE FROM schedule_state WHERE name = 'insights.weekly_brief';
DROP TABLE brief;

-- migrate:down
CREATE TABLE brief (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  week_start DATE NOT NULL,
  content_md TEXT NOT NULL,
  insight_ids UUID[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, week_start)
);
