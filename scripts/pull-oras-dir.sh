#!/usr/bin/env bash
# Pull an oras-pushed tar.gz layer into a directory.
# Usage: pull-oras-dir.sh <image:tag> <dest-dir>
set -euo pipefail

REF="${1:?}"
DEST="${2:?}"

# Git Bash `tar -C` rejects Actions `D:\a\_temp\…` (backslash escapes). Prefer /d/a/….
if command -v cygpath >/dev/null 2>&1; then
  case "$DEST" in
    [A-Za-z]:*|*\\*) DEST="$(cygpath -u "$DEST")" ;;
  esac
fi

rm -rf "$DEST"
mkdir -p "$DEST"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
(
  cd "$tmpdir"
  oras pull "$REF"
  # Prefer the archive layer — oras also drops the OCI manifest JSON, and
  # `find | head -1` can pick that first (no extract → empty dest).
  layer="$(find . -type f \( -name '*.tgz' -o -name '*.tar.gz' -o -name '*.tar.zst' \) | head -1 || true)"
  if [[ -z "$layer" ]]; then
    layer="$(find . -type f ! -name '*.json' | head -1 || true)"
  fi
  [[ -n "$layer" ]] || { echo "no layer from $REF" >&2; exit 1; }
  case "$layer" in
    *.tar.zst)
      if command -v zstd >/dev/null 2>&1; then
        zstd -d -c "$layer" | tar -x -C "$DEST"
      else
        echo "zstd required for $layer" >&2
        exit 1
      fi
      ;;
    *.tgz|*.tar.gz)
      tar -xzf "$layer" -C "$DEST"
      ;;
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
