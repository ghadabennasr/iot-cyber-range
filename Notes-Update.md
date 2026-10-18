# Build Log (Update) — IoT Cyber Range

Continuation of `Notes.md` (Ghada's original build log). This file records every
change made while taking over the unfinished tasks, every problem met, and how it
was handled. Steps are in the order they happened.

Last updated: 15 October 2026

**Scope of this work:** command-injection attack + detection rule #2, honeypot,
sensor-spoofing + rule #3, lateral-movement correlation, basic dashboard,
response action, end-to-end test, demo rehearsal, report.

**Ground rules agreed:**
- Arij keeps the backend ingestion (PostgreSQL) and the motion/lock firmware.
  Our part is built independently and linked to her backend after she finishes.
  Our IDS and dashboard therefore read MQTT and the broker log directly.
- Ghada's badge-controller circuit (`diagram.json`) is reused unchanged.
- The honeypot covers the **badge controller only** and does not depend on
  Arij's work (details in Step 9).
- The project is worked on **locally** and is not pushed to GitHub.

---

## Step 1: Review of the starting state

**What was done:**
Read the README and the first zip (`Notes.md`, `acl.conf`, `mosquitto.conf`,
`docker-compose.yml`, `passwordfile.example`, `diagram.json`).

**State found:**
Only the broker, authentication, ACLs, the tunnel and the badge button existed.
No backend, no database, no motion or lock device, no detection.

**Problems found and how they were handled:**

| # | Problem | Why it mattered | Fix |
|---|---------|-----------------|-----|
| 1 | `lock1` could write `facility/lock1/#` but not read its command topic, and nobody could publish commands | "Forged command" meant nothing without a legitimate sender; the lock could not receive commands | `lock1` reads `facility/lock1/cmd`; new `controller` user is the only one allowed to write it |
| 2 | Topic names differed: README said `zone2` / `lock3`, ACL said `motion1` / `lock1` | Firmware, ACL and rules would disagree | One scheme: `facility/<device>/...` with `badge1`, `motion1`, `lock1` |
| 3 | Denied publishes are dropped by Mosquitto, so a subscriber never sees them | Recon and forged commands would be invisible to the IDS | Broker log written to a shared volume; the IDS reads the log |
| 4 | Devices arrive through the bore tunnel, so their source IP is the tunnel host | IP-based detection and blocking is unreliable for devices | Identify by MQTT username / client ID; the attacker runs inside the Docker network |
| 5 | Open item in `Notes.md` assumed the ACL is keyed on client ID | Mosquitto keys ACL rules on the **username** | Item closed; the Figure 7 test passed because of the `badge1` username |
| 6 | Badge payload was plain text (`Badge detected`) | Nothing for rules to inspect, no way to spot replay | JSON payload with `device`, `badge_id`, `seq` (Step 7) |

---

## Step 2: Broker hardening

**What was done:**
- `Broker/acl.conf`: per-device rules, a `controller` command sender, monitoring
  users (`backend`, `ids`, `dashboard`) and a `canary` account with no rights.
- `Broker/mosquitto.conf`: `allow_anonymous false`, password and ACL files,
  logging to stdout and to `/mosquitto/log/mosquitto.log` (`log_type all`,
  because denied publishes are logged at debug level, as far as we know).
- `docker-compose.yml`: broker published on `127.0.0.1:1883` only (bore connects
  to localhost, the LAN cannot reach the broker); log kept in a named volume
  shared with the IDS.
- `Broker/add-user.ps1` / `add-user.sh`: create or update users with
  `mosquitto_passwd` run through Docker, printing the password.
- `Broker/passwordfile.example` updated with the new users.

**Note:** the lab built in Step 5 extends these files (more services, networks,
secrets).

**Known limits:**
- `log_type all` makes the log grow fast; reset the log volume between runs.
- Read-denied subscriptions are not visible in the log (Mosquitto checks reads at
  delivery time), so "unauthorized subscribe" cannot be detected from denials.

---

## Step 3: Development environment

**What was done:**
- Installed Docker Desktop with the **WSL 2** backend; verified with
  `docker run --rm hello-world`.
- Installed the VS Code extensions PlatformIO IDE, Wokwi Simulator and Docker.
- Requested a Wokwi licence (F1 → "Wokwi: Request a new License").

**Problems and answers:**
- *Is the VS Code Docker extension enough?* No. It is only a control panel for
  containers; it needs a real engine, which on Windows is Docker Desktop.
- *Simulated ESP32 reaching the broker:* Wokwi's built-in Wi-Fi gives internet
  access, so the bore tunnel (`bore local 1883 --to bore.pub`) still works.
  A Wokwi private gateway (`host.wokwi.internal`) would remove the need for bore,
  but Wokwi's own example indicates it needs a paid Wokwi Club plan (not verified
  for this account).
