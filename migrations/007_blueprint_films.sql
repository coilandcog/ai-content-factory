-- 007_blueprint_films.sql: code-drawn explainer films (D-111).
-- Each item says how its picture is made: Flow clips (the old way) or a blueprint film.
ALTER TABLE contents ADD COLUMN visual_style TEXT NOT NULL DEFAULT 'flow'
    CHECK (visual_style IN ('flow', 'blueprint'));

-- The film is a new asset kind. SQLite cannot change a CHECK, so the table is rebuilt with the
-- same columns and rows (nothing references assets).
CREATE TABLE assets_new (
    id              INTEGER PRIMARY KEY,
    content_id      INTEGER NOT NULL REFERENCES contents (id),
    kind            TEXT NOT NULL CHECK (kind IN (
                        'flow_sheet', 'flow_clip', 'still', 'still_anim', 'diagram', 'voiceover',
                        'subtitles', 'music', 'render_youtube', 'render_tiktok', 'preview', 'cover',
                        'thumbnail', 'metadata', 'blueprint')),
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
