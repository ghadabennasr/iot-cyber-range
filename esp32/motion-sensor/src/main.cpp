// Motion Sensor (ESP32 #2)
// HC-SR04 ultrasonic sensor (TRIG GPIO26, ECHO GPIO27) simulates a motion
// detector: an object closer than MOTION_THRESHOLD_CM means "motion".
// LED on GPIO2 mirrors the motion state.
//
// Publishes JSON to facility/motion1/reading, e.g.
//   {"device":"motion1","motion":true,"distance_cm":42.5,"seq":17}
// The field names (device, distance_cm, seq) are what the IDS in Lab/monitor.py
// inspects: distance_cm outside 1..1000 or a repeated/jumping seq raises alerts,
// so this firmware never publishes a failed measurement as 0.
//
// Also publishes a retained presence message on facility/motion1/status; the
// broker publishes the "offline" version for us (Last Will) if we drop off.
//
// NOTE (real hardware): the HC-SR04 ECHO pin is 5 V; use a voltage divider.
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "secrets.h"

static const int TRIG_PIN = 26;
static const int ECHO_PIN = 27;
static const int LED_PIN = 2;

static const char *CLIENT_ID = "motion-sensor";
static const char *TOPIC_READING = "facility/motion1/reading";
static const char *TOPIC_STATUS = "facility/motion1/status";
static const char *STATUS_ONLINE = "{\"device\":\"motion1\",\"status\":\"online\"}";
static const char *STATUS_OFFLINE = "{\"device\":\"motion1\",\"status\":\"offline\"}";

// Sensor behaviour
static const float MOTION_THRESHOLD_CM = 100.0f;  // closer than this = motion
static const float MIN_VALID_CM = 2.0f;           // HC-SR04 datasheet range
static const float MAX_VALID_CM = 400.0f;
static const unsigned long ECHO_TIMEOUT_US = 30000UL;  // ~5 m round trip
static const unsigned long SAMPLE_MS = 200;
static const unsigned long HEARTBEAT_MS = 5000;   // publish even if nothing changed
static const unsigned long MIN_PUBLISH_GAP_MS = 250;  // never flood the broker
static const int CONFIRM_SAMPLES = 2;             // debounce motion transitions

// Connectivity
static const unsigned long WIFI_RETRY_MS = 5000;
static const unsigned long MQTT_RETRY_MIN_MS = 3000;
static const unsigned long MQTT_RETRY_MAX_MS = 30000;

WiFiClient espClient;
PubSubClient mqttClient(espClient);

static unsigned long sequence = 0;
static unsigned long lastSample = 0;
static unsigned long lastPublish = 0;
static unsigned long lastWifiAttempt = 0;
static unsigned long lastMqttAttempt = 0;
static unsigned long mqttRetryMs = MQTT_RETRY_MIN_MS;

static bool motionState = false;
static int pendingCount = 0;
static float lastDistance = -1.0f;
static bool sendNow = true;  // publish one reading right after (re)connecting

// Returns distance in cm, or a negative value if the measurement failed.
static float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration = pulseIn(ECHO_PIN, HIGH, ECHO_TIMEOUT_US);
  if (duration == 0) return -1.0f;  // timeout: nothing echoed back
  float cm = duration * 0.0343f / 2.0f;
  if (cm < MIN_VALID_CM || cm > MAX_VALID_CM) return -1.0f;
  return cm;
}

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
  // Last Will: QoS1, retained, published by the broker if we vanish.
  if (mqttClient.connect(CLIENT_ID, MQTT_USER, MQTT_PASS, TOPIC_STATUS, 1, true,
                         STATUS_OFFLINE)) {
    Serial.println("MQTT connected");
    mqttClient.publish(TOPIC_STATUS, STATUS_ONLINE, true);
    sendNow = true;
    return true;
  }
  Serial.printf("MQTT failed, state=%d\n", mqttClient.state());
  return false;
}

static void publishReading(float distance) {
  char payload[112];
  snprintf(payload, sizeof(payload),
           "{\"device\":\"motion1\",\"motion\":%s,\"distance_cm\":%.1f,\"seq\":%lu}",
           motionState ? "true" : "false", distance, sequence + 1);
  if (mqttClient.publish(TOPIC_READING, payload)) {
    sequence++;  // only consume a sequence number when the publish succeeded
    sendNow = false;
    lastPublish = millis();
    Serial.printf("Published: %s\n", payload);
  } else {
    Serial.println("Publish failed");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.println("Motion Sensor starting...");

  connectWifiBlocking();
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setKeepAlive(30);
  mqttClient.setSocketTimeout(5);
  lastMqttAttempt = millis() - mqttRetryMs;  // first connection attempt right away
}

void loop() {
  unsigned long now = millis();

  // --- connectivity: never block the sampling loop for long ---
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
        // exponential backoff: the free bore.pub relay can be flaky
        mqttRetryMs = min(mqttRetryMs * 2, MQTT_RETRY_MAX_MS);
      }
    }
  } else {
    mqttClient.loop();
  }

  // --- sampling ---
  if (now - lastSample < SAMPLE_MS) return;
  lastSample = now;

  float distance = readDistanceCm();
  if (distance < 0) {
    Serial.println("Measurement failed/out of range: skipped (not published)");
    return;
  }
  lastDistance = distance;

  bool detected = distance < MOTION_THRESHOLD_CM;
  bool changed = false;
  if (detected != motionState) {
    if (++pendingCount >= CONFIRM_SAMPLES) {
      motionState = detected;
      pendingCount = 0;
      changed = true;
      digitalWrite(LED_PIN, motionState ? HIGH : LOW);
    }
  } else {
    pendingCount = 0;
  }

  bool heartbeatDue = (now - lastPublish >= HEARTBEAT_MS);
  bool gapOk = (now - lastPublish >= MIN_PUBLISH_GAP_MS);
  if ((changed || heartbeatDue || sendNow) && gapOk) {
    if (mqttClient.connected()) {
      publishReading(distance);
    } else {
      Serial.println("Reading dropped: MQTT not connected");
    }
  }
}
