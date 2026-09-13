-- Per-user paper categories in My Papers (stored on the mark row).
ALTER TABLE paper_marks ADD COLUMN IF NOT EXISTS category TEXT;

CREATE INDEX IF NOT EXISTS idx_paper_marks_user_category
ON paper_marks(user_id, category);
