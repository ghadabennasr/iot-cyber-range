// Badge Controller (ESP32 #1)
// Button on GPIO4 simulates a badge swipe; LED on GPIO2 blinks on publish.
// Publishes JSON to facility/badge1/access so the IDS has fields to inspect.
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "secrets.h"

static const int BUTTON_PIN = 4;
static const int LED_PIN = 2;
static const char *TOPIC = "facility/badge1/access";
static const char *BADGE_ID = "B-102";

static const unsigned long DEBOUNCE_MS = 500;
static const unsigned long RECONNECT_MS = 3000;

WiFiClient espClient;
PubSubClient mqttClient(espClient);

static unsigned long lastPress = 0;
static unsigned long lastReconnectAttempt = 0;
static unsigned long sequence = 0;

static void connectWifi() {
  Serial.print("Connecting to WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin("Wokwi-GUEST", "", 6);  // channel 6 = faster connect in Wokwi
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");
}

static bool connectMqtt() {
  Serial.printf("Connecting to MQTT at %s:%d\n", MQTT_HOST, MQTT_PORT);
  if (mqttClient.connect("badge-controller", MQTT_USER, MQTT_PASS)) {
    Serial.println("MQTT connected");
    return true;
  }
  Serial.printf("MQTT failed, state=%d\n", mqttClient.state());
  return false;
}

static void publishBadgeEvent() {
  char payload[96];
  snprintf(payload, sizeof(payload),
           "{\"device\":\"badge1\",\"badge_id\":\"%s\",\"seq\":%lu}",
           BADGE_ID, ++sequence);
  if (mqttClient.publish(TOPIC, payload)) {
    Serial.printf("Published: %s\n", payload);
    digitalWrite(LED_PIN, HIGH);
    delay(200);
    digitalWrite(LED_PIN, LOW);
  } else {
    Serial.println("Publish failed");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.println("Badge Controller starting...");

  connectWifi();
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }

  if (!mqttClient.connected()) {
    unsigned long now = millis();
    if (now - lastReconnectAttempt >= RECONNECT_MS) {
      lastReconnectAttempt = now;
      connectMqtt();
    }
  } else {
    mqttClient.loop();
  }

  if (digitalRead(BUTTON_PIN) == LOW && millis() - lastPress > DEBOUNCE_MS) {
    lastPress = millis();
    if (mqttClient.connected()) {
      publishBadgeEvent();
    } else {
      Serial.println("Badge swipe ignored: MQTT not connected");
    }
  }
}
