-- Per-paper user note. Stored on paper_marks: same (user_id, paper_id) key,
-- same upsert semantics, and CASCADE-deleted together with the mark.

ALTER TABLE paper_marks
  ADD COLUMN IF NOT EXISTS note TEXT;

-- Mirror paper_notes.MAX_NOTE_LENGTH (10_000 chars) at the DB layer.
-- DROP + ADD keeps the every-startup re-run idempotent.
ALTER TABLE paper_marks
  DROP CONSTRAINT IF EXISTS chk_paper_marks_note_length;
ALTER TABLE paper_marks
  ADD CONSTRAINT chk_paper_marks_note_length
  CHECK (note IS NULL OR char_length(note) <= 10000);
