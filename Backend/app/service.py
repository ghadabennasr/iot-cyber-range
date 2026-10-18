"""Message processing: validate, store, decide.

Receives every facility/# message, journals it, and applies two small rules:

  * a valid scan of an authorized badge  -> UNLOCK (the lock re-locks by itself)
  * N denied scans within a time window  -> ALARM

Rules only act on *trusted* events: well-formed, live (not a retained replay) and
not claiming to come from a different device than the topic they were published on.
"""
import logging
import time
from collections import deque

from . import config, db
from .ingest import parse_message

log = logging.getLogger("backend.service")


class Processor:
    def __init__(self, commander):
        self.commander = commander          # needs .send(cmd, reason, issued_by)
        self.dropped = 0                    # messages dropped by the per-device rate limit
        self._windows: dict[str, deque] = {}

    def _rate_ok(self, key: str) -> bool:
        """Sliding 60 s window per device: a flooding client must not fill the database."""
        now = time.monotonic()
        window = self._windows.setdefault(key, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= config.RATE_LIMIT_PER_MIN:
            return False
        window.append(now)
        return True

    def handle_message(self, topic: str, payload: bytes, qos: int = 0, retained: bool = False):
        ev = parse_message(topic, payload, qos, retained)
        if not self._rate_ok(ev.device_id or "unknown"):
            self.dropped += 1
            if self.dropped % 100 == 1:
                log.warning("Rate limit: dropping messages from %s (%d dropped so far)",
                            ev.device_id or "unknown", self.dropped)
            return None

        actions = []
        with db.pool.connection() as conn:  # one transaction per message
            decision = None
            if ev.event_type == "badge_access" and ev.valid:
                badge_id = ev.fields["badge_id"]
                if not ev.trusted:
                    reason = "retained message" if ev.retained else "payload device does not match topic"
                    decision = ("ignored", reason)
                elif db.badge_is_authorized(conn, badge_id):
                    decision = ("granted", "authorized badge")
                else:
                    decision = ("denied", "unknown or inactive badge")
            event_id = db.store_event(conn, ev, decision)

            if decision and decision[0] == "granted":
                actions.append(("UNLOCK", f"badge {badge_id} granted"))
            elif decision and decision[0] == "denied":
                denied = db.recent_denials(conn, config.DENY_WINDOW_S)
                if denied >= config.DENY_ALARM_THRESHOLD and not db.alarm_recently_issued(
                        conn, config.DENY_WINDOW_S):
                    actions.append(("ALARM", f"{denied} denied badge scans in {config.DENY_WINDOW_S}s"))

        # Commands are sent after the commit so the journal never lags behind the actuator.
        if config.AUTOMATION_ENABLED:
            for cmd, reason in actions:
                self.commander.send(cmd, reason, "automation")
        return event_id
