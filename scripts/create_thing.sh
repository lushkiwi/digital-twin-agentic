#!/usr/bin/env bash
# Create (or reset) the pump twin in Ditto. Idempotent: PUT is an upsert, so
# re-running this also serves as a "reset twin to baseline" for demo reruns.
set -euo pipefail

DITTO_BASE_URL="${DITTO_BASE_URL:-http://localhost:8080}"
DITTO_USER="${DITTO_USER:-ditto}"
DITTO_PASS="${DITTO_PASS:-ditto}"
THING_ID="${THING_ID:-org.acme:pump-01}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Waiting for Ditto at ${DITTO_BASE_URL} ..."
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "${DITTO_USER}:${DITTO_PASS}" \
    "${DITTO_BASE_URL}/api/2/things/${THING_ID}" || true)
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

echo "Creating/resetting thing ${THING_ID} ..."
code=$(curl -s -o /tmp/ditto_thing_resp.json -w '%{http_code}' \
  -u "${DITTO_USER}:${DITTO_PASS}" \
  -X PUT "${DITTO_BASE_URL}/api/2/things/${THING_ID}" \
  -H 'Content-Type: application/json' \
  -d @"${SCRIPT_DIR}/thing.json")

if [ "$code" = "201" ]; then
  echo "Thing created."
elif [ "$code" = "200" ] || [ "$code" = "204" ]; then
  echo "Thing already existed — reset to baseline."
else
  echo "ERROR: unexpected status ${code}" >&2
  cat /tmp/ditto_thing_resp.json >&2
  exit 1
fi
