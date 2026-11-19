-- Playlists: user-made, or imported from a YouTube playlist (source_id = its list= ID).
-- Shared by the household like the library; profile_id only records who made it.
CREATE TABLE playlists (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT,
    profile_id   INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    source_id    TEXT,                       -- YouTube playlist ID, when imported
    source_title TEXT,                       -- its title and owner on YouTube
    source_owner TEXT,
    auto_cache   INTEGER NOT NULL DEFAULT 0, -- syncing queues new videos for caching
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    synced_at    TEXT
);

-- Items point at `videos` rows. A video that isn't cached is kept as state 'discovered' (origin
-- 'playlist'): nothing acts on it until someone caches it, or a subscription picks it up.
CREATE TABLE playlist_items (
    playlist_id  INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    video_id     TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    added_at     TEXT NOT NULL,
    PRIMARY KEY (playlist_id, video_id)
);
CREATE INDEX playlist_items_order ON playlist_items(playlist_id, position);
CREATE INDEX playlist_items_video ON playlist_items(video_id);
