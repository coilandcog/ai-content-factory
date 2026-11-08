-- 011_chapters.sql: chapter titles of a long video (D-128, ROADMAP 2.28).
-- A long video is told in chapters; each scene names its chapter (scenes.chapter), so a chapter's
-- start time is its first scene's start. The titles become YouTube's chapter list.
CREATE TABLE chapters (
    id          INTEGER PRIMARY KEY,
    content_id  INTEGER NOT NULL REFERENCES contents (id),
    n           INTEGER NOT NULL CHECK (n >= 1),
    title       TEXT NOT NULL CHECK (trim(title) <> ''),
    UNIQUE (content_id, n)
);
CREATE INDEX idx_chapters_content ON chapters (content_id);
