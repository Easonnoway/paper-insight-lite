-- "Recently opened" (clicked into the detail page and stayed) is a separate
-- concept from the manual "viewed" mark: last_opened_at refreshes on every
-- qualifying open, while viewed/viewed_at become purely manual. Existing rows
-- keep their semantics; last_opened_at cannot be backfilled (no history).

ALTER TABLE paper_marks
  ADD COLUMN IF NOT EXISTS last_opened_at TIMESTAMPTZ;
