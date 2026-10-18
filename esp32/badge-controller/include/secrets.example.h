// Copy to secrets.h (git-ignored) and fill in. Never commit secrets.h.
#pragma once

// bore assigns a NEW port every time it restarts: update MQTT_PORT, rebuild.
#define MQTT_HOST "bore.pub"
#define MQTT_PORT 12345

#define MQTT_USER "badge1"
#define MQTT_PASS "change-me"
