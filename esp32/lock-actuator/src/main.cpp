// Lock / Siren Actuator (ESP32 #3)
// Subscribes to facility/lock1/cmd and publishes its state to
// facility/lock1/state (the only topics the `lock1` account may use, see
// Broker/acl.conf).
//
// Simulated hardware (Wokwi):
//   GPIO25  red LED    = door LOCKED   (blinks during an alarm)
//   GPIO26  green LED  = door UNLOCKED
//   GPIO27  buzzer     = siren (1 kHz square wave while the alarm is active)
//
// Commands (case-insensitive; plain text or JSON {"cmd":"UNLOCK",...}):
//   LOCK       lock the door
//   UNLOCK     unlock for UNLOCK_HOLD_MS, then re-lock automatically
//   ALARM      lock the door and sound the siren (stops by itself after ALARM_MAX_MS)
//   ALARM_OFF  silence the siren
//
// Safety / security choices:
//   * fail-secure: boots LOCKED, and the auto re-lock and alarm timeout run on
//     local timers, so they do not depend on the network;
//   * UNLOCK is refused while the alarm is active (needs ALARM_OFF first);
//   * unknown commands and oversized payloads are rejected, never executed;
//   * messages arriving right after (re)subscribing are ignored, so a retained
//     or stale command cannot replay an UNLOCK after a reconnect.
//
// State JSON (retained):
//   {"device":"lock1","state":"locked","alarm":false,"reason":"cmd:lock","seq":4}
// The broker publishes state "offline" for us (Last Will) if we drop off.
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "secrets.h"

static const int LED_LOCKED_PIN = 25;
static const int LED_UNLOCKED_PIN = 26;
static const int BUZZER_PIN = 27;

static const char *CLIENT_ID = "lock-actuator";
static const char *TOPIC_CMD = "facility/lock1/cmd";
static const char *TOPIC_STATE = "facility/lock1/state";
static const char *STATE_OFFLINE =
    "{\"device\":\"lock1\",\"state\":\"offline\",\"alarm\":false,\"reason\":\"lwt\"}";

static const unsigned long UNLOCK_HOLD_MS = 5000;
static const unsigned long ALARM_MAX_MS = 30000;
static const unsigned long HEARTBEAT_MS = 15000;
static const unsigned long STARTUP_IGNORE_MS = 1000;
static const unsigned long REJECT_PUBLISH_GAP_MS = 2000;
static const unsigned int MAX_CMD_LEN = 64;
static const unsigned long SIREN_HALF_PERIOD_US = 500;  // 1 kHz
static const unsigned long ALARM_BLINK_MS = 250;

static const unsigned long WIFI_RETRY_MS = 5000;
static const unsigned long MQTT_RETRY_MIN_MS = 3000;
static const unsigned long MQTT_RETRY_MAX_MS = 30000;

enum Command { CMD_NONE, CMD_LOCK, CMD_UNLOCK, CMD_ALARM, CMD_ALARM_OFF };

WiFiClient espClient;
PubSubClient mqttClient(espClient);

// Actuator state
static bool unlocked = false;  // fail-secure default
static bool alarmActive = false;
static unsigned long unlockedAt = 0;
static unsigned long alarmStartedAt = 0;

// Publishing / connectivity
static unsigned long sequence = 0;
static unsigned long lastStatePublish = 0;
static unsigned long lastRejectPublish = 0;
static unsigned long ignoreCmdUntil = 0;
static const char *pendingReason = nullptr;  // set by callback, published from loop()
static unsigned long lastWifiAttempt = 0;
static unsigned long lastMqttAttempt = 0;
static unsigned long mqttRetryMs = MQTT_RETRY_MIN_MS;

// Output helpers
static unsigned long lastSirenToggle = 0;
static bool sirenLevel = false;
static unsigned long lastBlink = 0;
static bool blinkLevel = false;

static void updateLeds() {
  if (alarmActive) return;  // handled by the blink logic in loop()
  digitalWrite(LED_LOCKED_PIN, unlocked ? LOW : HIGH);
  digitalWrite(LED_UNLOCKED_PIN, unlocked ? HIGH : LOW);
}

