-- 009_blender_style.sql: Blender films (D-121, D-123).
-- An item's picture can come from a Blender 3D model, and the rendered film is a new asset kind.
-- SQLite cannot change a CHECK, so both tables are rebuilt with the same columns and rows. Other
-- tables point at `contents`, so the runner switches foreign keys off for every migration and
-- checks them all before it commits (db.migrate).
CREATE TABLE contents_new (
    id                     INTEGER PRIMARY KEY,
    channel_id             INTEGER NOT NULL REFERENCES channels (id),
    format                 TEXT NOT NULL CHECK (format IN ('short', 'deep_dive', 'episode')),
    pillar                 TEXT,   -- no CHECK: pillars come from each channel's YAML
    object_name            TEXT NOT NULL,
    working_title          TEXT,
    angle                  TEXT,
    hook_type              TEXT,   -- no CHECK: list lives in CONTENT_STRATEGY.md §6
    beat_order             TEXT CHECK (beat_order IN ('classic', 'context_first', 'action_first', 'cold_open')),
    virality_score         INTEGER CHECK (virality_score BETWEEN 0 AND 100),
    score_breakdown_json   TEXT,
    status                 TEXT NOT NULL DEFAULT 'idea' CHECK (status IN (
                               'idea', 'scripted', 'script_approved', 'awaiting_clips', 'generated',
                               'assembled', 'approved', 'scheduled', 'published', 'analyzed',
                               'rejected', 'failed', 'archived')),
    status_before_failure  TEXT,
    status_reason          TEXT,
    planned_publish_at     TEXT,
    -- Affiliate links are disabled at launch (D-009): the database refuses any value.
    affiliate_links_json   TEXT CHECK (affiliate_links_json IS NULL),
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    visual_style           TEXT NOT NULL DEFAULT 'flow'
                               CHECK (visual_style IN ('flow', 'blueprint', 'blender')),
    series_id              INTEGER REFERENCES series (id),
    part_n                 INTEGER CHECK (part_n IS NULL OR part_n >= 1)
);
INSERT INTO contents_new (id, channel_id, format, pillar, object_name, working_title, angle,
    hook_type, beat_order, virality_score, score_breakdown_json, status, status_before_failure,
    status_reason, planned_publish_at, affiliate_links_json, created_at, updated_at, visual_style,
    series_id, part_n)
SELECT id, channel_id, format, pillar, object_name, working_title, angle, hook_type, beat_order,
    virality_score, score_breakdown_json, status, status_before_failure, status_reason,
    planned_publish_at, affiliate_links_json, created_at, updated_at, visual_style, series_id,
    part_n
FROM contents;
DROP TABLE contents;
ALTER TABLE contents_new RENAME TO contents;
CREATE INDEX idx_contents_channel_status ON contents (channel_id, status);
CREATE INDEX idx_contents_series ON contents (series_id);

CREATE TABLE assets_new (
    id              INTEGER PRIMARY KEY,
    content_id      INTEGER NOT NULL REFERENCES contents (id),
    kind            TEXT NOT NULL CHECK (kind IN (
                        'flow_sheet', 'flow_clip', 'still', 'still_anim', 'diagram', 'voiceover',
                        'subtitles', 'music', 'render_youtube', 'render_tiktok', 'preview', 'cover',
                        'thumbnail', 'metadata', 'blueprint', 'blender')),
    shot_n          INTEGER,
    path            TEXT NOT NULL,
    sha256          TEXT,
    generator       TEXT,
    model           TEXT,
    prompt          TEXT,
    music_track_id  INTEGER REFERENCES music_tracks (id),
    license         TEXT,
    license_url     TEXT,
    duration_s      REAL,
    width           INTEGER,
    height          INTEGER,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
INSERT INTO assets_new SELECT * FROM assets;
DROP TABLE assets;
ALTER TABLE assets_new RENAME TO assets;
CREATE INDEX idx_assets_content ON assets (content_id);
