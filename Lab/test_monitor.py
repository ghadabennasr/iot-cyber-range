import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import monitor


def rules():
    return [alert["rule"] for alert in monitor.event_rows("alerts", 50)]


def motion(seq, distance=15):
    return SimpleNamespace(
        topic="facility/motion1/reading",
        payload=json.dumps({"device": "motion1", "distance_cm": distance, "seq": seq}).encode(),
        qos=0,
    )


class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base = self.temp_dir.name
        self.patch = patch.multiple(
            monitor,
            DB_PATH=f"{base}/test.sqlite3",
            EVENTS_DIR=base,
            RESPONSE_REQUESTS=f"{base}/response-requests.jsonl",
        )
        self.patch.start()
        monitor.sensor_sequences.clear()
        monitor.recent_honeypot_sources.clear()
        monitor.broker_clients.clear()
        monitor.last_broker_source = "unknown"
        monitor.initialize_db()

    def tearDown(self):
        self.patch.stop()
        self.temp_dir.cleanup()


class SensorRuleTests(MonitorTestCase):
    def test_events_preserve_type_and_source_fields(self):
        event_id, _ = monitor.record_event("test-event", "test-source", {"ok": True})
        row = monitor.event_rows("events", 1)[0]
        self.assertEqual(
            (row["id"], row["event_type"], row["source"], row["details"]),
            (event_id, "test-event", "test-source", {"ok": True}),
        )

    def test_out_of_range_motion_reading_creates_alert(self):
        monitor.process_mqtt_message(motion(10, distance=0))
        self.assertEqual(rules(), ["sensor-range-anomaly"])

    def test_repeated_sequence_is_replay(self):
        monitor.process_mqtt_message(motion(4))
        monitor.process_mqtt_message(motion(4))
        self.assertIn("sensor-replay", rules())

    def test_normal_increasing_sequence_is_quiet(self):
        for seq in (1, 2, 3, 4):
            monitor.process_mqtt_message(motion(seq))
        self.assertEqual(rules(), [])

    def test_device_restart_is_not_replay(self):
        for seq in (50, 51, 52):
            monitor.process_mqtt_message(motion(seq))
        monitor.process_mqtt_message(motion(1))  # counter reset after reboot
        monitor.process_mqtt_message(motion(2))
        self.assertEqual(rules(), [])

    def test_huge_sequence_jump_alerts_but_does_not_poison_replay(self):
        monitor.process_mqtt_message(motion(5))
        monitor.process_mqtt_message(motion(999999))
        self.assertIn("sensor-seq-jump", rules())
        before = len(rules())
        for seq in (6, 7, 8):  # real device carries on afterwards
            monitor.process_mqtt_message(motion(seq))
        self.assertEqual(len(rules()), before)


class HoneypotRuleTests(MonitorTestCase):
    def test_any_honeypot_contact_raises_low_alert(self):
        monitor.process_honeypot_event(
            {"event_type": "honeypot_interaction", "source": "172.20.0.8",
             "command_injection_attempt": False, "payload": "GET /"}
        )
        self.assertEqual(rules(), ["honeypot-contact"])
        self.assertEqual(monitor.event_rows("alerts", 1)[0]["severity"], "low")

    def test_command_like_input_raises_high_alert(self):
        monitor.process_honeypot_event(
            {"event_type": "honeypot_interaction", "source": "172.20.0.8",
             "command_injection_attempt": True, "payload": "ping;whoami"}
        )
        self.assertIn("command-injection-attempt", rules())


