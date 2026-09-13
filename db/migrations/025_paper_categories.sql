-- Zotero-style paper category tree (multi-category per paper).
--
-- paper_categories: adjacency-list tree nodes (parent_id NULL = top level).
-- paper_category_assignments: many-to-many paper <-> category links.
-- Then migrate the legacy flat paper_marks.category text into top-level
-- nodes + assignments, and clear the legacy column (guarded so the
-- every-startup migration re-run stays a no-op).

CREATE TABLE IF NOT EXISTS paper_categories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  parent_id UUID REFERENCES paper_categories(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_paper_categories_user_parent
ON paper_categories(user_id, parent_id);

-- NULL != NULL in PG, so same-name-under-same-parent uniqueness needs a
-- COALESCE expression index instead of a plain unique constraint.
CREATE UNIQUE INDEX IF NOT EXISTS uq_paper_categories_user_parent_name
ON paper_categories(user_id, COALESCE(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), name);

CREATE TABLE IF NOT EXISTS paper_category_assignments (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  category_id UUID NOT NULL REFERENCES paper_categories(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (paper_id, category_id)
);

CREATE INDEX IF NOT EXISTS idx_pca_user_category
ON paper_category_assignments(user_id, category_id);

CREATE INDEX IF NOT EXISTS idx_pca_user_paper
ON paper_category_assignments(user_id, paper_id);

-- Legacy flat category text -> top-level nodes, once per user (a user with
-- any existing nodes has already been migrated).
INSERT INTO paper_categories (user_id, parent_id, name, position)
SELECT user_id, NULL, category,
       row_number() OVER (PARTITION BY user_id ORDER BY MIN(updated_at)) - 1
FROM paper_marks
WHERE category IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM paper_categories pc WHERE pc.user_id = paper_marks.user_id)
GROUP BY user_id, category
ON CONFLICT DO NOTHING;

-- Backfill assignments from the legacy text.
INSERT INTO paper_category_assignments (user_id, paper_id, category_id)
SELECT pm.user_id, pm.paper_id, pc.id
FROM paper_marks pm
JOIN paper_categories pc
  ON pc.user_id = pm.user_id AND pc.parent_id IS NULL AND pc.name = pm.category
WHERE pm.category IS NOT NULL
ON CONFLICT DO NOTHING;

-- Terminal state: empty the legacy column. Without this, a user clearing a
-- paper's categories in the new UI would see it resurrected by the next
-- startup migration re-run (the guard above is per-user, not per-paper).
UPDATE paper_marks SET category = NULL WHERE category IS NOT NULL;
