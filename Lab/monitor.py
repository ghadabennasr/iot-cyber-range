import hmac
import json
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import paho.mqtt.client as mqtt


EVENTS_DIR = os.environ.get("EVENTS_DIR", "/events")
DB_PATH = os.path.join(EVENTS_DIR, "lab.sqlite3")
HONEYPOT_LOG = os.path.join(EVENTS_DIR, "events.jsonl")
RESPONSE_REQUESTS = os.path.join(EVENTS_DIR, "response-requests.jsonl")
BROKER_LOG = "/mosquitto/log/mosquitto.log"
IDS_PASSWORD_FILE = "/run/secrets/ids_password"
# Accounts the operator may block. 'ids' and 'dashboard' are deliberately absent:
# blocking the detector would blind the lab.
BLOCKABLE_USERS = {"badge1", "motion1", "lock1", "controller", "backend", "canary"}
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "")
DATABASE_LOCK = threading.Lock()
REQUEST_LOCK = threading.Lock()
MAX_SEQ_JUMP = 1000
MAX_TRACKED_CLIENTS = 500
COMMAND_TOPIC = re.compile(r"^facility/[^/]+/cmd$")
CONNECT_LINE = re.compile(r"New client connected from (\S+) as (\S+) \(([^)]*)\)")
CONNECTION_LINE = re.compile(r"New connection from (\S+) on port")
DENIED_LINE = re.compile(r"Denied (PUBLISH|SUBSCRIBE) from (\S+)")
AUTH_FAIL_LINE = re.compile(r"Client (\S+) disconnected, not authori[sz]ed", re.IGNORECASE)
USERNAME_FIELD = re.compile(r"\bu'([^']*)'")
QUOTED_TOPIC = re.compile(r"'([^']*)'")
last_broker_source = "unknown"
broker_clients = OrderedDict()  # client id -> {"ip": ..., "username": ...}
recent_honeypot_sources = {}
sensor_sequences = {}  # topic -> deque of recent sequence numbers


def now():
    return datetime.now(timezone.utc).isoformat()


