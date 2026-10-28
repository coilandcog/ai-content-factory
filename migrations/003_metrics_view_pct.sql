-- 003_metrics_view_pct.sql: YouTube Shorts report an average view percentage above 100 % when
-- viewers rewatch (#5: 133 %), so the 0-100 check on metrics.avg_view_pct was wrong (D-101).
-- SQLite can't change a CHECK in place: copy the table into a new one with the right rule.

CREATE TABLE metrics_new (
    id                   INTEGER PRIMARY KEY,
    post_id              INTEGER NOT NULL REFERENCES posts (id),
    checkpoint           TEXT NOT NULL CHECK (checkpoint IN ('24h', '72h', '7d', '28d')),
    source               TEXT NOT NULL CHECK (source IN ('api', 'csv', 'manual')),
    views                INTEGER CHECK (views >= 0),
    avg_view_duration_s  REAL CHECK (avg_view_duration_s >= 0),
    avg_view_pct         REAL CHECK (avg_view_pct BETWEEN 0 AND 1000),
    stayed_to_watch_pct  REAL CHECK (stayed_to_watch_pct BETWEEN 0 AND 100),
    likes                INTEGER CHECK (likes >= 0),
    comments             INTEGER CHECK (comments >= 0),
    shares               INTEGER CHECK (shares >= 0),
    subscribers_gained   INTEGER,
    watch_time_min       REAL CHECK (watch_time_min >= 0),
    collected_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (post_id, checkpoint)
);

INSERT INTO metrics_new SELECT * FROM metrics;
DROP TABLE metrics;
ALTER TABLE metrics_new RENAME TO metrics;