- *bore changes its public port on every restart:* the port in `secrets.h` must
  be updated and the firmware rebuilt each session.

---

## Step 4: Smoke tests of the broker

**What was done:**
Tested with `mosquitto_pub` / `mosquitto_sub` through `docker exec`:
1. allowed publish by `badge1` on its own topic → received by a `backend` listener,
2. allowed command by `controller` on `facility/lock1/cmd`,
3. `badge1` publishing to `facility/lock1/cmd` → denied (nothing delivered),
4. wrong password and anonymous connection → refused,
5. checked the broker log for traces of 3 and 4.

**Why:** the IDS depends on those log traces (Problem 3 in Step 1).

---

## Step 5: Lab implemented with GitHub Copilot

**What was done:**
Work continued with Copilot, which wrote `IMPLEMENTATION-REPORT.txt`. According to
that report and the repo contents:
- `Attacker/`: container running the scenarios `recon`, `command-injection`,
  `sensor-spoof` and `lateral-movement` against the lab only.
- `Lab/honeypot.py`: fake TCP maintenance console on internal port 2323; logs
  every contact to `Lab/runtime/events.jsonl`; never executes input.
- `Lab/monitor.py`: IDS and dashboard backend. Reads MQTT `facility/#`, the broker
  log and the honeypot log; stores events and alerts in SQLite; serves
  `/api/events`, `/api/alerts`, `/api/respond`.
- `Lab/dashboard.html` behind an nginx proxy at `http://127.0.0.1:8080`: live
  event feed, alerts list, block button.
- `Lab/response.ps1`: host script (`Watch` / `Unblock`) that applies queued blocks
  by adding a deny block for the user to the ACL and restarting the broker.
- `Lab/run-scenario.ps1`: runs a scenario in the attacker container.
- Unit tests for the monitor and honeypot; the IDS password is passed as a Docker
  secret; the attacker sits on an internal Docker network.

---

## Step 6: Code review of the Copilot work

**What was done:**
Reviewed the full repo (secrets files were not opened), ran the unit tests (7
passed) and probed the parsing with realistic input.

| # | Finding | Impact |
|---|---------|--------|
| 1 | Mosquitto's denial lines name the **client ID**, not the username; the monitor expected a username and took the source from "the last connection seen". The unit test used an invented log line | Alerts had no username; source could be mislabelled when other clients connect |
| 2 | Honeypot regex matched `id` / `cat` anywhere (`?id=5`, `/cat/status`); plain honeypot contact raised no alert | False positives and a missed signal |
| 3 | Log-watcher thread only caught a few exception types and could die silently | Detection could stop while the dashboard looked healthy |
| 4 | Replay rule: a spoofed `seq` of 999999 made every later real reading a "replay"; a device reboot also caused false alerts | Rule could be poisoned and noisy |
| 5 | `/api/respond` had no authentication and ignored Content-Type; any web page or the attacker container could queue a block, including on `ids` | Detection could be blinded |
| 6 | Block is a second `user X` ACL section; only "applied and removed" was tested, not that publishes are denied | Response action unproven |
| 7 | Wokwi/PlatformIO files at repo root, sketch in `esp32/badge-controller/`, no `diagram.json` next to `wokwi.toml`, no `lib_deps` | Simulation could not be built |
| 8 | Badge sketch hard-coded the MQTT password and bore port | Secret in source |
| 9 | `Notes.md` still listed `zone2` / `lock3` | Stale documentation |

---

## Step 7: Patch applied

