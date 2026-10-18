"""Runtime settings, read from the environment (see docker-compose.yml / .env)."""
import os

from psycopg.conninfo import make_conninfo


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# --- MQTT ---------------------------------------------------------------------
MQTT_HOST = os.environ.get("MQTT_HOST", "mqtt-broker")
MQTT_PORT = _int("MQTT_PORT", 1883)
# Read-only account (ACL: read facility/#). Ingestion is disabled if no password.
BACKEND_MQTT_USER = os.environ.get("BACKEND_MQTT_USER", "backend")
BACKEND_MQTT_PASSWORD = os.environ.get("BACKEND_MQTT_PASSWORD", "")
# Only account allowed to write facility/lock1/cmd. Commands are disabled if no password.
CONTROLLER_MQTT_USER = os.environ.get("CONTROLLER_MQTT_USER", "controller")
CONTROLLER_MQTT_PASSWORD = os.environ.get("CONTROLLER_MQTT_PASSWORD", "")
LOCK_CMD_TOPIC = "facility/lock1/cmd"

# --- Database -----------------------------------------------------------------
# Either a full DATABASE_URL, or the POSTGRES_* parts (safely quoted by psycopg).
DATABASE_URL = os.environ.get("DATABASE_URL") or make_conninfo(
    host=os.environ.get("POSTGRES_HOST", "postgres"),
    port=os.environ.get("POSTGRES_PORT", "5432"),
    dbname=os.environ.get("POSTGRES_DB", "cyberrange"),
    user=os.environ.get("POSTGRES_USER", "cyberrange"),
    password=os.environ.get("POSTGRES_PASSWORD", ""),
)

# --- API ------------------------------------------------------------------------
# Bearer token for write endpoints (POST). Empty = write endpoints disabled (fails closed).
API_TOKEN = os.environ.get("API_TOKEN", "")

# --- Processing rules -----------------------------------------------------------
AUTOMATION_ENABLED = os.environ.get("AUTOMATION_ENABLED", "true").lower() == "true"
DENY_ALARM_THRESHOLD = _int("DENY_ALARM_THRESHOLD", 3)  # denied scans ...
DENY_WINDOW_S = _int("DENY_WINDOW_S", 60)               # ... within this window => ALARM
RATE_LIMIT_PER_MIN = _int("RATE_LIMIT_PER_MIN", 600)    # per device; excess is dropped
