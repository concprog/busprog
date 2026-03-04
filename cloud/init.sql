CREATE TABLE IF NOT EXISTS raw_events (
    time        TIMESTAMPTZ  NOT NULL,
    topic       TEXT,
    route_id    TEXT,
    stop_id     TEXT,
    vehicle_id  TEXT,
    payload     JSONB
);

SELECT create_hypertable('raw_events', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_raw_events_route_time
    ON raw_events (route_id, time DESC);

CREATE INDEX IF NOT EXISTS idx_raw_events_stop_time
    ON raw_events (stop_id, time DESC);
