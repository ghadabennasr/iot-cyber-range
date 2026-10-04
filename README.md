# IoT Cyber Range

A virtual IoT security lab: 3 simulated ESP32 devices (Wokwi) representing a
facility access-control system, communicating over MQTT, with a full
attack → detection → alert → response pipeline built around Docker.

## Concept

Three IoT devices simulate a small facility access-control system:

- **ESP32 #1 — Badge/RFID controller** (`badge-controller`, completed):
  publishes access events to `facility/badge1/access`
- **ESP32 #2 — Motion sensor** (`motion-sensor`, planned): publishes alerts
  to `facility/zone2/motion`
- **ESP32 #3 — Lock/siren actuator** (`lock-actuator`, planned): subscribes
  to commands on `facility/lock3/cmd` to lock/unlock or trigger an alarm

All devices communicate over MQTT through a Dockerized Mosquitto broker.

On top of this IoT layer, we are building a security layer (planned, not
yet implemented):
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

Full pipeline target: IoT devices → attack → monitoring → detection →
logging → alert → investigation → optional response.

## Architecture

[Attacker container] --> [MQTT Broker (Mosquitto, Docker, tunneled via bore.pub)]
^ ^ ^
[ESP32 #1] [ESP32 #2] [ESP32 #3] (Wokwi)
(done) (planned) (planned)
|
v
[Backend (MQTT subscriber + API)]
| | |
[Database] [Detection/IDS] [Honeypot]
|
[Dashboard]


## Components

- **esp32/** — Wokwi projects, one folder per device
  - `badge-controller/` — **completed**: push button (GPIO4) simulates a
    badge scan, red LED (GPIO2), PubSubClient library, publishes to
    `facility/badge1/access`
  - `motion-sensor/`, `lock-actuator/` — **not started yet**
- **Broker/** — `mosquitto.conf`, `acl.conf`, `passwordfile`
- **Backend/** — MQTT subscriber + API (**not started yet**)
- **screenshots/** — evidence figures referenced in `Notes.md`
- **docker-compose.yml** — currently orchestrates the Mosquitto broker;
  will be extended with backend, DB, IDS, honeypot, dashboard services

## Tech stack

- ESP32 simulation: Wokwi
- ESP32 firmware library: PubSubClient (Arduino)
- MQTT broker: Eclipse Mosquitto (Dockerized), username/password auth + ACLs
- Public tunnel: bore (open-source, bore.pub relay)
- Backend: Python (FastAPI) or Node.js — **planned**
- Database: PostgreSQL — **planned**
- Detection: custom rule-based microservice — **planned**
- Honeypot: custom fake service — **planned**
- Dashboard: React or plain HTML/JS + WebSocket/polling — **planned**
- Attacker tooling: Python (paho-mqtt, scapy, python-nmap) / CLI (nmap,
  mosquitto_pub, hydra) — **planned**

## Known technical constraint

Wokwi's simulated ESP32 network is not on the same LAN as our Docker
containers — it needs a real, reachable broker address. We expose the
Dockerized Mosquitto broker via bore (https://github.com/ekzhang/bore), an
open-source TCP tunnel to the public bore.pub relay, so Wokwi firmware can
connect to it over the internet, entirely free and with no payment
information required (we moved off ngrok because its free tier now requires
a credit/debit card for TCP tunnels). Note: bore.pub URLs/ports change on
every restart — update the address in the ESP32 sketch each session.
Forwarded traffic is not encrypted by default (per bore's own
documentation) — acceptable for this development/demo context, not
intended as production-secure transport. The bore.pub free relay has shown
intermittent connection instability under testing — documented as a known
limitation of the free community tunnel.

Each device's MQTT credentials (e.g. `badge1`) are scoped via ACLs to only
publish on that device's own topic. Since the `badge-controller` Wokwi
project may be shared/public, this credential is treated as low-privilege
by design rather than secret — leaking it only allows fake badge-scan
events, nothing more.

## Setup

1. Clone the repo
2. `Broker/mosquitto.conf` configures the listener (port 1883), references
   `Broker/acl.conf` for per-device topic permissions, and
   `Broker/passwordfile` for username/password authentication
   (`allow_anonymous false`)
3. Run `docker compose up` to start the Mosquitto broker
4. Run `bore local 1883 --to bore.pub` to expose the broker publicly; note
   the `bore.pub:<PORT>` address it prints
5. Open `esp32/badge-controller` in Wokwi, set the bore.pub address/port
   and the `badge1` credentials in the sketch, and run the simulation
6. Press the simulated button — a `facility/badge1/access` message should
   arrive, verifiable with `mosquitto_sub -h localhost -p 1883 -t facility/badge1/access`
7. (Backend/dashboard setup — to be added)

## Status

- [x] Repo structure created
- [x] `mosquitto.conf` written (listener 1883, ACL + password file referenced)
- [x] `acl.conf` written (per-device topic restrictions)
- [x] Mosquitto added to `docker-compose.yml` + tested locally (pub/sub confirmed)
- [x] Real MQTT authentication added (`passwordfile`, `allow_anonymous false`)
- [x] Public tunnel set up (bore.pub) and externally tested with auth/ACLs
- [x] First ESP32 (badge-controller) built and publishing to broker through bore
- [ ] Remaining 2 ESP32 firmwares (motion-sensor, lock-actuator)
- [ ] Backend event ingestion
- [ ] Attacker container + first attack scenario (recon)
- [ ] Detection rule #1 (recon)
- [ ] Command-injection attack + detection rule #2
- [ ] Honeypot built + integrated into logging
- [ ] Sensor-spoofing scenario + detection rule #3
- [ ] Lateral-movement correlation (honeypot -> broker)
- [ ] Basic dashboard (live event feed + alerts list)
- [ ] Response action (block/isolate source)
- [ ] End-to-end integration test
- [ ] Demo rehearsal
- [ ] Report written

See `Notes.md` for the detailed, dated build log.


## Team / Work Distribution

**Ghada — Infrastructure & Cybersecurity Lead**

Already completed (core project infrastructure):
- Designed the overall IoT Cyber Range architecture
- Created the repository structure
- Wrote `Broker/mosquitto.conf` (MQTT listener on port 1883, ACL and
  password file references)
- Wrote `Broker/acl.conf` with per-device MQTT topic restrictions
- Added Mosquitto to `docker-compose.yml` and tested local MQTT pub/sub
  communication
- Implemented real MQTT authentication with `passwordfile` and disabled
  anonymous access (`allow_anonymous false`)
- Set up the public Bore TCP tunnel and externally tested MQTT
  communication through it with authentication and ACLs
- Built the first ESP32, `badge-controller`, and verified end-to-end MQTT
  publishing through Bore

Remaining work:
- Attacker environment/scripts: reconnaissance, command-injection, sensor
  spoofing, and lateral-movement scenarios
- Honeypot
- Detection rules
- Dashboard and alerting
- Response/blocking mechanisms
- Attack/defense testing and final security integration

**Arij — IoT Device & Data Lead**

- Implement the remaining two ESP32 firmwares (`motion-sensor`,
  `lock-actuator`) and their MQTT integration
- Device-side testing and integration
- Backend MQTT event ingestion, database schema, and event
  storage/processing
- Integration of all three ESP32 devices with the backend


## Timeline (6 weeks)

- Week 1: repo + broker + first ESP32 connectivity — **done**
- Week 2: all 3 ESP32 firmwares + backend ingesting events — **in progress
  (badge-controller done)**
- Week 3: attacker container + recon attack + first detection rule + basic dashboard
- Week 4: command-injection attack + honeypot
- Week 5: sensor-spoofing scenario + anomaly rule + lateral-movement correlation
- Week 6: response action, polish, demo rehearsal, buffer