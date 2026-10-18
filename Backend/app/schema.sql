-- Idempotent schema, applied at backend start-up.

-- One row per device seen on the broker (id comes from the MQTT topic, not the payload).
CREATE TABLE IF NOT EXISTS devices (
    device_id       TEXT PRIMARY KEY,
    kind            TEXT,
    status          TEXT NOT NULL DEFAULT 'unknown',   -- online | offline | unknown
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_event_type TEXT
);

-- Raw journal: every message received on facility/#, valid or not.
CREATE TABLE IF NOT EXISTS mqtt_events (
    id             BIGSERIAL PRIMARY KEY,
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    topic          TEXT NOT NULL,
    device_id      TEXT,        -- derived from the topic: trusted (the ACL guards topics)
    claimed_device TEXT,        -- "device" field inside the payload: untrusted
    event_type     TEXT NOT NULL,
    valid          BOOLEAN NOT NULL,
    error          TEXT,
    retained       BOOLEAN NOT NULL DEFAULT FALSE,
    qos            SMALLINT NOT NULL DEFAULT 0,
    payload        JSONB,
    raw            TEXT NOT NULL,
    -- payload claims to come from another device than the topic it was published on
    spoofed        BOOLEAN GENERATED ALWAYS AS (
                       claimed_device IS NOT NULL AND device_id IS NOT NULL
                       AND claimed_device <> device_id) STORED
);
CREATE INDEX IF NOT EXISTS idx_events_received ON mqtt_events (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_device   ON mqtt_events (device_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type     ON mqtt_events (event_type, received_at DESC);

-- Badges allowed to open the door.
CREATE TABLE IF NOT EXISTS authorized_badges (
    badge_id   TEXT PRIMARY KEY,
    holder     TEXT,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Badge used by the badge-controller firmware (B-102) so the demo works out of the box.
INSERT INTO authorized_badges (badge_id, holder) VALUES ('B-102', 'Demo badge')
    ON CONFLICT (badge_id) DO NOTHING;

-- Typed projections of the journal.
CREATE TABLE IF NOT EXISTS badge_events (
    event_id    BIGINT PRIMARY KEY REFERENCES mqtt_events(id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ NOT NULL,
    badge_id    TEXT NOT NULL,
    seq         BIGINT,
    decision    TEXT NOT NULL CHECK (decision IN ('granted', 'denied', 'ignored')),
    reason      TEXT
);
CREATE INDEX IF NOT EXISTS idx_badge_received ON badge_events (received_at DESC);

CREATE TABLE IF NOT EXISTS motion_readings (
    event_id    BIGINT PRIMARY KEY REFERENCES mqtt_events(id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ NOT NULL,
    distance_cm DOUBLE PRECISION NOT NULL,
    motion      BOOLEAN NOT NULL,
    seq         BIGINT,
    plausible   BOOLEAN NOT NULL   -- false when outside the sensor's physical range
);
CREATE INDEX IF NOT EXISTS idx_motion_received ON motion_readings (received_at DESC);

CREATE TABLE IF NOT EXISTS lock_states (
    event_id    BIGINT PRIMARY KEY REFERENCES mqtt_events(id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ NOT NULL,
    state       TEXT NOT NULL,
    alarm       BOOLEAN NOT NULL,
    reason      TEXT,
    seq         BIGINT
);
CREATE INDEX IF NOT EXISTS idx_lockstate_received ON lock_states (received_at DESC);

-- Commands this backend asked the controller account to publish.
CREATE TABLE IF NOT EXISTS lock_commands (
    id        BIGSERIAL PRIMARY KEY,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cmd       TEXT NOT NULL CHECK (cmd IN ('LOCK', 'UNLOCK', 'ALARM', 'ALARM_OFF')),
    reason    TEXT,
    issued_by TEXT NOT NULL,        -- 'automation' or 'api'
    published BOOLEAN NOT NULL DEFAULT FALSE,
    error     TEXT
);
CREATE INDEX IF NOT EXISTS idx_cmd_issued ON lock_commands (issued_at DESC);
