"""Parse and validate MQTT messages from facility/#.

Pure functions only (no I/O) so the rules can be unit-tested without a broker
or a database. Anything that does not fit the expected shape is still kept as
a raw event with valid=False: an invalid or odd message is itself useful data
for the security side of the project.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_RAW_CHARS = 2048
TOPIC_RE = re.compile(r"^facility/([A-Za-z0-9_-]{1,32})/([A-Za-z0-9_/-]{1,64})$")
BADGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# last topic segment -> event type
EVENT_TYPES = {
    "access": "badge_access",
    "reading": "motion_reading",
    "state": "lock_state",
    "status": "device_status",
    "cmd": "lock_command",
}
# event types that are emitted BY a device (as opposed to commands sent TO it)
DEVICE_EVENTS = {"badge_access", "motion_reading", "lock_state", "device_status"}
DEVICE_KIND = {
    "badge_access": "badge-controller",
    "motion_reading": "motion-sensor",
    "lock_state": "lock-actuator",
}

# Physical range of the HC-SR04 used by the motion sensor; the IDS uses 1..1000.
MIN_PLAUSIBLE_CM = 2.0
MAX_PLAUSIBLE_CM = 400.0
LOCK_STATES = {"locked", "unlocked", "offline"}
DEVICE_STATUSES = {"online", "offline"}


@dataclass
class ParsedEvent:
    topic: str
    event_type: str
    device_id: Optional[str]
    raw: str
    qos: int = 0
    retained: bool = False
    data: Optional[dict] = None
    claimed_device: Optional[str] = None
    valid: bool = True
    error: Optional[str] = None
    fields: dict = field(default_factory=dict)  # typed values for the projection tables

    @property
    def spoofed(self) -> bool:
        return bool(self.claimed_device and self.device_id and self.claimed_device != self.device_id)

    @property
    def trusted(self) -> bool:
        """Safe to act on automatically: well-formed, live (not retained), not impersonating."""
        return self.valid and not self.retained and not self.spoofed


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bad_seq(seq: Any) -> bool:
    """seq is optional, but when present it must fit comfortably in a BIGINT."""
    return seq is not None and (not _is_int(seq) or not 0 <= seq <= 2**53)


def _reject_constant(name: str):
    raise ValueError(f"non-standard JSON constant {name}")  # NaN / Infinity


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate(ev: ParsedEvent) -> None:
    data = ev.data or {}
    if ev.event_type == "badge_access":
        badge_id, seq = data.get("badge_id"), data.get("seq")
        if not isinstance(badge_id, str) or not BADGE_ID_RE.match(badge_id):
            raise ValueError("badge_id missing or malformed")
        if _bad_seq(seq):
            raise ValueError("seq must be a non-negative integer")
        ev.fields = {"badge_id": badge_id, "seq": seq}
    elif ev.event_type == "motion_reading":
        distance, motion, seq = data.get("distance_cm"), data.get("motion"), data.get("seq")
        if not _is_number(distance) or distance != distance or abs(distance) > 1e6:
            raise ValueError("distance_cm must be a finite number")
        if not isinstance(motion, bool):
            raise ValueError("motion must be a boolean")
        if _bad_seq(seq):
            raise ValueError("seq must be a non-negative integer")
        ev.fields = {
            "distance_cm": float(distance),
            "motion": motion,
            "seq": seq,
            "plausible": MIN_PLAUSIBLE_CM <= distance <= MAX_PLAUSIBLE_CM,
        }
    elif ev.event_type == "lock_state":
        state, alarm, reason, seq = (data.get(k) for k in ("state", "alarm", "reason", "seq"))
        if state not in LOCK_STATES:
            raise ValueError("state must be locked, unlocked or offline")
        if alarm is not None and not isinstance(alarm, bool):
            raise ValueError("alarm must be a boolean")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("reason must be a string")
        if _bad_seq(seq):
            raise ValueError("seq must be a non-negative integer")
        ev.fields = {
            "state": state,
            "alarm": bool(alarm),
            "reason": reason[:80] if reason else None,
            "seq": seq,
        }
    elif ev.event_type == "device_status":
        if data.get("status") not in DEVICE_STATUSES:
            raise ValueError("status must be online or offline")
        ev.fields = {"status": data["status"]}


def parse_message(topic: str, payload: bytes, qos: int = 0, retained: bool = False) -> ParsedEvent:
    raw = payload.decode("utf-8", errors="replace")[:MAX_RAW_CHARS].replace("\x00", "")
    match = TOPIC_RE.match(topic)
    device_id = match.group(1) if match else None
    event_type = EVENT_TYPES.get(match.group(2).split("/")[-1], "unknown") if match else "unknown"
    ev = ParsedEvent(topic=topic[:200], event_type=event_type, device_id=device_id,
                     raw=raw, qos=qos, retained=retained)

    if event_type == "lock_command":
        return ev  # commands are free text/JSON for the lock; we only journal them
    if event_type == "unknown":
        ev.valid, ev.error = False, "unrecognised topic"
        return ev

    try:
        decoded = json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        decoded = None
    if not isinstance(decoded, dict):
        ev.valid, ev.error = False, "payload is not a JSON object"
        return ev
    ev.data = decoded
    claimed = decoded.get("device")
    ev.claimed_device = claimed[:32] if isinstance(claimed, str) else None
    try:
        _validate(ev)
    except ValueError as error:
        ev.valid, ev.error = False, str(error)
    return ev
