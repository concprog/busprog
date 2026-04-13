-- Raw events table (fallback for all messages)
CREATE TABLE IF NOT EXISTS raw_events (
    time        TIMESTAMPTZ  NOT NULL,
    topic       TEXT,
    route_id    TEXT,
    stop_id     TEXT,
    vehicle_id  TEXT,
    payload     JSONB
);

SELECT create_hypertable('raw_events', by_range('time'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_raw_events_route_time
    ON raw_events (route_id, time DESC);

CREATE INDEX IF NOT EXISTS idx_raw_events_stop_time
    ON raw_events (stop_id, time DESC);

-- ARIMA delay predictions (actual vs predicted)
CREATE TABLE IF NOT EXISTS arima_predictions (
    time        TIMESTAMPTZ  NOT NULL,
    route_id    TEXT,
    stop_id     TEXT,
    vehicle_id  TEXT,
    delay_min   INTEGER,
    pred_delay  INTEGER,
    arima_mse   REAL
);

SELECT create_hypertable('arima_predictions', by_range('time'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_arima_route_time
    ON arima_predictions (route_id, time DESC);

-- Headway/bunching metrics
CREATE TABLE IF NOT EXISTS headway_metrics (
    time           TIMESTAMPTZ  NOT NULL,
    route_id       TEXT,
    stop_id        TEXT,
    mean_hw_sec    REAL,
    ideal_hw_sec   REAL,
    congestion_sec REAL,
    choke_state    TEXT
);

SELECT create_hypertable('headway_metrics', by_range('time'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_headway_route_time
    ON headway_metrics (route_id, time DESC);

-- Advisory actions log
CREATE TABLE IF NOT EXISTS advisories (
    time        TIMESTAMPTZ  NOT NULL,
    route_id    TEXT,
    stop_id     TEXT,
    action      TEXT,
    reason      TEXT,
    queue_length INTEGER,
    arrival_freq REAL
);

SELECT create_hypertable('advisories', by_range('time'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_advisories_route_time
    ON advisories (route_id, time DESC);
