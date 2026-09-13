-- Multi-paper chat: a chat session can span multiple papers.
-- Single-paper sessions keep using chat_sessions.paper_id (backward compatible).
-- Multi-paper sessions set paper_id = NULL and store their papers in the join table.

ALTER TABLE chat_sessions ALTER COLUMN paper_id DROP NOT NULL;

CREATE TABLE IF NOT EXISTS chat_session_papers (
  session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  paper_id   TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  position   INT NOT NULL,
  PRIMARY KEY (session_id, paper_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_session_papers_session
ON chat_session_papers(session_id, position);
