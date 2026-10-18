"""Play the three ESP32 devices against the broker, without Wokwi or bore.

Useful to test the backend and the dashboard on their own. Payloads and topics
are exactly the ones produced by the firmwares in esp32/.

  python simulate_devices.py lock                       # act as the lock actuator (Ctrl+C to stop)
  python simulate_devices.py badge --id B-102           # one badge scan
  python simulate_devices.py badge --id EVIL-1 --count 3
  python simulate_devices.py motion --distance 40       # object close  -> motion = true
  python simulate_devices.py motion --distance 250 --count 5 --interval 1

Broker address: --host/--port (default 127.0.0.1:1883, the loopback-published
Docker port). Passwords come from the environment, never from the command line:
BADGE1_PASSWORD, MOTION1_PASSWORD, LOCK1_PASSWORD.
"""
import argparse
import json
import os
import sys
import time

import paho.mqtt.client as mqtt


def connect(user, client_id, host, port, will=None):
    password = os.environ.get(f"{user.upper()}_PASSWORD")
    if not password:
        sys.exit(f"Set {user.upper()}_PASSWORD in the environment.")
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv311)
    client.username_pw_set(user, password)
    if will:
        client.will_set(*will)
    client.connect(host, port, keepalive=30)
    client.loop_start()
    return client


def run_badge(args):
    client = connect("badge1", "badge-controller-sim", args.host, args.port)
    for seq in range(1, args.count + 1):
        payload = json.dumps({"device": "badge1", "badge_id": args.id, "seq": seq}, separators=(",", ":"))
        client.publish("facility/badge1/access", payload).wait_for_publish(3)
        print("badge  ->", payload)
        time.sleep(args.interval)
    client.disconnect()
    client.loop_stop()


def run_motion(args):
    will = ("facility/motion1/status", '{"device":"motion1","status":"offline"}', 1, True)
    client = connect("motion1", "motion-sensor-sim", args.host, args.port, will)
    client.publish("facility/motion1/status", '{"device":"motion1","status":"online"}', retain=True)
    for seq in range(1, args.count + 1):
        payload = json.dumps({"device": "motion1", "motion": args.distance < 100,
                              "distance_cm": args.distance, "seq": seq}, separators=(",", ":"))
        client.publish("facility/motion1/reading", payload).wait_for_publish(3)
        print("motion ->", payload)
        time.sleep(args.interval)
    client.disconnect()
    client.loop_stop()


def run_lock(args):
    """Same behaviour as esp32/lock-actuator (minus the LEDs and the buzzer)."""
    st = {"unlocked": False, "alarm": False, "unlock_at": 0.0, "alarm_at": 0.0, "seq": 0}
    will = ("facility/lock1/state",
            '{"device":"lock1","state":"offline","alarm":false,"reason":"lwt"}', 1, True)
    client = connect("lock1", "lock-actuator-sim", args.host, args.port, will)

    def publish(reason):
        st["seq"] += 1
        payload = json.dumps({"device": "lock1", "state": "unlocked" if st["unlocked"] else "locked",
                              "alarm": st["alarm"], "reason": reason, "seq": st["seq"]},
                             separators=(",", ":"))
        client.publish("facility/lock1/state", payload, retain=True)
        print("state  ->", payload)

    def on_message(c, u, m):
        text = m.payload.decode("utf-8", "replace")[:64]
        try:
            cmd = json.loads(text).get("cmd", "") if text.lstrip().startswith("{") else text
        except (ValueError, AttributeError):
            cmd = ""
        cmd = str(cmd).strip().lower()
        print("cmd    <-", text)
        if cmd == "lock":
            st["unlocked"] = False; publish("cmd:lock")
        elif cmd == "unlock":
            if st["alarm"]:
                publish("rejected:alarm-active")
            else:
                st.update(unlocked=True, unlock_at=time.time()); publish("cmd:unlock")
        elif cmd == "alarm":
            st.update(unlocked=False, alarm=True, alarm_at=time.time()); publish("cmd:alarm")
        elif cmd == "alarm_off":
            st["alarm"] = False; publish("cmd:alarm_off")
        else:
            publish("rejected:unknown-cmd")

    client.on_message = on_message
    client.subscribe("facility/lock1/cmd", qos=1)
    time.sleep(1)  # like the firmware: ignore anything delivered right after subscribing
    publish("connect")
    try:
        while True:
            time.sleep(0.2)
            if st["unlocked"] and time.time() - st["unlock_at"] >= 5:
                st["unlocked"] = False; publish("auto-relock")
            if st["alarm"] and time.time() - st["alarm_at"] >= 30:
                st["alarm"] = False; publish("alarm-timeout")
    except KeyboardInterrupt:
        client.disconnect()
        client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    sub = parser.add_subparsers(dest="device", required=True)
    b = sub.add_parser("badge"); b.add_argument("--id", default="B-102")
    b.add_argument("--count", type=int, default=1); b.add_argument("--interval", type=float, default=0.5)
    b.set_defaults(func=run_badge)
    m = sub.add_parser("motion"); m.add_argument("--distance", type=float, default=250.0)
    m.add_argument("--count", type=int, default=1); m.add_argument("--interval", type=float, default=1.0)
    m.set_defaults(func=run_motion)
    sub.add_parser("lock").set_defaults(func=run_lock)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
