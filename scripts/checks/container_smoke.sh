#!/bin/sh
set -eu

IMAGE="${1:-agent-wallet:smoke-test}"

echo "Starting container smoke test for image '$IMAGE'..."

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Image '$IMAGE' not found locally. Building image..."
  docker build -t "$IMAGE" .
else
  echo "Using existing image '$IMAGE'."
fi

TMP_DATA_DIR=$(mktemp -d 2>/dev/null || mktemp -d -t 'agent-wallet-data')
chmod 777 "$TMP_DATA_DIR"

CONTAINER_NAME="agent-wallet-smoke-$$"
CONTAINER_ID=""

cleanup() {
  EXIT_CODE=$?
  if [ -n "$CONTAINER_ID" ]; then
    echo "Stopping and removing container '$CONTAINER_NAME' ($CONTAINER_ID)..."
    docker stop "$CONTAINER_ID" >/dev/null 2>&1 || true
    docker rm -f "$CONTAINER_ID" >/dev/null 2>&1 || true
  fi
  if [ -n "${TMP_DATA_DIR:-}" ] && [ -d "$TMP_DATA_DIR" ]; then
    rm -rf "$TMP_DATA_DIR" >/dev/null 2>&1 || true
  fi
  exit "$EXIT_CODE"
}

trap cleanup EXIT INT TERM

if [ -n "${HOST_PORT:-}" ]; then
  PORT_MAP="-p 127.0.0.1:${HOST_PORT}:8000"
else
  PORT_MAP="-p 127.0.0.1::8000"
fi

echo "Running container from image '$IMAGE'..."
CONTAINER_ID=$(docker run -d \
  --name "$CONTAINER_NAME" \
  $PORT_MAP \
  -v "$TMP_DATA_DIR:/app/data" \
  "$IMAGE")

if [ -z "${HOST_PORT:-}" ]; then
  HOST_PORT=$(docker port "$CONTAINER_ID" 8000 | sed 's/.*://' | head -n 1)
fi

echo "Container started with ID $CONTAINER_ID on port $HOST_PORT."

HEALTH_URL="http://127.0.0.1:${HOST_PORT}/health"
echo "Waiting for $HEALTH_URL to return 200 (up to 30s)..."

MAX_RETRIES=30
COUNT=0
HEALTH_OK=0

while [ "$COUNT" -lt "$MAX_RETRIES" ]; do
  IS_RUNNING=$(docker inspect -f '{{.State.Running}}' "$CONTAINER_ID" 2>/dev/null || echo "false")
  if [ "$IS_RUNNING" != "true" ]; then
    echo "ERROR: Container exited prematurely. Container logs:"
    docker logs "$CONTAINER_ID"
    exit 1
  fi

  STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
  if [ "$STATUS_CODE" = "200" ]; then
    HEALTH_OK=1
    echo "Healthcheck passed: $HEALTH_URL returned HTTP 200 after ${COUNT}s."
    break
  fi

  sleep 1
  COUNT=$((COUNT + 1))
done

if [ "$HEALTH_OK" -ne 1 ]; then
  echo "ERROR: /health failed to respond with 200 within 30 seconds (last status: $STATUS_CODE)."
  echo "Container logs:"
  docker logs "$CONTAINER_ID"
  exit 1
fi

UI_URL="http://127.0.0.1:${HOST_PORT}/ui/"
echo "Checking UI at $UI_URL..."
UI_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$UI_URL" 2>/dev/null || echo "000")
if [ "$UI_STATUS" != "200" ]; then
  echo "ERROR: Expected HTTP 200 from $UI_URL, got $UI_STATUS."
  echo "Container logs:"
  docker logs "$CONTAINER_ID"
  exit 1
fi

UI_BODY=$(curl -s "$UI_URL" 2>/dev/null || true)
if ! echo "$UI_BODY" | grep -q "BaKhi Wallet"; then
  echo "ERROR: Response body from $UI_URL does not contain 'BaKhi Wallet'."
  exit 1
fi
echo "Verified /ui/ returns HTTP 200 and contains 'BaKhi Wallet'."

echo "Checking container process user..."
CONFIG_USER=$(docker inspect -f '{{.Config.User}}' "$CONTAINER_ID" 2>/dev/null || true)
if [ "$CONFIG_USER" = "root" ] || [ "$CONFIG_USER" = "0" ] || [ -z "$CONFIG_USER" ]; then
  echo "ERROR: Configured container user is root or empty: '$CONFIG_USER'."
  exit 1
fi

PROC_UID=$(docker exec "$CONTAINER_ID" python -c "import os; print(os.getuid())")
if [ "$PROC_UID" = "0" ]; then
  echo "ERROR: Process running inside container is root (UID 0)."
  exit 1
fi
echo "Verified container process is non-root (Config.User='$CONFIG_USER', UID=$PROC_UID)."

# Verify mounted volume is writable by non-root user
docker exec "$CONTAINER_ID" python -c "import pathlib; p = pathlib.Path('/app/data/smoke.tmp'); p.write_text('ok'); assert p.read_text() == 'ok'; p.unlink()"
echo "Verified state directory /app/data is writable by container user."

echo "Stopping and removing container $CONTAINER_ID..."
docker stop "$CONTAINER_ID" >/dev/null
docker rm -f "$CONTAINER_ID" >/dev/null
CONTAINER_ID=""

rm -rf "$TMP_DATA_DIR"
TMP_DATA_DIR=""

echo "Container smoke test passed successfully."
exit 0
