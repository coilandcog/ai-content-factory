-- 002_reference_notes.sql: the owner's reference library (ROADMAP 2.20, D-100).
-- Style notes Gemini wrote about images and clips the owner likes. Only approved notes reach the
-- prompts; the files themselves are never used in a video.

CREATE TABLE reference_notes (
    id                   INTEGER PRIMARY KEY,
    channel_id           INTEGER NOT NULL REFERENCES channels (id),
    kind                 TEXT NOT NULL CHECK (kind IN ('image', 'video')),
    original_name        TEXT NOT NULL,
    path                 TEXT NOT NULL,
    sha256               TEXT NOT NULL,
    summary              TEXT NOT NULL,
    lighting             TEXT NOT NULL,
    camera               TEXT NOT NULL,
    composition          TEXT NOT NULL,
    motion               TEXT NOT NULL,
    materials_colours    TEXT NOT NULL,
    prompt_phrases_json  TEXT NOT NULL,
    has_people           INTEGER NOT NULL CHECK (has_people IN (0, 1)),
    owner_note           TEXT,
    status               TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'approved', 'removed')),
    model                TEXT,
    added_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    decided_at           TEXT,
    UNIQUE (channel_id, sha256)
);
