-- ThyTube initial schema. Timestamps are UTC ISO-8601 strings; sizes are integer bytes.

CREATE TABLE profiles (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    colour      TEXT NOT NULL DEFAULT '#5b8def',
    created_at  TEXT NOT NULL
);

CREATE TABLE channels (
    id           TEXT PRIMARY KEY,           -- UC…
    handle       TEXT,
    title        TEXT,
    avatar_path  TEXT,                       -- relative to the pool
    avatar_url   TEXT,                       -- remote, for fetching
    updated_at   TEXT
);

CREATE TABLE subscriptions (
    id               INTEGER PRIMARY KEY,
    channel_id       TEXT NOT NULL REFERENCES channels(id),
    enabled          INTEGER NOT NULL DEFAULT 1,
    include_regex    TEXT,
    exclude_regex    TEXT,
    case_sensitive   INTEGER NOT NULL DEFAULT 0,
    min_duration_s   INTEGER,
    max_duration_s   INTEGER,
    include_shorts   INTEGER NOT NULL DEFAULT 0,
    include_streams  INTEGER NOT NULL DEFAULT 0,
    backfill_count   INTEGER NOT NULL DEFAULT 10,
    backfill_done    INTEGER NOT NULL DEFAULT 0,
    max_height       INTEGER,
    created_at       TEXT NOT NULL,
    last_polled_at   TEXT,
    next_poll_at     TEXT,
    last_poll_status TEXT,
    last_poll_error  TEXT
);
CREATE INDEX subscriptions_channel ON subscriptions(channel_id);

CREATE TABLE videos (
    id                TEXT PRIMARY KEY,
    channel_id        TEXT,
    title             TEXT,
    description       TEXT,
    duration_s        INTEGER,
    published_at      TEXT,
    chapters_json     TEXT,
    thumb_path        TEXT,                  -- relative to the pool
    thumb_url         TEXT,                  -- remote, for uncached display
    live_status       TEXT,
    is_short          INTEGER,
    state             TEXT NOT NULL,
    origin            TEXT NOT NULL,         -- subscription | manual
    subscription_id   INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    filter_reason     TEXT,
    pinned            INTEGER NOT NULL DEFAULT 0,
    max_height        INTEGER,               -- per-video override (from subscription)
    est_bytes         INTEGER,
    reserved_bytes    INTEGER,               -- non-NULL only while downloading
    size_bytes        INTEGER,
    file_path         TEXT,                  -- relative to the pool
    height            INTEGER,
    width             INTEGER,
    vcodec            TEXT,
    acodec            TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    systemic_failures INTEGER NOT NULL DEFAULT 0,
    last_error_class  TEXT,
    last_error        TEXT,
    next_attempt_at   TEXT,
    subs_state        TEXT,                  -- NULL | pending | done | failed
    subs_attempts     INTEGER NOT NULL DEFAULT 0,
    subs_next_at      TEXT,
    discovered_at     TEXT NOT NULL,
    queued_at         TEXT,
    downloaded_at     TEXT,
    evicted_at        TEXT,
    total_watch_count INTEGER NOT NULL DEFAULT 0,
    last_watched_at   TEXT
);
CREATE INDEX videos_state ON videos(state, next_attempt_at);
CREATE INDEX videos_channel ON videos(channel_id, state);
CREATE INDEX videos_downloaded ON videos(downloaded_at);

CREATE TABLE subtitles (
    video_id  TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    lang      TEXT NOT NULL,
    kind      TEXT NOT NULL,                 -- manual | auto
    path      TEXT NOT NULL,                 -- relative to the pool
    PRIMARY KEY (video_id, lang, kind)
);

CREATE TABLE watch_stats (
    profile_id      INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id        TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    watch_count     INTEGER NOT NULL DEFAULT 0,
    position_s      REAL NOT NULL DEFAULT 0,
    last_watched_at TEXT,
    PRIMARY KEY (profile_id, video_id)
);
CREATE INDEX watch_stats_recent ON watch_stats(profile_id, last_watched_at);

CREATE TABLE play_sessions (
    id                TEXT PRIMARY KEY,      -- client-generated UUID
    profile_id        INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
    video_id          TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    started_at        TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    played_s          REAL NOT NULL DEFAULT 0,
    counted           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE commands (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    taken_at     TEXT,
    done_at      TEXT,
    result_json  TEXT
);
CREATE INDEX commands_pending ON commands(done_at, id);

CREATE TABLE events (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    level     TEXT NOT NULL,                 -- info | warn | error
    kind      TEXT NOT NULL,
    message   TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX events_ts ON events(ts);

CREATE TABLE kv (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
