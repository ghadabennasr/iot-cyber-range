# Build Log — IoT Cyber Range

This file tracks every development step chronologically, with screenshots
as evidence, stored in `screenshots/`.

---

## 2026-09-27 — Step 1: Repository structure

**What was done:**
Created the base repo structure to organize the project from day one:

```
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
```

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

**Note:** at this point the rules were written but could not be fully
trusted, since `allow_anonymous true` is still active in the broker config —
see Known Issues.

**Next:** add Mosquitto as a service in `docker-compose.yml` and test it.

---

## 2026-09-XX — Step 3: Mosquitto running in Docker
<!-- TODO: replace XX with the real date -->

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

**Next:** expose the broker publicly so Wokwi's simulated ESP32 devices can
reach it (ngrok first, then replaced — see Step 4).

---

## 2026-09-XX — Step 4: Public tunnel via bore.pub (ngrok alternative)
<!-- TODO: replace XX with the real date -->

**Context:** ngrok's free tier now requires a credit/debit card to open TCP
tunnels (ERR_NGROK_8013). Since the project must remain 100% free with no
payment info, we switched to bore (https://github.com/ekzhang/bore), an
open-source TCP tunnel client using the public bore.pub relay server.

**What was done:**
- Downloaded bore v0.6.0 prebuilt Windows binary from the official GitHub
  releases page, extracted `bore.exe`
- Ran `bore local 1883 --to bore.pub` with the Dockerized Mosquitto broker
  already running; bore assigned a public endpoint at `bore.pub:<PORT>`

![Figure 8](screenshots/Figure%208%20—%20bore%20tunnel%20running.png)
*Figure 8 — bore tunnel running, listening at bore.pub:<PORT>*

**What was tested:**
- Confirmed raw MQTT pub/sub across the tunnel (external `mosquitto_pub` ->
  bore.pub -> local Dockerized broker -> local `mosquitto_sub`)

![Figure 9](screenshots/Figure%209%20—%20MQTT%20pub-sub%20through%20bore%20tunnel.png)
*Figure 9 — successful MQTT communication through the bore tunnel*

- Confirmed MQTT authentication works through the tunnel (authenticated
  client publish succeeded)

![Figure 10](screenshots/Figure%2010%20—%20authenticated%20publish%20through%20bore.png)
*Figure 10 — authenticated MQTT publish accepted through bore.pub*

- Confirmed ACL restrictions still apply through the tunnel: an
  unauthorized publish attempt to a restricted topic was correctly
  rejected, proving the security layer survives tunneling, not just local
  connections

![Figure 11](screenshots/Figure%2011%20—%20ACL%20block%20through%20bore%20tunnel.png)
*Figure 11 — ACL correctly blocking an unauthorized publish through the tunnel*

**Important limitation:** bore's documentation states forwarded traffic is
NOT encrypted by default (the shared secret, if used, only protects the
initial handshake). This tunnel is used strictly as a development /
university demo mechanism to let Wokwi's cloud-simulated ESP32 reach our
local broker, not as production-secure MQTT transport. Documented here for
the report's security assessment section.

**Known caveats:**
- bore.pub assigns a random port on each restart — must be updated in the
  Wokwi sketches each session.
- The free relay has shown intermittent instability; firmware must
  reconnect automatically.

**Next:** ESP32 firmwares (badge-controller in progress by teammate;
motion-sensor and lock-actuator by Arij).

---

## 2026-10-03 — Step 5: ESP32 #2 motion-sensor firmware (written, untested)

**What was done:**
- Wrote `esp32/motion-sensor/sketch.ino`, `diagram.json`, `libraries.txt`
  (Wokwi project: ESP32 DevKit + PIR sensor on GPIO 13)
- Publishes motion events to `facility/zone2/motion` and online/offline
  status (retained, with Last Will) to `facility/zone2/status` — both inside
  the `facility/zone2/#` publish permission from the ACL
- Automatic WiFi + MQTT reconnect with backoff (bore.pub instability)
- Connects with username `motion-sensor` so the ACL is keyed on the
  authenticated user

**Next:** test in Wokwi against the bore tunnel (screenshot as Figure 12),
then write the lock-actuator firmware.

---

## Known issues / tech debt

- [ ] `allow_anonymous true` is still enabled in `mosquitto.conf` for
      development convenience. Authenticated clients work (Figure 10), but
      anonymous clients are still accepted. Must be replaced with mandatory
      per-client authentication before the final demo — or explicitly
      documented in the report as a deliberate, time-boxed
      development-mode limitation.
- [x] ACL rules (Figure 3) validated at topic level, locally (Figure 7) and
      through the tunnel (Figure 11).
- [ ] Confirm the ACL is keyed on the authenticated **username**, not on the
      client ID (`badge1` in Figure 7). Client IDs are client-chosen and can
      be spoofed; in Mosquitto, ACL `user` blocks match the username. Re-run
      the test with explicit credentials.
- [ ] Once `allow_anonymous false` is set, re-test that an anonymous client
      is rejected and that ACLs still hold.
- [ ] Tunnel traffic is unencrypted (bore) — report limitation.

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
| 8 | bore tunnel running |
| 9 | MQTT pub/sub through bore tunnel |
| 10 | Authenticated publish through bore |
| 11 | ACL block through bore tunnel |
