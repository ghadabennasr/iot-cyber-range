# Build Log

## 2026-09-27 — Step 1: Repo + Broker skeleton
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