#!/usr/bin/env bash
# Actions-cache helper for CIP Qt kit tarballs under workspace/.cache/qt-kit/
# (never under $QT_ROOT install paths — those collide with Qt:build prefixes).
#
#   cache-qt-kit.sh pack <cip-sha> <kit-slug> <qt-root>
#     Pack installed prefix into .cache/qt-kit/<sha12>-<kit>.tar.gz
#   cache-qt-kit.sh unpack <cip-sha> <kit-slug> <qt-root>
#     Unpack into qt-root if the Actions blob exists; exit 1 on miss.
set -euo pipefail

CMD="${1:?pack|unpack}"
SHA="${2:?cip sha}"
KIT="${3:?kit slug}"
QT_ROOT="${4:?qt root}"
SHORT="${SHA:0:12}"
CACHE_ROOT="${GITHUB_WORKSPACE:-.}/.cache/qt-kit"
# gzip — available on all GHA runners without brew/apt; Actions cache re-compresses.
BLOB="$CACHE_ROOT/${SHORT}-${KIT}.tar.gz"

# shellcheck source=/dev/null
if [[ -f "${QTCLIENT_ROOT:-}/tools/qt/qt-version.sh" ]]; then
  source "${QTCLIENT_ROOT}/tools/qt/qt-version.sh"
elif [[ -f tools/qt/qt-version.sh ]]; then
  source tools/qt/qt-version.sh
elif [[ -f _qtclient/tools/qt/qt-version.sh ]]; then
  source _qtclient/tools/qt/qt-version.sh
else
  echo "[qt-kit-cache] qt-version.sh not found" >&2
  exit 1
fi

DIR="$(qt_install_dir_for_kit "$KIT")"
PREFIX="$QT_ROOT/cip-$SHORT/$DIR"

case "$CMD" in
  pack)
    if ! qt_prefix_has_qt6 "$PREFIX"; then
      echo "[qt-kit-cache] skip pack — no Qt at $PREFIX"
      exit 0
    fi
    mkdir -p "$CACHE_ROOT"
    echo "[qt-kit-cache] packing $PREFIX → $BLOB"
    tar -C "$QT_ROOT" -czf "$BLOB" "cip-$SHORT/$DIR"
    ls -lh "$BLOB"
    ;;
  unpack)
    [[ -f "$BLOB" ]] || { echo "[qt-kit-cache] miss $BLOB"; exit 1; }
    mkdir -p "$QT_ROOT"
    echo "[qt-kit-cache] unpacking $BLOB → $QT_ROOT"
    tar -C "$QT_ROOT" -xzf "$BLOB"
    if ! qt_prefix_has_qt6 "$PREFIX"; then
      echo "[qt-kit-cache] unpack missing Qt6Config under $PREFIX" >&2
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 pack|unpack <cip-sha> <kit> <qt-root>" >&2
    exit 2
    ;;
esac
