# Build Log

## Step 1: Repo + Broker skeleton
- Created repo structure: esp32/, broker/, backend/, docker-compose.yml
- Wrote broker/mosquitto.conf:
  - listener on 1883, protocol mqtt
  - acl_file pointing to broker/acl.conf
  - allow_anonymous true (TEMPORARY — see "Known issues" below)
- Next: write acl.conf, add mosquitto service to docker-compose.yml, test locally

## Known issues / tech debt
- [ ] allow_anonymous true is insecure — must switch to username/password or
      client-cert auth before final demo, or explicitly justify it in the
      report as "development mode, hardened in Week X"

## Step 2: ACL configuration
- Wrote broker/acl.conf:
  - each ESP32 client restricted to publish-only on its own topic
    (e.g. badge-controller -> facility/badge1/#, motion-sensor ->
    facility/zone2/#, lock-actuator subscribes to facility/lock3/cmd)
  - backend client granted subscribe rights on facility/# (wildcard, read-only)
- mosquitto.conf still has allow_anonymous true — ACLs won't be properly
  enforced per-client until real auth (username/password) is added
- Next: add mosquitto service to docker-compose.yml, test locally with
  mosquitto_pub/mosquitto_sub before touching Wokwi

## Known issues / tech debt
- [ ] allow_anonymous true is insecure — must switch to username/password or
      client-cert auth before final demo, or explicitly justify it in the
      report as "development mode, hardened in Week X"
- [ ] ACL rules written but not yet testable/enforceable without real client
      authentication — flag this dependency

## Step 3: Mosquitto in Docker Compose
- Added mosquitto service to docker-compose.yml:
  - image: eclipse-mosquitto:2
  - mounted broker/mosquitto.conf and broker/acl.conf as volumes
  - exposed port 1883:1883
- Ran `docker compose up`, confirmed broker starts without errors
- Tested locally: mosquitto_pub / mosquitto_sub against the Dockerized
  broker from host machine — pub/sub confirmed working
- Next: expose the broker so Wokwi's simulated ESP32 can reach it
  (ngrok tunnel), then write the first ESP32 sketch (badge-controller)

  