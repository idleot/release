#!/usr/bin/env bash
# Copy a path out of an image into a directory. Missing images are not an
# error (first runs, slices not published yet) — the target stays empty.
# Usage: extract-image.sh <image> <path-in-image> <dest-dir>
set -euo pipefail

IMAGE="$1"
SRC="$2"
DEST="$3"

mkdir -p "$DEST"
if ! docker pull --quiet "$IMAGE" >/dev/null 2>&1; then
  echo "[extract] ${IMAGE} not found — skipping"
  exit 0
fi

# Artifact images are FROM scratch (no CMD); create never runs the command.
cid="$(docker create "$IMAGE" /bin/true)"
trap 'docker rm -f "$cid" >/dev/null' EXIT
docker cp "${cid}:${SRC%/}/." "$DEST"
echo "[extract] ${IMAGE}:${SRC} → ${DEST} ($(du -sh "$DEST" | cut -f1))"
