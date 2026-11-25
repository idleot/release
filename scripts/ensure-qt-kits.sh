#!/usr/bin/env bash
# Fail-fast gate: CIP Qt kits must exist on GHCR before release pack/tip jobs
# burn hours in --pull-only. Pack jobs still pull the kits; this only checks
# manifests so a missing macOS/wasm kit fails the cut in minutes.
#
# Usage: ensure-qt-kits.sh <qtclient-root> [timeout_secs] [interval_secs]
# Env: QT_GHCR_IMAGE (default ghcr.io/qtclient/qt5), QT_CIP_SHA (optional override)
set -euo pipefail

QTCLIENT_ROOT="${1:?qtclient root}"
TIMEOUT="${2:-900}"
INTERVAL="${3:-30}"

# shellcheck source=/dev/null
source "$QTCLIENT_ROOT/tools/qt/qt-version.sh"
SHA="${QT_CIP_SHA:-$(qt_resolve_cip_sha)}"
[[ -n "$SHA" ]] || { echo "::error::no qt5-cip SHA (init qtclient tools/qt or set QT_CIP_SHA)" >&2; exit 1; }
SHORT="${SHA:0:12}"
IMAGE="${QT_GHCR_IMAGE:-ghcr.io/qtclient/qt5}"

kits=(
  linux-x64
  macos-arm64
  windows-x64
  wasm_multithread
)

oras_present() {
  local ref=$1
  if command -v oras >/dev/null 2>&1; then
    oras logout ghcr.io 2>/dev/null || true
    oras manifest fetch "$ref" >/dev/null 2>&1 && return 0
  fi
  docker buildx imagetools inspect "$ref" >/dev/null 2>&1 && return 0
  return 1
}

missing=()
for kit in "${kits[@]}"; do
  ref="${IMAGE}:${SHORT}-${kit}"
  if oras_present "$ref"; then
    echo "[qt-kits] present $ref"
  else
    echo "[qt-kits] missing $ref"
    missing+=("$kit")
  fi
done

if [[ ${#missing[@]} -eq 0 ]]; then
  echo "[qt-kits] all CIP Qt kits present for ${SHORT}"
  exit 0
fi

echo "[qt-kits] waiting up to ${TIMEOUT}s for: ${missing[*]}"
deadline=$((SECONDS + TIMEOUT))
while (( SECONDS < deadline )); do
  still=()
  for kit in "${missing[@]}"; do
    ref="${IMAGE}:${SHORT}-${kit}"
    if oras_present "$ref"; then
      echo "[qt-kits] present $ref"
    else
      still+=("$kit")
    fi
  done
  missing=("${still[@]+"${still[@]}"}")
  if [[ ${#missing[@]} -eq 0 ]]; then
    echo "[qt-kits] all CIP Qt kits present for ${SHORT}"
    exit 0
  fi
  left=$((deadline - SECONDS))
  echo "[qt-kits] still missing ${missing[*]} (${left}s left)"
  sleep "$INTERVAL"
done

echo "::error::CIP Qt kits missing after ${TIMEOUT}s for sha ${SHORT}: ${missing[*]}"
echo "::error::image ${IMAGE}:<sha12>-<kit> — publish kits (qtclient/Qt5) before cutting a release"
exit 1
