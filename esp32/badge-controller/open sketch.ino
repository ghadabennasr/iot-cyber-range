#include <WiFi.h>
#include <PubSubClient.h>

// Wi-Fi
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASSWORD = "";

// MQTT through Bore
const char* MQTT_SERVER = "bore.pub";
const int MQTT_PORT = 41459;

// MQTT credentials
const char* MQTT_USER = "badge1";
const char* MQTT_PASSWORD = "bgahdagdea";

const int BUTTON_PIN = 4;
const int LED_PIN = 2;

WiFiClient espClient;
PubSubClient mqttClient(espClient);

void setup() {
  Serial.begin(115200);

  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);

  digitalWrite(LED_PIN, LOW);

  Serial.println("Badge Controller starting...");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to WiFi");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi connected!");

  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
}

void loop() {
  mqttClient.loop();

  if (!mqttClient.connected()) {
    connectMQTT();
  }

  if (digitalRead(BUTTON_PIN) == LOW) {
    digitalWrite(LED_PIN, HIGH);
    mqttClient.publish("facility/badge1/access", "Badge detected");
    Serial.println("Badge detected!");
    delay(1000);
    digitalWrite(LED_PIN, LOW);
  }
}

void connectMQTT() {
  Serial.print("Connecting to MQTT at ");
  Serial.print(MQTT_SERVER);
  Serial.print(":");
  Serial.println(MQTT_PORT);

  if (mqttClient.connect("badge-controller", MQTT_USER, MQTT_PASSWORD)) {
    Serial.println("MQTT connected!");
  } else {
    Serial.print("MQTT failed, state=");
    Serial.println(mqttClient.state());
    delay(3000);
  }
}