**What was done:**
- **Badge project (#7, #8):** `esp32/badge-controller/` is its own PlatformIO +
  Wokwi project (`platformio.ini`, `wokwi.toml`, `src/main.cpp`,
  `include/secrets.example.h`). Credentials and bore port live in a git-ignored
  `secrets.h`. Payload is JSON: `{"device":"badge1","badge_id":"B-102","seq":N}`.
  Non-blocking MQTT reconnect and Wi-Fi reconnect added. Old root PlatformIO files
  and `sketch.ino` removed.
- **Denial attribution (#1):** the monitor keeps a `client id → (ip, username)` map
  from "New client connected" lines and resolves denials through it. Source
  falls back to the last connection only for anonymous rejections.
- **New rule `forged-lock-command` (high):** denied publish to `facility/<device>/cmd`.
  This is rule #2 of the task list.
- **Honeypot (#2):** command-injection regex now needs a separator or operator
  (`;whoami`, `|cat`, `&&`, `$(`, backtick); `?id=5` and `/cat/status` are no
  longer flagged. New low-severity `honeypot-contact` alert for any contact.
- **Watcher (#3):** catches every exception per line and per pass, and waits for
  half-written lines.
- **Replay rule (#4):** keeps a window of recent sequence numbers; a restart is
  not a replay; a jump of more than 1000 raises `sensor-seq-jump` without
  poisoning later readings.
- **API (#5):** `/api/respond` needs `Authorization: Bearer <DASHBOARD_TOKEN>`
  (set in `.env`) and a JSON Content-Type; it fails closed when no token is set.
  `ids` and `dashboard` can no longer be blocked from the dashboard or
  `response.ps1`. The dashboard has a token box.
- **Tests:** 22 unit tests, including realistic Mosquitto log lines, client
  attribution when other clients connect in between, partial-line reading and the
  API's 503 / 401 / 415 / 202 / 400 responses. The badge firmware was
  syntax-checked against stub headers (not a real PlatformIO build).

---

## Step 8: Problems met while applying and running the patch

| Problem | Cause | Fix |
|---------|-------|-----|
| `response.ps1` "is not digitally signed" | File came from a downloaded zip and Windows marked it as from the internet | `Unblock-File .\Lab\response.ps1`, or `Get-ChildItem -Recurse -Include *.ps1 \| Unblock-File`; alternative: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Old generic alerts ("rejected a connection or topic operation") still listed after the patch | Rows from earlier runs remain in the SQLite DB | Reset: `docker compose down -v`, empty `Lab\runtime\`, `docker compose up -d` (wipes all history) |
| Question about enabling a VS Code env setting | Not needed: Docker Compose reads `.env` from the repo root by itself | None; `python.terminal.useEnvFile` only matters for VS Code terminals and nothing here uses it |
| Where to run each command | Mixed repo-root and firmware-folder steps | Rule: PowerShell at the repo root for Docker/scripts; VS Code opened on `esp32/badge-controller/` for firmware |

---

## Step 9: Verification

**Scenario run (`lateral-movement`) — result:**
```
honeypot-to-broker-correlation  critical
forged-lock-command             high      badge1 tried to publish to facility/lock1/cmd ...
unauthorized-mqtt-activity      medium    Mosquitto rejected a publish from badge1.
honeypot-contact                low       172.19.0.6 contacted the simulated maintenance honeypot ...
```
All four expected alerts appeared, the username (`badge1`) is now resolved, and the
honeypot contact and the broker denial were correlated by the same source IP. This
confirmed the assumption about the Mosquitto log format. (Six older alerts with
the generic wording were leftovers from earlier runs.)

**Honeypot scope decision (badge controller only):**
- The honeypot stands for the **badge controller's maintenance console only**.
  There is no honeypot for the motion sensor or the lock.
- It does not depend on Arij's work. It is a plain TCP service that writes events
  to `Lab/runtime/events.jsonl`; it does not use MQTT, the backend or the motion
  and lock devices. The correlation with the broker uses only the attacker's
  source IP.
- In the code it is still labelled generically (`fake-maintenance-console`,
  "Maintenance console unavailable."). Relabelling it as a badge-controller
  console is optional and cosmetic.

---

## Open items

- **Block action not yet verified:** run `response.ps1 -Action Watch`, queue a
  block for `badge1` from the dashboard, then publish as `badge1`; the log must
  show a new `Denied PUBLISH`. If the publish still passes, the duplicate
  `user badge1` ACL section is not honoured and the block needs another approach
  (for example editing the user's existing section, or Mosquitto's dynamic
  security plugin). Restarting the broker also drops every client; a reload
  (SIGHUP) with the `Broker/` folder mounted could avoid that, but is untested.
- **Badge firmware build:** run PlatformIO build and the Wokwi simulation from
  `esp32/badge-controller/` and confirm the JSON reaches a `backend` listener.
- **Motion and lock firmware:** still empty (Arij). The IDS expects motion
  payloads with `distance_cm` and `seq`; this must be agreed with her.
- **Integration with Arij's backend:** after she finishes, store alerts through
  her database or add an `alerts/#` MQTT topic.
- **Scenario naming:** the forged lock command is exercised inside
  `lateral-movement`, while `command-injection` sends text to the honeypot.
  Decide how the demo and report name them.
- **Network layout:** the attacker container can still reach the monitor's HTTP
  port (now protected by the token); a separate network would be stricter.
- **Documentation:** update `Notes.md` / README (still mention `zone2` / `lock3`).
- **Before sharing the project:** rotate the lab passwords and keep bore running
  only during tests and demos.
- **Remaining deliverables:** end-to-end integration test, demo rehearsal, report;
  screenshots of the alert output, `docker compose ps` and the Wokwi run would
  serve as evidence.
