# IoT Cyber Range

A virtual IoT security lab: 3 simulated ESP32 devices (Wokwi) representing a
facility access-control system, communicating over MQTT, with a full
attack → detection → alert → response pipeline built around Docker.

## Concept

Three IoT devices simulate a small facility access-control system:

- **ESP32 #1 — Badge/RFID controller**: publishes access events to
  `facility/badge1/access`
- **ESP32 #2 — Motion sensor**: publishes alerts to `facility/zone2/motion`
- **ESP32 #3 — Lock/siren actuator**: subscribes to commands on
  `facility/lock3/cmd` to lock/unlock or trigger an alarm

All devices communicate over MQTT through a Dockerized Mosquitto broker.

On top of this IoT layer, we build a security layer:
- A controlled **attacker** environment running scripted attacks (recon,
  unauthorized MQTT subscriptions, forged actuator commands, sensor spoofing)
- A **honeypot**: a fake exposed service designed to attract and log
  unauthorized interaction
- A **detection** service: custom rule-based engine watching MQTT traffic
  for suspicious patterns
- A **logging** pipeline storing all events in a database
- A **dashboard** showing live events/alerts (source, target, timestamp,
  event type) for investigation
- An optional **response** action (blocking/isolating a malicious source)

Full pipeline demonstrated: IoT devices → attack → monitoring → detection →
logging → alert → investigation → optional response.

## Architecture

[Attacker container] --> [MQTT Broker (Mosquitto, Docker, tunneled via ngrok)]
^ ^ ^
[ESP32 #1] [ESP32 #2] [ESP32 #3] (Wokwi)
|
v
[Backend (MQTT subscriber + API)]
| | |
[Database] [Detection/IDS] [Honeypot]
|
[Dashboard]


## Components

- **esp32/** — Wokwi projects, one folder per device (`badge-controller/`,
  `motion-sensor/`, `lock-actuator/`)
- **broker/** — `mosquitto.conf` and `acl.conf`
- **backend/** — MQTT subscriber + API (not started yet)
- **docker-compose.yml** — orchestrates broker, backend, DB, IDS, honeypot,
  dashboard on a shared Docker network

## Tech stack

- ESP32 simulation: Wokwi
- MQTT broker: Eclipse Mosquitto (Dockerized)
- Backend: Python (FastAPI) or Node.js
- Database: PostgreSQL
- Detection: custom rule-based microservice
- Honeypot: custom fake service
- Dashboard: React or plain HTML/JS + WebSocket/polling
- Attacker tooling: Python (paho-mqtt, scapy, python-nmap) / CLI (nmap,
  mosquitto_pub, hydra)

## Known technical constraint

Wokwi's simulated ESP32 network is not on the same LAN as our Docker
containers — it needs a real, reachable broker address. We expose the
Dockerized Mosquitto broker via an ngrok TCP tunnel so Wokwi firmware can
connect to it over the internet. Free ngrok URLs change on every restart —
update the address in the ESP32 sketch each session.

## Setup

1. Clone the repo
2. `broker/mosquitto.conf` configures the listener (port 1883) and points to
   `broker/acl.conf` for per-device topic permissions
3. Run `docker compose up` to start the Mosquitto broker
4. Run `ngrok tcp 1883` to expose the broker publicly; note the forwarding
   address it prints
5. Open the relevant project in `esp32/` on Wokwi and set the ngrok
   address/port as the MQTT broker host in the sketch
6. (Backend/dashboard setup — to be added)

## Status

- [x] Repo structure created
- [x] `mosquitto.conf` written (listener 1883, ACL file referenced)
- [x] `acl.conf` written (per-device topic restrictions)
- [x] Mosquitto added to `docker-compose.yml` + tested locally (pub/sub confirmed)
- [ ] ngrok tunnel set up and externally tested
- [ ] First ESP32 (badge-controller) publishing to broker
- [ ] Remaining 2 ESP32 firmwares
- [ ] Backend event ingestion
- [ ] Attacker container + first attack scenario (recon)
- [ ] Detection rule #1 (recon)
- [ ] Command-injection attack + detection rule #2
- [ ] Honeypot built + integrated into logging
- [ ] Sensor-spoofing scenario + detection rule #3
- [ ] Lateral-movement correlation (honeypot -> broker)
- [ ] Basic dashboard (live event feed + alerts list)
- [ ] Response action (block/isolate source)
- [ ] Replace allow_anonymous with real auth
- [ ] End-to-end integration test
- [ ] Demo rehearsal
- [ ] Report written

See `notes.md` for the detailed, dated build log.

## Known issues / tech debt

- `allow_anonymous true` is currently set in `mosquitto.conf` for
  development — must be replaced with real per-client authentication
  (username/password or client certs) before the final demo, or explicitly
  justified in the report as a documented development-mode limitation.
- ACL rules are written but not yet enforceable/testable without real
  client authentication.

## Team

- **Arij** — Device & Data lead: ESP32 firmware, MQTT topic
  design/ACLs, backend event ingestion, database schema, recon +
  command-injection detection rules
- **Ghada** — Attack & Defense lead: attacker scripts, honeypot,
  spoofing + lateral-movement detection rules, dashboard/alerting UI

## Timeline (6 weeks)

- Week 1: repo + broker + first ESP32 connectivity
- Week 2: all 3 ESP32 firmwares + backend ingesting events
- Week 3: attacker container + recon attack + first detection rule + basic dashboard
- Week 4: command-injection attack + honeypot
- Week 5: sensor-spoofing scenario + anomaly rule + lateral-movement correlation
- Week 6: response action, polish, demo rehearsal, buffer