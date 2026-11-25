#!/usr/bin/env bash
# One-shot ORAS pull of client-sdk / client-sdk-wasm (no long wait).
# Usage: try-pull-client-sdk.sh <oras-ref> <dest-dir>
# Exit 0 on success, 1 on miss.
set -euo pipefail

REF="${1:?oras ref}"
DEST="${2:?dest dir}"

command -v oras >/dev/null || { echo "oras required" >&2; exit 1; }
oras logout ghcr.io 2>/dev/null || true
rm -rf "$DEST"
mkdir -p "$DEST"
tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

if ! oras pull "$REF" -o "$tmp" 2>"$tmp/err"; then
  echo "[client-sdk] miss $REF ($(head -c 160 "$tmp/err" 2>/dev/null || true))"
  exit 1
fi
layer="$(find "$tmp" -maxdepth 1 -type f \( -name '*.tgz' -o -name '*.tar.gz' -o -name 'client-sdk.tgz' -o -name 'host-sdk.tgz' \) | head -1 || true)"
[[ -n "$layer" ]] || layer="$(find "$tmp" -maxdepth 1 -type f | head -1 || true)"
[[ -n "$layer" ]] || { echo "[client-sdk] no layer in $REF" >&2; exit 1; }
tar -xzf "$layer" -C "$DEST"
test -f "$DEST/lib/cmake/QtClientSdk/QtClientSdkConfig.cmake"
test -f "$DEST/lib/cmake/QtClientSdk/QtClientMod.cmake"
echo "[client-sdk] ready in $DEST from $REF"
