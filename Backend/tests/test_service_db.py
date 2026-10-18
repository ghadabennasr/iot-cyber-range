"""Integration tests: need a PostgreSQL. Set TEST_DATABASE_URL, e.g.
   postgresql://postgres:pw@localhost:5432/cyberrange_test
They are skipped otherwise."""
import json
import os

import pytest

from app import config, db
from app.service import Processor

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


class FakeCommander:
    def __init__(self):
        self.sent = []

    def send(self, cmd, reason, issued_by):
        db.record_command(cmd, reason, issued_by)
        self.sent.append((cmd, reason, issued_by))


@pytest.fixture()
def proc(monkeypatch):
    db.open_pool(URL, wait_s=10)
    with db.pool.connection() as conn:
        conn.execute("TRUNCATE mqtt_events, lock_commands, devices RESTART IDENTITY CASCADE")
        conn.execute("DELETE FROM authorized_badges WHERE badge_id <> 'B-102'")
        conn.execute("UPDATE authorized_badges SET active = TRUE WHERE badge_id = 'B-102'")
    monkeypatch.setattr(config, "AUTOMATION_ENABLED", True)
    monkeypatch.setattr(config, "DENY_ALARM_THRESHOLD", 3)
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 600)
    yield Processor(FakeCommander())
    db.close_pool()


def badge(proc, badge_id="B-102", device="badge1", seq=1, topic="facility/badge1/access", retained=False):
    return proc.handle_message(topic, json.dumps({"device": device, "badge_id": badge_id, "seq": seq}).encode(),
                               0, retained)


def test_authorized_badge_unlocks_and_is_stored(proc):
    badge(proc)
    assert proc.commander.sent == [("UNLOCK", "badge B-102 granted", "automation")]
    rows = db.list_badge_events(10)
    assert rows[0]["decision"] == "granted" and rows[0]["badge_id"] == "B-102"
    dev = db.list_devices()[0]
    assert dev["device_id"] == "badge1" and dev["kind"] == "badge-controller" and dev["status"] == "online"


def test_unknown_badge_denied_then_alarm_after_threshold(proc):
    for i in range(2):
        badge(proc, "EVIL-1", seq=i)
    assert proc.commander.sent == []
    badge(proc, "EVIL-1", seq=3)
    assert [c[0] for c in proc.commander.sent] == ["ALARM"]
    badge(proc, "EVIL-1", seq=4)                       # alarm already raised in this window
    assert [c[0] for c in proc.commander.sent] == ["ALARM"]
    assert {r["decision"] for r in db.list_badge_events(10)} == {"denied"}


def test_inactive_badge_is_denied(proc):
    db.upsert_badge("B-999", "ex-employee", False)
    badge(proc, "B-999")
    assert db.list_badge_events(1)[0]["decision"] == "denied" and proc.commander.sent == []


def test_spoofed_and_retained_badge_events_never_unlock(proc):
    badge(proc, device="lock1")                         # claims to be another device
    badge(proc, retained=True)                          # replayed retained message
    assert proc.commander.sent == []
    assert [r["decision"] for r in db.list_badge_events(10)] == ["ignored", "ignored"]
    assert any(e["spoofed"] for e in db.list_events(10, "badge_access", None))


def test_invalid_payload_journaled_not_projected(proc):
    proc.handle_message("facility/badge1/access", b"garbage")
    assert db.list_badge_events(10) == []
    ev = db.list_events(10, None, None)[0]
    assert ev["valid"] is False and ev["error"] and proc.commander.sent == []


def test_motion_and_lock_state_projection(proc):
    proc.handle_message("facility/motion1/reading",
                        json.dumps({"device": "motion1", "motion": True, "distance_cm": 40.0, "seq": 1}).encode())
    proc.handle_message("facility/motion1/reading",
                        json.dumps({"device": "motion1", "motion": True, "distance_cm": 0, "seq": 999999}).encode())
    assert [m["plausible"] for m in db.list_motion(10, False)] == [False, True]
    assert [m["distance_cm"] for m in db.list_motion(10, True)] == [40.0]
    proc.handle_message("facility/lock1/state",
                        json.dumps({"device": "lock1", "state": "unlocked", "alarm": False, "reason": "cmd:unlock", "seq": 2}).encode())
    assert db.latest_lock_state()["state"] == "unlocked"
    proc.handle_message("facility/lock1/state", json.dumps({"device": "lock1", "state": "offline", "alarm": False}).encode())
    assert {d["device_id"]: d["status"] for d in db.list_devices()}["lock1"] == "offline"


def test_observed_commands_do_not_register_a_device(proc):
    proc.handle_message("facility/lock1/cmd", b'{"cmd":"UNLOCK"}')
    assert db.list_devices() == []
    assert db.list_events(5, "lock_command", None)[0]["device_id"] == "lock1"


def test_rate_limit_drops_flood(proc, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 5)
    for i in range(20):
        proc.handle_message("facility/motion1/reading",
                            json.dumps({"device": "motion1", "motion": False, "distance_cm": 50, "seq": i}).encode())
    assert len(db.list_motion(100, False)) == 5 and proc.dropped == 15


def test_automation_can_be_disabled(proc, monkeypatch):
    monkeypatch.setattr(config, "AUTOMATION_ENABLED", False)
    badge(proc)
    assert proc.commander.sent == [] and db.list_badge_events(1)[0]["decision"] == "granted"
