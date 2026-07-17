#!/usr/bin/env bash
# Create (or reset) all four component twins in Ditto. Idempotent: PUT is an upsert,
# so re-running also serves as "reset system to baseline" for demo reruns.
set -euo pipefail

DITTO_BASE_URL="${DITTO_BASE_URL:-http://localhost:8080}"
DITTO_USER="${DITTO_USER:-ditto}"
DITTO_PASS="${DITTO_PASS:-ditto}"
THING_NS="${THING_NS:-org.acme}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE_THING="${THING_NS}:motor-01"

echo "Waiting for Ditto at ${DITTO_BASE_URL} ..."
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "${DITTO_USER}:${DITTO_PASS}" \
    "${DITTO_BASE_URL}/api/2/things/${PROBE_THING}" || true)
  # 200 (exists) or 404 (gateway up, thing missing) both mean Ditto is ready
  if [ "$code" = "200" ] || [ "$code" = "404" ]; then
    break
  fi
  if [ "$i" = "60" ]; then
    echo "ERROR: Ditto did not become ready (last status: ${code})" >&2
    exit 1
  fi
  sleep 2
done

for f in "${SCRIPT_DIR}"/things/*.json; do
  name="$(basename "$f" .json)"        # e.g. motor-01
  tid="${THING_NS}:${name}"
  code=$(curl -s -o /tmp/ditto_thing_resp.json -w '%{http_code}' \
    -u "${DITTO_USER}:${DITTO_PASS}" \
    -X PUT "${DITTO_BASE_URL}/api/2/things/${tid}" \
    -H 'Content-Type: application/json' \
    -d @"$f")
  case "$code" in
    201) echo "created  ${tid}" ;;
    200|204) echo "reset    ${tid}" ;;
    *) echo "ERROR: ${tid} -> ${code}" >&2; cat /tmp/ditto_thing_resp.json >&2; exit 1 ;;
  esac
done
