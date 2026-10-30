#!/usr/bin/env bash
# Pull an oras-pushed tar.gz layer into a directory.
# Usage: pull-oras-dir.sh <image:tag> <dest-dir>
set -euo pipefail

REF="${1:?}"
DEST="${2:?}"
rm -rf "$DEST"
mkdir -p "$DEST"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
(
  cd "$tmpdir"
  oras pull "$REF"
  layer="$(find . -type f | head -1)"
  [[ -n "$layer" ]] || { echo "no layer from $REF" >&2; exit 1; }
  case "$layer" in
    *.tgz|*.tar.gz) tar -xzf "$layer" -C "$DEST" ;;
    *)
      if tar -tzf "$layer" >/dev/null 2>&1; then
        tar -xzf "$layer" -C "$DEST"
      else
        # Already extracted by oras into cwd
        shopt -s dotglob
        cp -a ./* "$DEST/" 2>/dev/null || true
      fi
      ;;
  esac
)
echo "[pull] $REF → $DEST"
