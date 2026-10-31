-- 008_series.sql: one topic as several linked videos (ROADMAP 2.24, D-112).
-- The parts come from the machine's blueprint drawing; each part is a normal content item.
CREATE TABLE series (
    id                  INTEGER PRIMARY KEY,
    channel_id          INTEGER NOT NULL REFERENCES channels (id),
    topic               TEXT NOT NULL,
    parts               INTEGER NOT NULL CHECK (parts BETWEEN 2 AND 5),
    teaser_content_id   INTEGER REFERENCES contents (id),  -- an overview video the parts link to
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
ALTER TABLE contents ADD COLUMN series_id INTEGER REFERENCES series (id);
ALTER TABLE contents ADD COLUMN part_n INTEGER CHECK (part_n IS NULL OR part_n >= 1);
CREATE INDEX idx_contents_series ON contents (series_id);