static void stopSiren() {
  sirenLevel = false;
  digitalWrite(BUZZER_PIN, LOW);
}

static void publishState(const char *reason) {
  if (!mqttClient.connected()) return;
  char payload[128];
  snprintf(payload, sizeof(payload),
           "{\"device\":\"lock1\",\"state\":\"%s\",\"alarm\":%s,\"reason\":\"%s\",\"seq\":%lu}",
           unlocked ? "unlocked" : "locked", alarmActive ? "true" : "false", reason,
           sequence + 1);
  if (mqttClient.publish(TOPIC_STATE, payload, true)) {  // retained
    sequence++;
    lastStatePublish = millis();
    Serial.printf("State: %s\n", payload);
  } else {
    Serial.println("State publish failed");
  }
}

static void queuePublish(const char *reason) { pendingReason = reason; }

static void queueReject(const char *reason) {
  unsigned long now = millis();
  if (now - lastRejectPublish >= REJECT_PUBLISH_GAP_MS) {  // do not become an echo amplifier
    lastRejectPublish = now;
    queuePublish(reason);
  }
}

// ---- command parsing -------------------------------------------------------

// Copies the value of "cmd" from a flat JSON object into out. No JSON library
// needed: we only ever look for one short string field.
static bool extractCmdField(const char *json, char *out, size_t outSize) {
  const char *key = strstr(json, "\"cmd\"");
  if (!key) return false;
  const char *p = key + 5;
  while (*p == ' ' || *p == ':') p++;
  if (*p != '"') return false;
  p++;
  size_t i = 0;
  while (*p && *p != '"' && i < outSize - 1) out[i++] = *p++;
  if (*p != '"') return false;  // unterminated / too long
  out[i] = '\0';
  return true;
}

static Command parseCommand(const char *text) {
  char word[24] = {0};
  while (*text == ' ' || *text == '\t') text++;
  if (*text == '{') {
    if (!extractCmdField(text, word, sizeof(word))) return CMD_NONE;
  } else {
    size_t i = 0;
    while (text[i] && text[i] != ' ' && text[i] != '\t' && text[i] != '\r' &&
           text[i] != '\n' && i < sizeof(word) - 1) {
      word[i] = text[i];
      i++;
    }
    word[i] = '\0';
  }
  for (char *c = word; *c; c++) *c = tolower((unsigned char)*c);

  if (strcmp(word, "lock") == 0) return CMD_LOCK;
  if (strcmp(word, "unlock") == 0) return CMD_UNLOCK;
  if (strcmp(word, "alarm") == 0) return CMD_ALARM;
  if (strcmp(word, "alarm_off") == 0) return CMD_ALARM_OFF;
  return CMD_NONE;
}

static void applyCommand(Command cmd) {
  unsigned long now = millis();
  switch (cmd) {
    case CMD_LOCK:
      unlocked = false;
      updateLeds();
      queuePublish("cmd:lock");
      break;
    case CMD_UNLOCK:
      if (alarmActive) {
        Serial.println("UNLOCK refused: alarm active");
        queueReject("rejected:alarm-active");
      } else {
        unlocked = true;
        unlockedAt = now;
        updateLeds();
        queuePublish("cmd:unlock");
      }
      break;
    case CMD_ALARM:
      unlocked = false;  // an alarm always secures the door
      alarmActive = true;
      alarmStartedAt = now;
      queuePublish("cmd:alarm");
      break;
    case CMD_ALARM_OFF:
      alarmActive = false;
      stopSiren();
      updateLeds();
      queuePublish("cmd:alarm_off");
      break;
    default:
      Serial.println("Command rejected: unknown");
      queueReject("rejected:unknown-cmd");
      break;
  }
}

