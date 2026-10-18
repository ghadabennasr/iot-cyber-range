import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingest import parse_message  # noqa: E402


def msg(topic, obj, **kw):
    payload = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    return parse_message(topic, payload, **kw)


def test_badge_ok():
    ev = msg("facility/badge1/access", {"device": "badge1", "badge_id": "B-102", "seq": 3})
    assert ev.valid and ev.event_type == "badge_access" and ev.device_id == "badge1"
    assert ev.fields == {"badge_id": "B-102", "seq": 3} and ev.trusted


def test_motion_ok_and_plausibility():
    ok = msg("facility/motion1/reading", {"device": "motion1", "motion": True, "distance_cm": 42.5, "seq": 1})
    assert ok.valid and ok.fields["plausible"] is True
    far = msg("facility/motion1/reading", {"device": "motion1", "motion": False, "distance_cm": 0, "seq": 2})
    assert far.valid and far.fields["plausible"] is False   # stored, but flagged implausible


def test_lock_state_and_status():
    ev = msg("facility/lock1/state", {"device": "lock1", "state": "unlocked", "alarm": False, "reason": "cmd:unlock", "seq": 4})
    assert ev.valid and ev.fields["state"] == "unlocked"
    assert not msg("facility/lock1/state", {"state": "exploded"}).valid
    assert msg("facility/motion1/status", {"device": "motion1", "status": "offline"}).valid


def test_spoofed_device_is_not_trusted():
    ev = msg("facility/badge1/access", {"device": "lock1", "badge_id": "B-102", "seq": 1})
    assert ev.valid and ev.spoofed and not ev.trusted


def test_retained_is_not_trusted():
    ev = msg("facility/badge1/access", {"device": "badge1", "badge_id": "B-102"}, retained=True)
    assert ev.valid and not ev.trusted


def test_malformed_payloads_are_kept_but_invalid():
    for body in (b"not json", b"[1,2]", b'{"badge_id": 5}', b'{"badge_id":"x y"}',
                 b'{"badge_id":"B-1","seq":-1}', b'{"badge_id":"B-1","seq":true}',
                 b'{"badge_id":"B-1","seq":1e30}', b"\xff\xfe\x00"):
        ev = parse_message("facility/badge1/access", body)
        assert not ev.valid and ev.error, body
    for body in (b'{"motion":true,"distance_cm":NaN}', b'{"motion":true,"distance_cm":Infinity}',
                 b'{"motion":"yes","distance_cm":3}', b'{"motion":true,"distance_cm":"3"}'):
        assert not parse_message("facility/motion1/reading", body).valid, body


def test_deeply_nested_json_does_not_crash():
    assert not parse_message("facility/badge1/access", b"[" * 100000).valid


def test_unknown_topics_and_commands():
    assert not parse_message("facility/weird", b"{}").valid
    assert not parse_message("facility/../etc/passwd", b"{}").valid
    cmd = parse_message("facility/lock1/cmd", b"UNLOCK")
    assert cmd.valid and cmd.event_type == "lock_command"


def test_raw_is_bounded():
    ev = parse_message("facility/badge1/access", b"A" * 100000)
    assert len(ev.raw) <= 2048
