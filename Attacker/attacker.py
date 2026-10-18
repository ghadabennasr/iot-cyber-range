import argparse
import os
import socket
import threading
import time

import paho.mqtt.client as mqtt


HONEYPOT_HOST = os.environ.get("HONEYPOT_HOST", "honeypot")
HONEYPOT_PORT = int(os.environ.get("HONEYPOT_PORT", "2323"))
BROKER_HOST = os.environ.get("BROKER_HOST", "mqtt-broker")
BROKER_PORT = int(os.environ.get("BROKER_PORT", "1883"))


def touch_honeypot(request):
    with socket.create_connection((HONEYPOT_HOST, HONEYPOT_PORT), timeout=3) as connection:
        connection.sendall((request[:1024] + "\n").encode())
        connection.shutdown(socket.SHUT_WR)
        return connection.recv(1024).decode("utf-8", errors="replace").strip()


def mqtt_client():
    username = os.environ.get("MQTT_USERNAME")
    password = os.environ.get("MQTT_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "MQTT_USERNAME and MQTT_PASSWORD are required; use Lab\\run-scenario.ps1."
        )
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"range-exercise-{int(time.time())}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(username, password)
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=10)
    client.loop_start()
    return client


def run_recon():
    print("Recon exercise: only the in-range honeypot and MQTT broker are contacted.")
    print(f"Honeypot response: {touch_honeypot('GET / HTTP/1.1')}")
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"range-recon-anonymous-{int(time.time())}",
        protocol=mqtt.MQTTv311,
    )
    connected = threading.Event()
    result = {}

    def on_connect(client, userdata, flags, reason, properties):
        result["reason"] = reason
        connected.set()

    client.on_connect = on_connect
    loop_started = False
    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=10)
        client.loop_start()
        loop_started = True
        if not connected.wait(timeout=3):
            raise RuntimeError("No MQTT connection response during anonymous-access probe.")
        if result["reason"] == 0:
            raise RuntimeError("Broker accepted an anonymous MQTT connection.")
        print(f"Broker rejected anonymous MQTT access ({result['reason']}).")
    finally:
        if client.is_connected():
            client.disconnect()
        if loop_started:
            client.loop_stop()


def run_command_injection():
    probe = "GET /maintenance?cmd=ping%20127.0.0.1%3Bwhoami HTTP/1.1"
    print("Sending harmless command-like text to the fake maintenance honeypot.")
    print("The honeypot records the input and never executes it.")
    print(f"Honeypot response: {touch_honeypot(probe)}")


def run_sensor_spoof():
    client = mqtt_client()
    try:
        payload = (
            '{"device":"motion1","motion":true,'
            '"distance_cm":0,"seq":999999}'
        )
        info = client.publish("facility/motion1/reading", payload, qos=0)
        info.wait_for_publish(timeout=3)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with code {info.rc}.")
        print("Published an out-of-range simulated motion reading; IDS should alert.")
    finally:
        client.disconnect()
        client.loop_stop()


def run_lateral_movement():
    print(f"Honeypot response: {touch_honeypot('GET /admin HTTP/1.1')}")
    client = mqtt_client()
    try:
        client.publish("facility/lock1/cmd", "lock", qos=1).wait_for_publish(timeout=3)
        print("Attempted a badge-account publish to the lock command topic; "
              "the broker ACL should deny it.")
    finally:
        client.disconnect()
        client.loop_stop()


SCENARIOS = {
    "recon": run_recon,
    "command-injection": run_command_injection,
    "sensor-spoof": run_sensor_spoof,
    "lateral-movement": run_lateral_movement,
}


def main():
    parser = argparse.ArgumentParser(
        description="Run a bounded exercise against this CyberRange Compose network."
    )
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    args = parser.parse_args()
    SCENARIOS[args.scenario]()


if __name__ == "__main__":
    main()
