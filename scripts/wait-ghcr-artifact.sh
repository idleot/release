#!/usr/bin/env bash
# Wait until an OCI artifact exists on GHCR (oras/docker imagetools).
# Usage: wait-ghcr-artifact.sh <image:tag> [timeout_secs] [interval_secs]
set -euo pipefail

REF="${1:?image:tag}"
TIMEOUT="${2:-7200}"
INTERVAL="${3:-60}"
start="$(date +%s)"

echo "[wait] waiting for $REF (timeout=${TIMEOUT}s)"
while true; do
  if docker buildx imagetools inspect "$REF" >/dev/null 2>&1; then
    echo "[wait] ready: $REF"
    exit 0
  fi
  if command -v oras >/dev/null 2>&1; then
    if oras manifest fetch "$REF" >/dev/null 2>&1; then
      echo "[wait] ready (oras): $REF"
      exit 0
    fi
  fi
  now="$(date +%s)"
  if (( now - start >= TIMEOUT )); then
    echo "[wait] timed out waiting for $REF" >&2
    exit 1
  fi
  echo "[wait] not yet — sleep ${INTERVAL}s"
  sleep "$INTERVAL"
done
