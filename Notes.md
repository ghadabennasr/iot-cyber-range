# Build Log — IoT Cyber Range

This file tracks every development step chronologically, with screenshots
as evidence, stored in `screenshots/`.

---

## 2026-09-27 — Step 1: Repository structure

**What was done:**
Created the base repo structure to organize the project from day one:

iot-cyber-range/
esp32/
badge-controller/
motion-sensor/
lock-actuator/
broker/
backend/
screenshots/
docker-compose.yml
README.md
notes.md


![Figure 1](screenshots/Figure%201%20—%20IoT%20Cyber%20Range%20project%20structure.png)
*Figure 1 — IoT Cyber Range project structure*

**Next:** write `mosquitto.conf` and `acl.conf`.

---

## 2026-09-27 — Step 1 (cont.): Mosquitto broker configuration

**What was done:**
Wrote `broker/mosquitto.conf`:
- Listener on port `1883`
- Protocol: `mqtt`
- References `acl.conf` for access control
- `allow_anonymous true` — set temporarily for development, flagged in
  Known Issues below for hardening before the final demo

![Figure 2](screenshots/Figure%202%20—%20Mosquitto%20MQTT%20broker%20configuration.png)
*Figure 2 — Mosquitto MQTT broker configuration*

**Next:** write the ACL rules.

---

## 2026-09-27 — Step 2: MQTT access control (ACL)

**What was done:**
Wrote `broker/acl.conf` defining per-device permissions:
- `badge-controller` (ESP32 #1): publish-only on `facility/badge1/#`
- `motion-sensor` (ESP32 #2): publish-only on `facility/zone2/#`
- `lock-actuator` (ESP32 #3): subscribe-only on `facility/lock3/cmd`
- `backend`: subscribe-only on `facility/#` (wildcard, read-only)

![Figure 3](screenshots/Figure%203%20—%20MQTT%20access%20control%20rules.png)
*Figure 3 — MQTT access control rules*

**Note:** these rules are written but not yet fully enforceable with
confidence since `allow_anonymous true` is still active in the broker
config — see Known Issues.

**Next:** add Mosquitto as a service in `docker-compose.yml` and test it.

---

## 2026-09-27 — Step 3: Mosquitto running in Docker

**What was done:**
- Added a `mosquitto` service to `docker-compose.yml`:
  - `image: eclipse-mosquitto:2`
  - mounted `broker/mosquitto.conf` and `broker/acl.conf` as volumes
  - exposed port `1883:1883`
- Ran `docker compose up` and confirmed the broker starts without errors

![Figure 4](screenshots/Figure%204%20—%20Mosquitto%20broker%20running%20inside%20a%20Docker%20container.png)
*Figure 4 — Mosquitto broker running inside a Docker container*

![Figure 5](screenshots/Figure%205%20—%20Successful%20Mosquitto%20broker%20initialization.png)
*Figure 5 — Successful Mosquitto broker initialization*

**What was tested:**
- Verified pub/sub locally with `mosquitto_pub` / `mosquitto_sub` against
  the Dockerized broker — messages published were correctly received

![Figure 6](screenshots/Figure%206%20—%20Successful%20MQTT%20communication%20between%20a%20device%20and%20the%20backend.png)
*Figure 6 — Successful MQTT communication between a device and the backend*

- Verified ACL enforcement: a client identified as `badge1` was correctly
  **blocked** from publishing to `facility/lock3/cmd` (the lock actuator's
  command topic), confirming per-device topic restrictions are working

![Figure 7](screenshots/Figure%207%20—%20ACL%20preventing%20badge1%20from%20publishing%20to%20the%20lock%20actuator%20topic.png)
*Figure 7 — ACL preventing badge1 from publishing to the lock actuator topic*

**Next:** expose the broker publicly (ngrok tunnel) so Wokwi's simulated
ESP32 devices can reach it, then write the first ESP32 sketch
(badge-controller).

---

## Known issues / tech debt

- [ ] `allow_anonymous true` is currently enabled in `mosquitto.conf` for
      development convenience. This is insecure and must be replaced with
      real per-client authentication (username/password or client
      certificates) before the final demo — or explicitly documented in
      the report as a deliberate, time-boxed development-mode limitation.
- [ ] ACL rules (Figure 3) are written and partially validated (Figure 7
      shows a topic-level block working), but full enforcement has not
      been tested under real client authentication — flag this as a
      dependency for later hardening.
- [ ] Confirm the Figure 7 test used an explicit client ID (`badge1`)
      rather than relying on IP or another incidental factor, to be sure
      the ACL is genuinely keyed on client identity.

## Screenshots reference

| Figure | Description |
|---|---|
| 1 | IoT Cyber Range project structure |
| 2 | Mosquitto MQTT broker configuration |
| 3 | MQTT access control rules |
| 4 | Mosquitto broker running inside a Docker container |
| 5 | Successful Mosquitto broker initialization |
| 6 | Successful MQTT communication between a device and the backend |
| 7 | ACL preventing badge1 from publishing to the lock actuator topic |