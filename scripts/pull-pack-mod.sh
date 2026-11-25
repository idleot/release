#!/usr/bin/env bash
# Pull IdleOT pack-mod install prefix from GHCR when present (retry / rebuild skip).
# Usage: pull-pack-mod.sh <oras-ref> <dest-dir>
# Exit 0 always; prints pack_mod_hit=true|false on stdout last line for CI parsing.
# Sets GITHUB_OUTPUT pack_mod_hit when available.
set -euo pipefail

REF="${1:?oras ref}"
DEST="${2:?dest dir}"

hit=false
oras logout ghcr.io 2>/dev/null || true
tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

if oras pull "$REF" -o "$tmp" 2>"$tmp/err"; then
  archive="$(find "$tmp" -maxdepth 1 -type f \( -name '*.tgz' -o -name '*.tar.gz' -o -name 'pack-mod.tgz' \) | head -1 || true)"
  if [[ -n "$archive" ]]; then
    rm -rf "$DEST"
    mkdir -p "$DEST"
    tar -xzf "$archive" -C "$DEST"
    hit=true
    echo "[pack-mod] restored $REF → $DEST"
  else
    echo "[pack-mod] ORAS hit but no archive: $REF"
  fi
else
  echo "[pack-mod] miss $REF ($(head -c 120 "$tmp/err" 2>/dev/null || true))"
fi

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "pack_mod_hit=$hit" >> "$GITHUB_OUTPUT"
fi
echo "pack_mod_hit=$hit"
# Always exit 0 — caller branches on pack_mod_hit.
exit 0
