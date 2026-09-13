-- Manually added papers carry a user-entered publication year. arXiv papers
-- keep arxiv_papers.published_at and leave this NULL (the card shows
-- published_at first, falling back to this year only when no date exists).

ALTER TABLE papers
  ADD COLUMN IF NOT EXISTS published_year INTEGER;
