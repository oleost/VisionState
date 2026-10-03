#!/usr/bin/env bash
# Upgrade test (CI, after the smoke test): what users get when they update.
#
#   scripts/upgrade_test.sh <new image> <arch> <platform>
#
# 1. The newest stable release (from GHCR) creates one sensor of each kind.
# 2. The new image starts on the same data: the sensors are still there, keep their Home Assistant
#    unique IDs and entity IDs, and the reading sensor publishes its value again.
# 3. Rollback: the stable release starts again on the data the new image migrated (what happens
#    when someone restores a backup or goes back a version).
# Every run must start cleanly, log no traceback and stop with exit code 0.
set -euo pipefail

NEW="$1" ARCH="$2" PLATFORM="$3"
ASSETS=visionstate/backend/tests/assets
STABLE=$(git tag --list 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)
CURRENT="v$(grep -E '^version:' visionstate/config.yaml | cut -d '"' -f 2)"
if [ "$STABLE" = "$CURRENT" ]; then  # a stable tag being released: compare with the one before
  STABLE=$(git tag --list 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | grep -vx "$CURRENT" | tail -1)
fi
OLD="ghcr.io/oleost/visionstate-$ARCH:${STABLE#v}"
echo "upgrade test: $OLD -> $NEW -> $OLD"

python3 -m http.server 8766 --bind 127.0.0.1 --directory "$ASSETS" >/dev/null 2>&1 &
SERVER=$!
WORK=$(mktemp -d)
cleanup() {
  kill "$SERVER" 2>/dev/null || true
  docker rm -f vs-upgrade mqtt-upgrade >/dev/null 2>&1 || true
  docker volume rm -f vs-upgrade-data >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
docker pull -q --platform "$PLATFORM" "$OLD"
docker run -d --name mqtt-upgrade -p 1884:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf >/dev/null

api() { curl -sf "localhost:8099/api/v1/$1" "${@:2}"; }

start() {  # <image> <label>
  docker run -d --init --name vs-upgrade --platform "$PLATFORM" --network host -v vs-upgrade-data:/data \
    -e VISIONSTATE_DATA=/data -e VISIONSTATE_MEDIA=/data/media \
    -e VISIONSTATE_MQTT_HOST=127.0.0.1 -e VISIONSTATE_MQTT_PORT=1884 "$1" >/dev/null
  for _ in $(seq 1 120); do
    if api status | jq -e '.mqtt.connected and .backbone_error == ""' >/dev/null 2>&1; then
      echo "$2 started: $(api status | jq -c '{version, sensors}')"
      return
    fi
    sleep 3
  done
  echo "::error::$2 did not start"; docker logs vs-upgrade | tail -40; exit 1
}

stop() {  # <label>
  if docker logs vs-upgrade 2>&1 | grep -q Traceback; then
    echo "::error::$1 logged a traceback"; docker logs vs-upgrade | tail -60; exit 1
  fi
  docker stop -t 30 vs-upgrade >/dev/null
  code=$(docker inspect -f '{{.State.ExitCode}}' vs-upgrade)
  docker rm vs-upgrade >/dev/null
  if [ "$code" != "0" ]; then echo "::error::$1 exited with code $code"; exit 1; fi
}

# The discovery configs of our entities (retained, so a fresh subscription gets all of them).
discovery() {  # <file>
  docker exec mqtt-upgrade mosquitto_sub -t 'homeassistant/#' -v -W 5 2>/dev/null     | python3 scripts/discovery_ids.py collect > "$1" || true
}

sensors() { api sensors | jq -c '[.[] | {id, slug, name, kind}] | sort_by(.id)'; }

# 1. The stable release creates the sensors.
start "$OLD" "stable $STABLE"
api sensors -X POST -H 'Content-Type: application/json' -d '{"name":"Door","source_type":"http","source":"http://127.0.0.1:8766/driveway.jpg","states":[{"name":"Open"},{"name":"Closed"}],"interval_s":3600}' >/dev/null
api sensors -X POST -H 'Content-Type: application/json' -d '{"name":"Yard","kind":"objects","source_type":"http","source":"http://127.0.0.1:8766/beach.jpg","objects":{"classes":["dog","person"]},"interval_s":3600}' >/dev/null
api sensors -X POST -H 'Content-Type: application/json' -d '{"name":"Meter","kind":"reading","source_type":"http","source":"http://127.0.0.1:8766/lcd.png","reading":{"mode":"counter","decimals":1,"unit":"kWh","device_class":"energy"},"interval_s":3600}' >/dev/null
docker exec mqtt-upgrade mosquitto_sub -t 'visionstate/meter/state' -C 1 -W 300 | grep -q '123456.7'
before_sensors=$(sensors)
discovery "$WORK/before.json"
echo "stable made $(echo "$before_sensors" | jq length) sensors, $(jq length "$WORK/before.json") discovery configs"
stop "stable $STABLE"

# 2. The new image on the same data. The value is published again after start-up.
docker exec mqtt-upgrade mosquitto_pub -t 'visionstate/meter/state' -r -n
start "$NEW" "new image"
if [ "$(sensors)" != "$before_sensors" ]; then
  echo "::error::sensors changed by the upgrade"; echo "before: $before_sensors"; echo "after:  $(sensors)"; exit 1
fi
docker exec mqtt-upgrade mosquitto_sub -t 'visionstate/meter/state' -C 1 -W 300 | grep -q '123456.7'
discovery "$WORK/after.json"
python3 scripts/discovery_ids.py compare "$WORK/before.json" "$WORK/after.json"
stop "new image"

# 3. Rollback: the stable release on the migrated data.
start "$OLD" "stable $STABLE again"
if [ "$(sensors)" != "$before_sensors" ]; then
  echo "::error::stable $STABLE does not see the sensors after the rollback"; sensors; exit 1
fi
stop "stable $STABLE again"
echo "upgrade test passed ($STABLE -> new image -> $STABLE)"
