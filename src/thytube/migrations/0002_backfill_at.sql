-- When a subscription's first backfill ran. Videos discovered after it are "new uploads" and don't
-- count towards backfill_count when the backfill is re-run with a different number.
ALTER TABLE subscriptions ADD COLUMN backfill_at TEXT;

-- Existing subscriptions: estimate it. The first backfill writes all its rows in one transaction, and
-- "before subscription" rows are only ever written by a backfill. A minute of slack covers rows written
-- a second or two later in the same transaction.
UPDATE subscriptions SET backfill_at = strftime('%Y-%m-%dT%H:%M:%SZ', COALESCE(
    (SELECT MAX(discovered_at) FROM videos v
      WHERE v.subscription_id = subscriptions.id AND v.filter_reason LIKE 'before subscription%'),
    (SELECT MIN(discovered_at) FROM videos v WHERE v.subscription_id = subscriptions.id),
    last_polled_at,
    created_at
), '+60 seconds')
WHERE backfill_done = 1;