class BrokerLogTests(MonitorTestCase):
    CONNECT = "1760000000: New client connected from {ip}:40000 as {cid} (p2, c1, k10, u'{user}')."

    def feed(self, *lines):
        for line in lines:
            monitor.process_broker_line(line)

    def test_denied_publish_resolves_username_and_source_from_client_id(self):
        self.feed(
            "1760000000: New connection from 172.20.0.5:40000 on port 1883.",
            self.CONNECT.format(ip="172.20.0.5", cid="range-exercise-1", user="badge1"),
            "1760000001: Denied PUBLISH from range-exercise-1 "
            "(d0, q1, r0, m1, 'facility/lock1/cmd', ... (4 bytes))",
        )
        event = monitor.event_rows("events", 1)[0]
        self.assertEqual(event["event_type"], "mqtt_denied")
        self.assertEqual(event["source"], "172.20.0.5")
        self.assertEqual(event["details"]["username"], "badge1")
        self.assertEqual(event["details"]["topic"], "facility/lock1/cmd")
        self.assertEqual(
            sorted(rules()), ["forged-lock-command", "unauthorized-mqtt-activity"]
        )

    def test_attribution_survives_other_clients_connecting_in_between(self):
        self.feed(
            "1: New connection from 172.20.0.5:40000 on port 1883.",
            self.CONNECT.format(ip="172.20.0.5", cid="attacker-1", user="badge1"),
            "2: New connection from 172.20.0.99:41000 on port 1883.",  # unrelated client
            self.CONNECT.format(ip="172.20.0.99", cid="ids-1", user="ids"),
            "3: Denied PUBLISH from attacker-1 (d0, q0, r0, m0, 'facility/lock1/cmd', ... (4 bytes))",
        )
        self.assertEqual(monitor.event_rows("events", 1)[0]["source"], "172.20.0.5")

    def test_denied_publish_to_non_command_topic_is_not_forged_command(self):
        self.feed(
            self.CONNECT.format(ip="172.20.0.5", cid="c1", user="motion1"),
            "3: Denied PUBLISH from c1 (d0, q0, r0, m0, 'facility/badge1/access', ... (4 bytes))",
        )
        self.assertEqual(rules(), ["unauthorized-mqtt-activity"])

    def test_anonymous_rejection_uses_last_connection_source(self):
        self.feed(
            "1: New connection from 172.20.0.9:41000 on port 1883.",
            "1: Client <unknown> disconnected, not authorised.",
        )
        event = monitor.event_rows("events", 1)[0]
        self.assertEqual(event["source"], "172.20.0.9")
        self.assertIsNone(event["details"]["username"])
        self.assertEqual(rules(), ["unauthorized-mqtt-activity"])

    def test_honeypot_then_broker_denial_correlates_same_source(self):
        monitor.recent_honeypot_sources["172.20.0.9"] = monitor.time.time()
        self.feed(
            self.CONNECT.format(ip="172.20.0.9", cid="c9", user="badge1"),
            "3: Denied PUBLISH from c9 (d0, q1, r0, m1, 'facility/lock1/cmd', ... (4 bytes))",
        )
        self.assertIn("honeypot-to-broker-correlation", rules())

    def test_unrelated_log_lines_do_nothing(self):
        self.feed(
            "1: Received PUBLISH from c1 (d0, q0, r0, m0, 'facility/denied-room/x', ... (4 bytes))",
            "1: Sending CONNACK to c1 (0, 0)",
        )
        self.assertEqual(rules(), [])


class LogReaderTests(MonitorTestCase):
    def test_half_written_line_is_not_consumed_and_bad_lines_do_not_stall(self):
        base = self.temp_dir.name
        log = os.path.join(base, "log.txt")
        offset = os.path.join(base, "log.offset")
        seen = []

        def callback(line):
            if line == "boom":
                raise KeyError("bad line")
            seen.append(line)

        with open(log, "wb") as handle:
            handle.write(b"one\nboom\ntwo\npart")
        monitor.read_new_lines(log, offset, callback)
        self.assertEqual(seen, ["one", "two"])
        with open(log, "ab") as handle:
            handle.write(b"ial\n")
        monitor.read_new_lines(log, offset, callback)
        self.assertEqual(seen, ["one", "two", "partial"])


class DashboardApiTests(MonitorTestCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), monitor.DashboardHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/respond"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def post(self, body, token=None, content_type="application/json"):
        headers = {"Content-Type": content_type}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_fails_closed_when_no_token_configured(self):
        with patch.object(monitor, "DASHBOARD_TOKEN", ""):
            self.assertEqual(self.post({"username": "badge1"}, token="anything"), 503)

    def test_rejects_wrong_or_missing_token(self):
        with patch.object(monitor, "DASHBOARD_TOKEN", "s3cret"):
            self.assertEqual(self.post({"username": "badge1"}), 401)
            self.assertEqual(self.post({"username": "badge1"}, token="nope"), 401)

    def test_rejects_non_json_content_type(self):
        with patch.object(monitor, "DASHBOARD_TOKEN", "s3cret"):
            self.assertEqual(
                self.post({"username": "badge1"}, token="s3cret", content_type="text/plain"), 415
            )

    def test_valid_token_queues_block_and_ids_is_protected(self):
        with patch.object(monitor, "DASHBOARD_TOKEN", "s3cret"):
            self.assertEqual(self.post({"username": "badge1"}, token="s3cret"), 202)
            self.assertEqual(self.post({"username": "ids"}, token="s3cret"), 400)
        self.assertIn("response-requested", rules())


if __name__ == "__main__":
    unittest.main()
