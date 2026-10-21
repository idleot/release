#!/usr/bin/env bash
# Fail fast when release pins lack GHCR images (warm builds should have pushed them).
# Env: REGISTRY, and any of OTC_SHA / CRYSTAL_SHA / PORTAL_SHA / CLIENT / SERVER / PORTAL
#      (CLIENT/SERVER/PORTAL are "true"/"false"; only check when true).
# DESKTOP_IMAGE defaults to tibia-desktop.
set -euo pipefail

REGISTRY="${REGISTRY:?}"
DESKTOP_IMAGE="${DESKTOP_IMAGE:-tibia-desktop}"
missing=0

need() {
  local image="$1" tag="$2" label="$3"
  if docker buildx imagetools inspect "${REGISTRY}/${image}:${tag}" >/dev/null 2>&1; then
    echo "[ok] ${image}:${tag}"
  else
    echo "::error::missing ${REGISTRY}/${image}:${tag} (${label}) — build it first (warm workflow) or pass build_if_missing=true"
    missing=1
  fi
}

if [[ "${CLIENT:-false}" == "true" ]]; then
  : "${OTC_SHA:?}"
  need otclient-wasm "$OTC_SHA" "client wasm"
  need "$DESKTOP_IMAGE" "$OTC_SHA" "client desktop"
fi
if [[ "${SERVER:-false}" == "true" ]]; then
  : "${CRYSTAL_SHA:?}"
  need crystalserver "$CRYSTAL_SHA" "game server"
fi
# Portal images are private to idleot/portal; skip imagetools here (Coolify pulls).

exit "$missing"