static void onMessage(char *topic, byte *payload, unsigned int length) {
  if (strcmp(topic, TOPIC_CMD) != 0) return;
  if (millis() < ignoreCmdUntil) {
    Serial.println("Command ignored (right after subscribe: possible stale/retained)");
    return;
  }
  if (length == 0 || length > MAX_CMD_LEN) {
    Serial.printf("Command rejected: bad length %u\n", length);
    queueReject("rejected:bad-length");
    return;
  }
  char buffer[MAX_CMD_LEN + 1];
  memcpy(buffer, payload, length);
  buffer[length] = '\0';
  Serial.printf("Command received: %s\n", buffer);
  applyCommand(parseCommand(buffer));
}

// ---- connectivity ----------------------------------------------------------

static void connectWifiBlocking() {
  Serial.print("Connecting to WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin("Wokwi-GUEST", "", 6);  // channel 6 = faster connect in Wokwi
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? "\nWiFi connected" : "\nWiFi not ready yet");
}

static bool connectMqtt() {
  Serial.printf("Connecting to MQTT at %s:%d\n", MQTT_HOST, MQTT_PORT);
  if (mqttClient.connect(CLIENT_ID, MQTT_USER, MQTT_PASS, TOPIC_STATE, 1, true,
                         STATE_OFFLINE)) {
    Serial.println("MQTT connected");
    ignoreCmdUntil = millis() + STARTUP_IGNORE_MS;
    mqttClient.subscribe(TOPIC_CMD, 1);
    queuePublish("connect");
    return true;
  }
  Serial.printf("MQTT failed, state=%d\n", mqttClient.state());
  return false;
}

// ---- Arduino entry points --------------------------------------------------

void setup() {
  Serial.begin(115200);
  pinMode(LED_LOCKED_PIN, OUTPUT);
  pinMode(LED_UNLOCKED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  stopSiren();
  updateLeds();  // boots LOCKED
  Serial.println("Lock Actuator starting (LOCKED)...");

  connectWifiBlocking();
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(onMessage);
  mqttClient.setKeepAlive(30);
  mqttClient.setSocketTimeout(5);
  lastMqttAttempt = millis() - mqttRetryMs;  // first connection attempt right away
}

void loop() {
  unsigned long now = millis();

  // --- connectivity ---
  if (WiFi.status() != WL_CONNECTED) {
    if (now - lastWifiAttempt >= WIFI_RETRY_MS) {
      lastWifiAttempt = now;
      WiFi.begin("Wokwi-GUEST", "", 6);
    }
  } else if (!mqttClient.connected()) {
    if (now - lastMqttAttempt >= mqttRetryMs) {
      lastMqttAttempt = now;
      if (connectMqtt()) {
        mqttRetryMs = MQTT_RETRY_MIN_MS;
      } else {
        mqttRetryMs = min(mqttRetryMs * 2, MQTT_RETRY_MAX_MS);
      }
    }
  } else {
    mqttClient.loop();
  }

  // --- local safety timers (work even without network) ---
  if (unlocked && now - unlockedAt >= UNLOCK_HOLD_MS) {
    unlocked = false;
    updateLeds();
    queuePublish("auto-relock");
  }
  if (alarmActive && now - alarmStartedAt >= ALARM_MAX_MS) {
    alarmActive = false;
    stopSiren();
    updateLeds();
    queuePublish("alarm-timeout");
  }

  // --- siren + blinking red LED during an alarm ---
  if (alarmActive) {
    unsigned long us = micros();
    if (us - lastSirenToggle >= SIREN_HALF_PERIOD_US) {
      lastSirenToggle = us;
      sirenLevel = !sirenLevel;
      digitalWrite(BUZZER_PIN, sirenLevel);
    }
    if (now - lastBlink >= ALARM_BLINK_MS) {
      lastBlink = now;
      blinkLevel = !blinkLevel;
      digitalWrite(LED_LOCKED_PIN, blinkLevel);
      digitalWrite(LED_UNLOCKED_PIN, LOW);
    }
  }

  // --- state publishing (from loop context, never from the MQTT callback) ---
  if (pendingReason) {
    const char *reason = pendingReason;
    pendingReason = nullptr;
    publishState(reason);
  } else if (now - lastStatePublish >= HEARTBEAT_MS) {
    publishState("heartbeat");
  }
}
