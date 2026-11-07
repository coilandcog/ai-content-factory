-- 010_scenes.sql: scenes as units of a Blender film (D-124, ROADMAP 2.27).
-- Each scene is rendered as its own clip, checked, approved or redone on its own. An approval
-- holds the clip's fingerprint (SHA-256): a changed file is no longer approved.
CREATE TABLE scenes (
    id               INTEGER PRIMARY KEY,
    content_id       INTEGER NOT NULL REFERENCES contents (id),
    chapter          INTEGER NOT NULL DEFAULT 1 CHECK (chapter >= 1),  -- more chapters: task 2.30
    n                INTEGER NOT NULL CHECK (n >= 1),                  -- order within the video
    role             TEXT NOT NULL,          -- hook | body | note | close (the model's scene)
    start_s          REAL NOT NULL,          -- on the narration's timeline
    end_s            REAL NOT NULL,
    first_frame      INTEGER NOT NULL,       -- 1-based frames of the whole film, 30 fps
    last_frame       INTEGER NOT NULL,
    narration        TEXT NOT NULL DEFAULT '',  -- the words spoken while it is on screen
    status           TEXT NOT NULL DEFAULT 'waiting'
                         CHECK (status IN ('waiting', 'rendered', 'approved', 'redo')),
    path             TEXT,                   -- the clip in the asset folder, relative to the root
    sha256           TEXT,                   -- fingerprint of that clip
    approved_sha256  TEXT,                   -- the fingerprint the owner approved
    reason           TEXT,                   -- why it is being redone
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (content_id, chapter, n),
    CHECK (last_frame >= first_frame AND end_s > start_s)
);
CREATE INDEX idx_scenes_content ON scenes (content_id);

-- Every decision on a scene, with the exact file it was about (approval history).
CREATE TABLE scene_reviews (
    id          INTEGER PRIMARY KEY,
    scene_id    INTEGER NOT NULL REFERENCES scenes (id),
    decision    TEXT NOT NULL CHECK (decision IN ('approve', 'redo')),
    sha256      TEXT,
    reason      TEXT,
    decided_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (decision = 'approve' OR (reason IS NOT NULL AND trim(reason) <> ''))
);
CREATE INDEX idx_scene_reviews_scene ON scene_reviews (scene_id);
