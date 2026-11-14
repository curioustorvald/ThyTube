-- How often each profile passed over a video offered by the channel shuffle ("cast again").
-- pick_next weighs often-passed-over videos down. Kept apart from watch_stats so a reroll doesn't
-- make the video look watched (watch_stats rows are listed as "who watched this").
CREATE TABLE reroll_stats (
    profile_id       INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id         TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    reroll_count     INTEGER NOT NULL DEFAULT 0,
    last_rerolled_at TEXT,
    PRIMARY KEY (profile_id, video_id)
);
