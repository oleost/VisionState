#!/usr/bin/env bash
# Nothing phones home (CLAUDE.md): runs the image with an MQTT broker, a camera serving the test
# photos and a sensor of each kind whose model is bundled, and records every DNS question and every
# new connection it makes, from its first second to its last. Fails on any name looked up and any
# address outside the local ones. A library that starts sending something (ONNX Runtime's telemetry
# did, until 0.14.0b6) is caught here before a release.
#
#   bash scripts/network_watch.sh <image> [platform]     (CI: the image job; needs Docker and sudo)
#
# WATCH_S: how long the sensors run while it listens (default 180 s; ONNX Runtime's telemetry
# showed within seconds of a model loading, then every ~9 s).
#
# Docker's default network, not one of its own: there DNS goes out on port 53, where the capture
# sees it (on a network of its own Docker answers names itself, on a rewritten port), so the broker
# and the camera are reached by their addresses.
set -euo pipefail

IMAGE=$1
PLATFORM=${2:-linux/amd64}
WATCH_S=${WATCH_S:-180}
ASSETS="$PWD/visionstate/backend/tests/assets"
CAPTURE=$(mktemp)
API=http://127.0.0.1:8098/api/v1

cleanup() {
  docker rm -f watch-app watch-camera watch-mqtt watch-net >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup

command -v tcpdump >/dev/null || { sudo apt-get update -q && sudo apt-get install -y -q tcpdump; }
docker run -d --name watch-mqtt eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf >/dev/null
docker run -d --name watch-camera --platform "$PLATFORM" -v "$ASSETS:/srv:ro" \
  --entrypoint python "$IMAGE" -m http.server 8765 --directory /srv >/dev/null
ip_of() { docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$1"; }
MQTT_IP=$(ip_of watch-mqtt)
CAMERA_IP=$(ip_of watch-camera)

# The app's network belongs to a container that is up first, so the capture sees the app's whole
# life, from its first second.
docker run -d --name watch-net --platform "$PLATFORM" -p 127.0.0.1:8098:8099 \
  --entrypoint sleep "$IMAGE" infinity >/dev/null
pid=$(docker inspect -f '{{.State.Pid}}' watch-net)
sudo nsenter -t "$pid" -n tcpdump -l -n -i any -tttt \
  'port 53 or (tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack == 0)' \
  >"$CAPTURE" 2>/dev/null &
dump=$!
sleep 2

# --init: Home Assistant runs apps with an init process (tini) as PID 1.
docker run -d --init --name watch-app --platform "$PLATFORM" --network container:watch-net \
  -e VISIONSTATE_DATA=/tmp/data -e VISIONSTATE_MEDIA=/tmp/media -e VISIONSTATE_MQTT_HOST="$MQTT_IP" \
  "$IMAGE" >/dev/null

ok=""
for _ in $(seq 1 120); do
  status=$(curl -sf "$API/status" || true)
  if [ -n "$status" ] && echo "$status" | jq -e '.mqtt.connected and .backbone != null and .backbone_error == ""' >/dev/null; then ok=1; break; fi
  sleep 3
done
if [ -z "$ok" ]; then echo "status: ${status:-none}"; docker logs watch-app | tail -30; exit 1; fi

camera="http://$CAMERA_IP:8765"
curl -sf -X POST "$API/sensors" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Watch state\",\"source_type\":\"http\",\"source\":\"$camera/beach.jpg\",\"states\":[{\"name\":\"A\"},{\"name\":\"B\"}]}" >/dev/null
curl -sf -X POST "$API/sensors" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Watch yard\",\"kind\":\"objects\",\"source_type\":\"http\",\"source\":\"$camera/beach.jpg\",\"objects\":{\"classes\":[\"dog\"]}}" >/dev/null
curl -sf -X POST "$API/sensors" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Watch meter\",\"kind\":\"reading\",\"source_type\":\"http\",\"source\":\"$camera/lcd.png\",\"reading\":{\"mode\":\"counter\",\"decimals\":1}}" >/dev/null
# The models are at work: the dog is seen and the meter read.
docker exec watch-mqtt mosquitto_sub -t 'visionstate/watch_yard/objects/dog/state' -C 1 -W 300 | grep -q ON
docker exec watch-mqtt mosquitto_sub -t 'visionstate/watch_meter/state' -C 1 -W 300 | grep -q '123456.7'
echo "sensors at work; listening for ${WATCH_S} s"
sleep "$WATCH_S"
docker stop -t 30 watch-app >/dev/null
sleep 2
sudo kill "$dump" 2>/dev/null || true
wait "$dump" 2>/dev/null || true

python3 - "$CAPTURE" "$MQTT_IP:1883" "$CAMERA_IP:8765" <<'EOF'
import ipaddress, re, sys

names, outside, local = {}, {}, set()
for line in open(sys.argv[1]):
    for name in re.findall(r" (?:A|AAAA|HTTPS|SVCB|PTR|SRV|TXT|MX|ANY)\? (\S+?)\.? ", line):
        if name != "localhost" and not name.endswith(".in-addr.arpa"):
            names.setdefault(name, line.strip())
    m = re.search(r" > (\d+\.\d+\.\d+\.\d+)\.(\d+): Flags \[S\]", line)
    if m:
        target = f"{m.group(1)}:{m.group(2)}"
        ip = ipaddress.ip_address(m.group(1))
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            local.add(target)
        else:
            outside.setdefault(target, line.strip())
print(f"{sum(1 for _ in open(sys.argv[1]))} packets recorded (DNS and new connections)")
# A control: the capture must have seen the app connect to the broker and to the camera.
missing = set(sys.argv[2:]) - local
if missing:
    print(f"::error::The capture did not see the app connect to {sorted(missing)}: it did not work")
    sys.exit(1)
if names or outside:
    for what, found in (("looked up", names), ("connected to", outside)):
        for key, line in found.items():
            print(f"::error::The app reached out: {what} {key}  [{line}]")
    sys.exit(1)
print("nothing beyond the broker and the camera: no phoning home")
EOF
