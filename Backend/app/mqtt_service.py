"""MQTT side of the backend: one read-only ingest client, one command publisher.

Two separate broker accounts keep least privilege intact (see Broker/acl.conf):
  * `backend`    may only READ facility/#            -> ingestion
  * `controller` may only WRITE facility/lock1/cmd   -> lock/alarm commands
"""
import json
import logging

import paho.mqtt.client as mqtt

from . import config, db

log = logging.getLogger("backend.mqtt")
COMMANDS = ("LOCK", "UNLOCK", "ALARM", "ALARM_OFF")


class MqttService:
    def __init__(self):
        self.processor = None
        self.ingest_connected = False
        self.controller_connected = False
        self._ingest = None
        self._controller = None

    def _client(self, client_id, username, password):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv311
        )
        client.username_pw_set(username, password)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def start(self):
        if config.BACKEND_MQTT_PASSWORD:
            self._ingest = self._client(
                "cyberrange-backend", config.BACKEND_MQTT_USER, config.BACKEND_MQTT_PASSWORD
            )
            self._ingest.on_connect = self._on_ingest_connect
            self._ingest.on_disconnect = self._on_ingest_disconnect
            self._ingest.on_message = self._on_message
            self._ingest.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
            self._ingest.loop_start()
        else:
            log.error("BACKEND_MQTT_PASSWORD is empty: MQTT ingestion is DISABLED")

        if config.CONTROLLER_MQTT_PASSWORD:
            self._controller = self._client(
                "cyberrange-controller", config.CONTROLLER_MQTT_USER, config.CONTROLLER_MQTT_PASSWORD
            )
            self._controller.on_connect = self._on_controller_connect
            self._controller.on_disconnect = self._on_controller_disconnect
            self._controller.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
            self._controller.loop_start()
        else:
            log.warning("CONTROLLER_MQTT_PASSWORD is empty: lock commands will be journaled only")

    def stop(self):
        for client in (self._ingest, self._controller):
            if client is not None:
                client.disconnect()
                client.loop_stop()

    # ---- ingest callbacks
    def _on_ingest_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("Ingest connection rejected by broker: %s", reason_code)
            return
        self.ingest_connected = True
        client.subscribe("facility/#", qos=1)
        log.info("Ingest connected, subscribed to facility/#")

    def _on_ingest_disconnect(self, client, userdata, flags, reason_code, properties):
        self.ingest_connected = False
        log.warning("Ingest disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message):
        try:
            self.processor.handle_message(
                message.topic, message.payload, message.qos, bool(message.retain)
            )
        except Exception:  # one bad message must never kill the ingest thread
            log.exception("Failed to process message on %s", message.topic)

    # ---- controller callbacks
    def _on_controller_connect(self, client, userdata, flags, reason_code, properties):
        self.controller_connected = not reason_code.is_failure
        if reason_code.is_failure:
            log.error("Controller connection rejected by broker: %s", reason_code)
        else:
            log.info("Controller connected")

    def _on_controller_disconnect(self, client, userdata, flags, reason_code, properties):
        self.controller_connected = False
        log.warning("Controller disconnected: %s", reason_code)

    # ---- commands
    def send(self, cmd: str, reason: str | None, issued_by: str) -> dict:
        """Journal the command, then publish it as the `controller` account (never retained)."""
        if cmd not in COMMANDS:
            raise ValueError(f"unknown command {cmd!r}")
        command_id = db.record_command(cmd, reason, issued_by)
        error = None
        if self._controller is None or not self.controller_connected:
            error = "controller not connected"
        else:
            try:
                # The lock firmware limits commands to 64 bytes: keep the payload minimal.
                info = self._controller.publish(
                    config.LOCK_CMD_TOPIC, json.dumps({"cmd": cmd}, separators=(",", ":")),
                    qos=1, retain=False,
                )
                info.wait_for_publish(timeout=3)
                if not info.is_published():
                    error = "publish not acknowledged in time"
            except (ValueError, RuntimeError) as exc:
                error = f"publish failed: {exc}"
        db.finish_command(command_id, error is None, error)
        log.info("Command %s (%s) -> %s", cmd, reason, "sent" if error is None else error)
        return {"id": command_id, "cmd": cmd, "published": error is None, "error": error}