def connect_db():
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_db():
    os.makedirs(EVENTS_DIR, exist_ok=True)
    with connect_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT NOT NULL,
                details TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                rule TEXT NOT NULL,
                severity TEXT NOT NULL,
                summary TEXT NOT NULL,
                event_id INTEGER REFERENCES events(id)
            );
            """
        )


def record_event(event_type, source, details, timestamp=None):
    timestamp = timestamp or now()
    with DATABASE_LOCK, connect_db() as db:
        cursor = db.execute(
            "INSERT INTO events(timestamp,event_type,source,details) VALUES(?,?,?,?)",
            (timestamp, str(event_type), str(source), json.dumps(details)),
        )
        return cursor.lastrowid, timestamp


def create_alert(rule, severity, summary, event_id, timestamp=None):
    with DATABASE_LOCK, connect_db() as db:
        db.execute(
            "INSERT INTO alerts(timestamp,rule,severity,summary,event_id) VALUES(?,?,?,?,?)",
            (timestamp or now(), rule, severity, summary, event_id),
        )


def decode_event(row):
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "event_type": row["event_type"],
        "source": row["source"],
        "details": json.loads(row["details"]),
    }


def event_rows(table, limit):
    limit = max(1, min(limit, 500))
    with DATABASE_LOCK, connect_db() as db:
        rows = db.execute(
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        if table == "events":
            item["details"] = json.loads(item["details"])
        result.append(item)
    return result


def process_mqtt_message(message):
    try:
        payload = message.payload.decode("utf-8")
    except UnicodeDecodeError:
        payload = message.payload.decode("utf-8", errors="replace")
    details = {
        "topic": message.topic,
        "payload": payload[:2048],
        "qos": message.qos,
    }
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        decoded = None
    claimed_source = (
        decoded.get("device", "mqtt-client")
        if isinstance(decoded, dict)
        else "mqtt-client"
    )
    event_id, timestamp = record_event("mqtt_publish", claimed_source, details)

    if message.topic.startswith("facility/motion1/"):
        reading = decoded
        if isinstance(reading, dict):
            distance = reading.get("distance_cm")
            sequence = reading.get("seq")
            if isinstance(distance, (int, float)) and not 1 <= distance <= 1000:
                create_alert(
                    "sensor-range-anomaly",
                    "high",
                    f"Motion reading outside lab range ({distance} cm)",
                    event_id,
                    timestamp,
                )
            if isinstance(sequence, int) and not isinstance(sequence, bool):
                history = sensor_sequences.setdefault(message.topic, deque(maxlen=50))
                if history:
                    if sequence in history:
                        create_alert(
                            "sensor-replay",
                            "medium",
                            f"Motion sequence {sequence} was already seen",
                            event_id,
                            timestamp,
                        )
                    elif sequence < history[-1]:
                        history.clear()  # counter went back to an unseen value: device restart
                    elif sequence - history[-1] > MAX_SEQ_JUMP:
                        create_alert(
                            "sensor-seq-jump",
                            "medium",
                            f"Motion sequence jumped from {history[-1]} to {sequence}",
                            event_id,
                            timestamp,
                        )
                history.append(sequence)


def process_honeypot_event(event):
    source = event.get("source", "unknown")
    event_id, timestamp = record_event(
        event.get("event_type", "honeypot_interaction"), source, event, event.get("timestamp")
    )
    recent_honeypot_sources[source] = time.time()
    create_alert(
        "honeypot-contact",
        "low",
        f"{source} contacted the simulated maintenance honeypot; no legitimate client should.",
        event_id,
        timestamp,
    )
    if event.get("command_injection_attempt"):
        create_alert(
            "command-injection-attempt",
            "high",
            "Command-like input reached the simulated maintenance honeypot; no command was executed.",
            event_id,
            timestamp,
        )


def strip_port(remote):
    if remote.startswith("[") and "]:" in remote:
        return remote[1:remote.rfind("]:")]
    if ":" in remote:
        return remote.rsplit(":", 1)[0]
    return remote


def remember_client(client_id, ip, username):
    broker_clients[client_id] = {"ip": ip, "username": username}
    broker_clients.move_to_end(client_id)
    while len(broker_clients) > MAX_TRACKED_CLIENTS:
        broker_clients.popitem(last=False)


def process_broker_line(line):
    """Turn Mosquitto log lines into events/alerts.

    Mosquitto names the CLIENT ID (not the username) on denial lines, and the
    username/IP only on the earlier "New client connected" line, so we keep a
    client-id -> (ip, username) map and resolve denials through it.
    """
    global last_broker_source
    connection = CONNECTION_LINE.search(line)
    if connection:
        last_broker_source = strip_port(connection.group(1))
    connected = CONNECT_LINE.search(line)
    if connected:
        user = USERNAME_FIELD.search(connected.group(3))
        remember_client(
            connected.group(2),
            strip_port(connected.group(1)),
            user.group(1) if user else None,
        )
        return

    operation = None
    client_id = None
    topic = None
    denied = DENIED_LINE.search(line)
    auth_failure = AUTH_FAIL_LINE.search(line)
    if denied:
        operation, client_id = denied.group(1), denied.group(2)
        quoted = QUOTED_TOPIC.search(line)
        topic = quoted.group(1) if quoted else None
    elif auth_failure:
        operation = "CONNECT"
        client_id = auth_failure.group(1)
        if client_id == "<unknown>":
            client_id = None
    else:
        return

    known = broker_clients.get(client_id) if client_id else None
    source = known["ip"] if known else last_broker_source
    username = known["username"] if known else None
    details = {
        "log": line.strip()[:1000],
        "operation": operation,
        "client_id": client_id,
        "username": username,
        "topic": topic,
    }
    event_id, timestamp = record_event("mqtt_denied", source, details)
    create_alert(
        "unauthorized-mqtt-activity",
        "medium",
        f"Mosquitto rejected a {operation.lower()} from {username or 'an unidentified client'}.",
        event_id,
        timestamp,
    )
    if operation == "PUBLISH" and topic and COMMAND_TOPIC.match(topic):
        create_alert(
            "forged-lock-command",
            "high",
            f"{username or 'An unidentified client'} tried to publish to {topic}, "
            "which only the controller may command.",
            event_id,
            timestamp,
        )
    seen_at = recent_honeypot_sources.get(source)
    if seen_at and time.time() - seen_at <= 120:
        create_alert(
            "honeypot-to-broker-correlation",
            "critical",
            "The same lab source contacted the honeypot and then triggered a broker denial.",
            event_id,
            timestamp,
        )


def read_new_lines(path, offset_path, callback):
    try:
        size = os.path.getsize(path)
        try:
            with open(offset_path, encoding="ascii") as state:
                offset = int(state.read().strip() or "0")
        except (OSError, ValueError):
            offset = 0
        if offset > size:
            offset = 0
        with open(path, "rb") as source:
            source.seek(offset)
            while True:
                raw = source.readline()
                if not raw or not raw.endswith(b"\n"):
                    break  # nothing new, or a half-written line: retry next pass
                offset = source.tell()
                try:
                    callback(raw.decode("utf-8").strip())
                except Exception as error:  # one bad line must not stall the watcher
                    print(f"Skipped bad line in {path}: {error!r}", flush=True)
        with open(offset_path, "w", encoding="ascii") as state:
            state.write(str(offset))
    except FileNotFoundError:
        return
    except OSError as error:
        print(f"Could not read {path}: {error}", flush=True)


def watch_logs():
    while True:
        try:
            read_new_lines(
                HONEYPOT_LOG,
                os.path.join(EVENTS_DIR, "honeypot.offset"),
                lambda line: process_honeypot_event(json.loads(line)),
            )
            read_new_lines(
                BROKER_LOG,
                os.path.join(EVENTS_DIR, "broker.offset"),
                process_broker_line,
            )
        except Exception as error:  # keep detecting; never let this thread die silently
            print(f"Log watcher error: {error!r}", flush=True)
        time.sleep(0.5)


def queue_response(username, reason):
    if username not in BLOCKABLE_USERS:
        raise ValueError("That account cannot be blocked from the dashboard.")
    request = {
        "id": f"{int(time.time() * 1000)}-{threading.get_ident()}",
        "timestamp": now(),
        "action": "block",
        "username": username,
        "reason": reason[:300],
    }
    os.makedirs(EVENTS_DIR, exist_ok=True)
    encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode()
    with REQUEST_LOCK:
        descriptor = os.open(
            RESPONSE_REQUESTS,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
    event_id, timestamp = record_event(
        "response_requested", "dashboard", request, request["timestamp"]
    )
    create_alert(
        "response-requested",
        "info",
        f"Queued a local broker block for {username}; host watcher must be running.",
        event_id,
        timestamp,
    )
    return request


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            try:
                with open("/app/dashboard.html", encoding="utf-8") as page:
                    body = page.read().encode()
            except OSError as error:
                self.send_error(500, f"Dashboard unavailable: {error}")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/healthz":
            self.send_json(200, {"status": "ok"})
            return
        if parsed.path in ("/api/events", "/api/alerts", "/api/responses"):
            try:
                limit = int(parse_qs(parsed.query).get("limit", ["100"])[0])
            except ValueError:
                limit = 100
            table = {
                "/api/events": "events",
                "/api/alerts": "alerts",
                "/api/responses": "response_results",
            }[parsed.path]
            if table == "response_results":
                path = os.path.join(EVENTS_DIR, "response-results.jsonl")
                try:
                    with open(path, encoding="utf-8") as source:
                        items = [json.loads(line) for line in source if line.strip()]
                except FileNotFoundError:
                    items = []
                selected = items[-max(1, min(limit, 500)):]
                self.send_json(200, list(reversed(selected)))
            else:
                self.send_json(200, event_rows(table, limit))
            return
        self.send_error(404, "Not found")

    def authorize(self):
        """Bearer token from DASHBOARD_TOKEN; fails closed when unset."""
        if not DASHBOARD_TOKEN:
            self.send_json(503, {"error": "Response disabled: set DASHBOARD_TOKEN in .env."})
            return False
        header = self.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied.encode(), DASHBOARD_TOKEN.encode()):
            self.send_json(401, {"error": "Missing or invalid dashboard token."})
            return False
        return True

    def do_POST(self):
        if urlparse(self.path).path != "/api/respond":
            self.send_error(404, "Not found")
            return
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self.send_json(415, {"error": "Content-Type must be application/json."})
            return
        if not self.authorize():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 2048:
                raise ValueError("Request body must be between 1 and 2048 bytes.")
            body = json.loads(self.rfile.read(length))
            request = queue_response(
                body.get("username", ""),
                str(body.get("reason", "Requested from dashboard")),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
            return
        self.send_json(202, {"queued": True, "request": request})

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        print(f"dashboard: {format_string % args}", flush=True)


def start_dashboard():
    server = ThreadingHTTPServer(("0.0.0.0", 8080), DashboardHandler)
    print("Local security dashboard listening on port 8080", flush=True)
    server.serve_forever()


def start_mqtt_monitor():
    with open(IDS_PASSWORD_FILE, encoding="utf-8") as source:
        password = source.read().strip()
    if not password:
        raise RuntimeError("IDS password secret is empty.")
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="cyberrange-ids",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("ids", password)
    client.on_connect = lambda client, userdata, flags, reason, properties: (
        client.subscribe("facility/#") if reason == 0 else print(
            f"IDS MQTT connection rejected: {reason}", flush=True
        )
    )
    client.on_message = lambda client, userdata, message: process_mqtt_message(message)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect("mqtt-broker", 1883, keepalive=30)
    client.loop_forever()


if __name__ == "__main__":
    initialize_db()
    threading.Thread(target=watch_logs, daemon=True).start()
    threading.Thread(target=start_dashboard, daemon=True).start()
    start_mqtt_monitor()
