#!/usr/bin/env bash
#
# Configure and start the Docker daemon for a nested VM (the Cloud Agent VM).
#
# The nested environment must use the **vfs** storage driver -- the default
# overlay2 fails with "invalid argument" inside the nested container. This
# script is idempotent: run it before building/using any of the images here.
#
#   ./docker/setup-docker.sh
#
set -euo pipefail
SUDO="${SUDO:-sudo}"

# 1. Force the vfs storage driver.
if [ ! -f /etc/docker/daemon.json ] || ! grep -q '"storage-driver"' /etc/docker/daemon.json 2>/dev/null; then
  echo '[setup-docker] writing /etc/docker/daemon.json (storage-driver=vfs)'
  echo '{"storage-driver":"vfs"}' | $SUDO tee /etc/docker/daemon.json >/dev/null
  NEED_RESTART=1
fi

# 2. Make sure the daemon is up.
if ! $SUDO docker info >/dev/null 2>&1 || [ "${NEED_RESTART:-0}" = "1" ]; then
  echo '[setup-docker] starting dockerd...'
  if command -v service >/dev/null 2>&1 && $SUDO service docker status >/dev/null 2>&1; then
    $SUDO service docker restart || true
  fi
  # Fallback: launch dockerd directly if the service isn't managed.
  if ! $SUDO docker info >/dev/null 2>&1; then
    $SUDO sh -c 'nohup dockerd >/tmp/dockerd.log 2>&1 &'
  fi
  for _ in $(seq 1 30); do
    $SUDO docker info >/dev/null 2>&1 && break
    sleep 1
  done
fi

if ! $SUDO docker info >/dev/null 2>&1; then
  echo '[setup-docker] ERROR: docker daemon did not come up (see /tmp/dockerd.log)' >&2
  exit 1
fi
$SUDO docker info --format '[setup-docker] Docker {{.ServerVersion}} ready (storage-driver={{.Driver}})'
