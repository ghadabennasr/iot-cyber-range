import json
import os
import re
import socketserver
from datetime import datetime, timezone
from urllib.parse import unquote


EVENTS_PATH = os.environ.get("EVENTS_PATH", "/events/events.jsonl")
PORT = int(os.environ.get("HONEYPOT_PORT", "2323"))
# Flag shell-style injection, not ordinary words: command separators/operators,
# or a known command directly after a separator ("...;whoami", "|cat /etc/passwd").
# Plain "?id=5" or "/cat/status" must NOT match.
COMMANDS = r"(?:whoami|id|uname|cat|ls|curl|wget|nc|bash|sh|ping)"
INJECTION_MARKERS = re.compile(
    r"(?:&&|\|\||`|\$\(|[;|\n]\s*" + COMMANDS + r"\b)",
    re.IGNORECASE,
)


def emit_event(event):
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    encoded = (json.dumps(event, separators=(",", ":")) + "\n").encode()
    os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
    descriptor = os.open(EVENTS_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)


class HoneypotHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        try:
            raw = self.rfile.readline(4097)
        except TimeoutError:
            raw = b""
        payload = raw[:4096].decode("utf-8", errors="replace").strip()
        decoded_payload = unquote(payload)
        event = {
            "event_type": "honeypot_interaction",
            "source": self.client_address[0],
            "service": "fake-maintenance-console",
            "payload": payload[:512],
            "command_injection_attempt": bool(
                INJECTION_MARKERS.search(payload)
                or INJECTION_MARKERS.search(decoded_payload)
            ),
        }
        emit_event(event)
        self.wfile.write(b"Maintenance console unavailable.\n")


class ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with ThreadedServer(("0.0.0.0", PORT), HoneypotHandler) as server:
        print(f"Lab honeypot listening on internal TCP port {PORT}", flush=True)
        server.serve_forever()
