-- 001_init.sql: every table from docs/DATABASE.md §3.
-- Timestamps are ISO 8601 UTC text, e.g. 2026-10-22T15:00:00Z.
-- Lists of allowed values are CHECK constraints; keep them in sync with DATABASE.md.

CREATE TABLE channels (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    niche         TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('active', 'paused')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE platform_accounts (
    id                   INTEGER PRIMARY KEY,
    channel_id           INTEGER NOT NULL REFERENCES channels (id),
    platform             TEXT NOT NULL CHECK (platform IN ('youtube', 'tiktok', 'instagram', 'facebook')),
    handle               TEXT,
    external_account_id  TEXT,
    publish_mode         TEXT CHECK (publish_mode IN ('api_scheduled', 'inbox_draft', 'manual')),
    enabled              INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    UNIQUE (channel_id, platform)
);

CREATE TABLE trend_signals (
    id            INTEGER PRIMARY KEY,
    channel_id    INTEGER NOT NULL REFERENCES channels (id),
    source        TEXT NOT NULL CHECK (source IN (
                      'wikimedia_pageviews', 'youtube_search', 'youtube_popular',
                      'google_trends_csv', 'manual')),
    query         TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    metric_name   TEXT,
    metric_value  REAL,
    observed_at   TEXT NOT NULL,
    raw_json      TEXT
);
CREATE INDEX idx_trend_signals_channel_query ON trend_signals (channel_id, query);

CREATE TABLE contents (
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
    updated_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_contents_channel_status ON contents (channel_id, status);

CREATE TABLE content_signals (
    content_id  INTEGER NOT NULL REFERENCES contents (id),
    signal_id   INTEGER NOT NULL REFERENCES trend_signals (id),
    PRIMARY KEY (content_id, signal_id)
);

CREATE TABLE scripts (
    id              INTEGER PRIMARY KEY,
    content_id      INTEGER NOT NULL REFERENCES contents (id),
    version         INTEGER NOT NULL CHECK (version >= 1),
    hook            TEXT,
    body_text       TEXT,
    engineer_note   TEXT,
    loop_line       TEXT,
    word_count      INTEGER,
    est_duration_s  REAL,
    shot_list_json  TEXT,
    similarity_max  REAL,
    llm_provider    TEXT,
    llm_model       TEXT,
    prompt_version  TEXT,
    is_current      INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (content_id, version)
);
-- Partial unique index: at most one row per content may have is_current = 1.
CREATE UNIQUE INDEX idx_scripts_one_current ON scripts (content_id) WHERE is_current = 1;

CREATE TABLE sources (
    id           INTEGER PRIMARY KEY,
    content_id   INTEGER NOT NULL REFERENCES contents (id),
    url          TEXT NOT NULL,
    title        TEXT,
    publisher    TEXT,
    claim        TEXT,
    accessed_at  TEXT
);
CREATE INDEX idx_sources_content ON sources (content_id);

CREATE TABLE music_tracks (
    id                    INTEGER PRIMARY KEY,
    title                 TEXT NOT NULL,
    artist                TEXT,
    source                TEXT NOT NULL CHECK (source IN ('youtube_audio_library')),
    platform_scope        TEXT NOT NULL CHECK (platform_scope IN ('youtube')),
    attribution_required  INTEGER NOT NULL DEFAULT 0 CHECK (attribution_required IN (0, 1)),
    attribution_text      TEXT,
    license_url           TEXT,
    mood                  TEXT,
    bpm                   INTEGER,
    path                  TEXT NOT NULL,
    added_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE assets (
    id              INTEGER PRIMARY KEY,
    content_id      INTEGER NOT NULL REFERENCES contents (id),
    kind            TEXT NOT NULL CHECK (kind IN (
                        'flow_sheet', 'flow_clip', 'still', 'still_anim', 'diagram', 'voiceover',
                        'subtitles', 'music', 'render_youtube', 'render_tiktok', 'preview', 'cover',
                        'thumbnail', 'metadata')),
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
CREATE INDEX idx_assets_content ON assets (content_id);

CREATE TABLE reviews (
    id                   INTEGER PRIMARY KEY,
    content_id           INTEGER NOT NULL REFERENCES contents (id),
    gate                 TEXT NOT NULL CHECK (gate IN ('script', 'final')),
    decision             TEXT NOT NULL CHECK (decision IN ('approve', 'reject', 'redo')),
    reason               TEXT,
    channel              TEXT NOT NULL CHECK (channel IN ('cli', 'telegram')),
    telegram_message_id  INTEGER,
    decided_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- Reject and redo must say why: the learn stage uses the reasons.
    CHECK (decision = 'approve' OR (reason IS NOT NULL AND trim(reason) <> ''))
);
CREATE INDEX idx_reviews_content ON reviews (content_id);

CREATE TABLE posts (
    id                   INTEGER PRIMARY KEY,
    content_id           INTEGER NOT NULL REFERENCES contents (id),
    platform_account_id  INTEGER NOT NULL REFERENCES platform_accounts (id),
    platform             TEXT NOT NULL CHECK (platform IN ('youtube', 'tiktok')),
    status               TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                             'pending', 'uploaded', 'scheduled', 'draft_sent', 'published', 'failed')),
    external_id          TEXT,
    url                  TEXT,
    title                TEXT,
    caption              TEXT,
    hashtags             TEXT,
    -- We always disclose AI content (D-039): the database refuses anything else.
    ai_disclosure        INTEGER NOT NULL DEFAULT 1 CHECK (ai_disclosure = 1),
    privacy              TEXT CHECK (privacy IN ('private', 'unlisted', 'public', 'self_only')),
    scheduled_for        TEXT,
    published_at         TEXT,
    dry_run              INTEGER NOT NULL DEFAULT 1 CHECK (dry_run IN (0, 1)),
    UNIQUE (content_id, platform)
);

CREATE TABLE metrics (
    id                   INTEGER PRIMARY KEY,
    post_id              INTEGER NOT NULL REFERENCES posts (id),
    checkpoint           TEXT NOT NULL CHECK (checkpoint IN ('24h', '72h', '7d', '28d')),
    source               TEXT NOT NULL CHECK (source IN ('api', 'csv', 'manual')),
    views                INTEGER,
    avg_view_duration_s  REAL,
    avg_view_pct         REAL CHECK (avg_view_pct BETWEEN 0 AND 100),
    stayed_to_watch_pct  REAL CHECK (stayed_to_watch_pct BETWEEN 0 AND 100),
    likes                INTEGER,
    comments             INTEGER,
    shares               INTEGER,
    subscribers_gained   INTEGER,
    watch_time_min       REAL,
    collected_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (post_id, checkpoint)
);

CREATE TABLE performance_labels (
    content_id           INTEGER PRIMARY KEY REFERENCES contents (id),
    label                TEXT NOT NULL CHECK (label IN ('winner', 'average', 'loser')),
    basis_json           TEXT,
    overridden_by_owner  INTEGER NOT NULL DEFAULT 0 CHECK (overridden_by_owner IN (0, 1)),
    notes                TEXT,
    computed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE runs (
    id               INTEGER PRIMARY KEY,
    command          TEXT NOT NULL,
    channel_slug     TEXT,
    started_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    finished_at      TEXT,
    status           TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    items_processed  INTEGER NOT NULL DEFAULT 0,
    items_failed     INTEGER NOT NULL DEFAULT 0,
    dry_run          INTEGER NOT NULL CHECK (dry_run IN (0, 1)),
    error            TEXT
);

CREATE TABLE api_usage (
    id          INTEGER PRIMARY KEY,
    channel_id  INTEGER NOT NULL REFERENCES channels (id),
    service     TEXT NOT NULL CHECK (service IN (
                    'gemini', 'youtube_data', 'youtube_analytics', 'tiktok', 'flow', 'wikimedia',
                    'telegram')),
    usage_date  TEXT NOT NULL,   -- local date YYYY-MM-DD (Europe/Berlin)
    units       INTEGER NOT NULL DEFAULT 0 CHECK (units >= 0),
    UNIQUE (channel_id, service, usage_date)
);
