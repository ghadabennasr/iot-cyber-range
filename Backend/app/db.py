"""PostgreSQL access: connection pool, schema bootstrap, storage, read queries."""
import logging
import time
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import config
from .ingest import DEVICE_EVENTS, DEVICE_KIND, ParsedEvent

log = logging.getLogger("backend.db")
SCHEMA_FILE = Path(__file__).with_name("schema.sql")

pool: ConnectionPool | None = None


def open_pool(conninfo: str | None = None, wait_s: int = 60) -> ConnectionPool:
    """Open the pool and apply the schema, waiting for Postgres to come up."""
    global pool
    pool = ConnectionPool(
        conninfo or config.DATABASE_URL,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    deadline = time.time() + wait_s
    while True:
        try:
            pool.open(wait=True, timeout=5)
            break
        except Exception as error:  # Postgres still starting
            if time.time() > deadline:
                raise
            log.warning("Waiting for database: %s", error)
            pool.close()
            time.sleep(2)
    init_schema()
    return pool


def close_pool() -> None:
    if pool is not None:
        pool.close()


def init_schema() -> None:
    with pool.connection() as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


def is_up() -> bool:
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- storage ------

def device_status_for(ev: ParsedEvent) -> str:
    if ev.event_type == "device_status":
        return ev.fields["status"]
    if ev.event_type == "lock_state" and ev.fields["state"] == "offline":
        return "offline"
    return "online"


def store_event(conn, ev: ParsedEvent, decision: tuple[str, str] | None = None) -> int:
    """Journal one event and its typed projection. `decision` = (decision, reason) for badge scans."""
    row = conn.execute(
        """INSERT INTO mqtt_events
               (topic, device_id, claimed_device, event_type, valid, error, retained, qos, payload, raw)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id, received_at""",
        (ev.topic, ev.device_id, ev.claimed_device, ev.event_type, ev.valid, ev.error,
         ev.retained, ev.qos, Jsonb(ev.data) if ev.data is not None else None, ev.raw),
    ).fetchone()
    event_id, received_at = row["id"], row["received_at"]

    if ev.valid:
        f = ev.fields
        if ev.event_type == "badge_access":
            conn.execute(
                """INSERT INTO badge_events (event_id, received_at, badge_id, seq, decision, reason)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (event_id, received_at, f["badge_id"], f["seq"], decision[0], decision[1]),
            )
        elif ev.event_type == "motion_reading":
            conn.execute(
                """INSERT INTO motion_readings
                       (event_id, received_at, distance_cm, motion, seq, plausible)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (event_id, received_at, f["distance_cm"], f["motion"], f["seq"], f["plausible"]),
            )
        elif ev.event_type == "lock_state":
            conn.execute(
                """INSERT INTO lock_states (event_id, received_at, state, alarm, reason, seq)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (event_id, received_at, f["state"], f["alarm"], f["reason"], f["seq"]),
            )

    if ev.valid and not ev.spoofed and ev.device_id and ev.event_type in DEVICE_EVENTS:
        conn.execute(
            """INSERT INTO devices (device_id, kind, status, last_event_type)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (device_id) DO UPDATE SET
                   last_seen = now(),
                   status = EXCLUDED.status,
                   last_event_type = EXCLUDED.last_event_type,
                   kind = COALESCE(devices.kind, EXCLUDED.kind)""",
            (ev.device_id, DEVICE_KIND.get(ev.event_type), device_status_for(ev), ev.event_type),
        )
    return event_id


def badge_is_authorized(conn, badge_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM authorized_badges WHERE badge_id = %s AND active", (badge_id,)
    ).fetchone()
    return row is not None


def recent_denials(conn, window_s: int) -> int:
    """Denied scans in the window, counting the one being processed (inserted before this call)."""
    return conn.execute(
        """SELECT count(*) AS n FROM badge_events
           WHERE decision = 'denied' AND received_at > now() - make_interval(secs => %s)""",
        (window_s,),
    ).fetchone()["n"]


def alarm_recently_issued(conn, window_s: int) -> bool:
    return conn.execute(
        """SELECT 1 FROM lock_commands
           WHERE cmd = 'ALARM' AND issued_at > now() - make_interval(secs => %s) LIMIT 1""",
        (window_s,),
    ).fetchone() is not None


def record_command(cmd: str, reason: str | None, issued_by: str) -> int:
    with pool.connection() as conn:
        return conn.execute(
            "INSERT INTO lock_commands (cmd, reason, issued_by) VALUES (%s, %s, %s) RETURNING id",
            (cmd, (reason or "")[:200] or None, issued_by),
        ).fetchone()["id"]


def finish_command(command_id: int, published: bool, error: str | None) -> None:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE lock_commands SET published = %s, error = %s WHERE id = %s",
            (published, error, command_id),
        )


# ------------------------------------------------------------------ queries ----

def _limit(value: int) -> int:
    return max(1, min(int(value), 500))


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def list_devices() -> list[dict]:
    return fetch_all(
        """SELECT device_id, kind, status, first_seen, last_seen, last_event_type,
                  round(extract(epoch FROM now() - last_seen)::numeric, 1) AS seconds_since_seen
           FROM devices ORDER BY device_id"""
    )


def list_events(limit: int, event_type: str | None, device_id: str | None) -> list[dict]:
    return fetch_all(
        """SELECT id, received_at, topic, device_id, claimed_device, event_type, valid, error,
                  retained, spoofed, payload
           FROM mqtt_events
           WHERE (%s::text IS NULL OR event_type = %s) AND (%s::text IS NULL OR device_id = %s)
           ORDER BY id DESC LIMIT %s""",
        (event_type, event_type, device_id, device_id, _limit(limit)),
    )


def list_badge_events(limit: int) -> list[dict]:
    return fetch_all(
        "SELECT * FROM badge_events ORDER BY received_at DESC, event_id DESC LIMIT %s",
        (_limit(limit),),
    )


def list_motion(limit: int, plausible_only: bool) -> list[dict]:
    return fetch_all(
        """SELECT * FROM motion_readings WHERE (NOT %s OR plausible)
           ORDER BY received_at DESC, event_id DESC LIMIT %s""",
        (plausible_only, _limit(limit)),
    )


def latest_lock_state() -> dict | None:
    rows = fetch_all("SELECT * FROM lock_states ORDER BY received_at DESC, event_id DESC LIMIT 1")
    return rows[0] if rows else None


def list_lock_commands(limit: int) -> list[dict]:
    return fetch_all("SELECT * FROM lock_commands ORDER BY id DESC LIMIT %s", (_limit(limit),))


def list_badges() -> list[dict]:
    return fetch_all("SELECT * FROM authorized_badges ORDER BY badge_id")


def upsert_badge(badge_id: str, holder: str | None, active: bool) -> None:
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO authorized_badges (badge_id, holder, active) VALUES (%s, %s, %s)
               ON CONFLICT (badge_id) DO UPDATE SET holder = EXCLUDED.holder, active = EXCLUDED.active""",
            (badge_id, holder, active),
        )


def stats() -> dict:
    by_type = fetch_all(
        """SELECT event_type, count(*) AS total, count(*) FILTER (WHERE NOT valid) AS invalid,
                  count(*) FILTER (WHERE spoofed) AS spoofed
           FROM mqtt_events GROUP BY event_type ORDER BY event_type"""
    )
    badges = fetch_all("SELECT decision, count(*) AS total FROM badge_events GROUP BY decision")
    return {"events_by_type": by_type, "badge_decisions": {r["decision"]: r["total"] for r in badges}}
