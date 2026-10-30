-- 004_lessons.sql: lessons learned from failed clip reviews (ROADMAP 2.18, D-103).
-- Gemini proposes general lessons from the failures; only lessons the owner approves reach the
-- film rules and the clip review. lesson_inputs remembers which reviews were already learned from.

CREATE TABLE lessons (
    id             INTEGER PRIMARY KEY,
    channel_id     INTEGER NOT NULL REFERENCES channels (id),
    text           TEXT NOT NULL,
    applies_to     TEXT NOT NULL CHECK (applies_to IN ('clip', 'frame', 'both')),
    evidence_json  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'removed')),
    model          TEXT,
    added_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    decided_at     TEXT
);

CREATE TABLE lesson_inputs (
    channel_id   INTEGER NOT NULL REFERENCES channels (id),
    review_key   TEXT NOT NULL,
    learned_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (channel_id, review_key)
);
