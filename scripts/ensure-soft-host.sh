#!/usr/bin/env bash
# Ensure Soft (qtclient) published client-* ORAS for SHA. If any slice is
# missing, dispatch Client:ci at that SHA (Release-owned; no Soft-main warm
# dependency) then wait for ORAS.
#
# Usage: ensure-soft-host.sh <qtclient_sha> [release_id]
# Env: GH_TOKEN, HOST_OWNER (default qtclient), QTCLIENT_REPO (default qtclient/qtclient)
#
# Does not pass workflow inputs (older Soft pins may lack release_id). The
# dispatched run is matched by headSha + workflow_dispatch.
set -euo pipefail

SHA="${1:?qtclient sha}"
RELEASE_ID="${2:-}"
HOST_OWNER="${HOST_OWNER:-qtclient}"
QTCLIENT_REPO="${QTCLIENT_REPO:-qtclient/qtclient}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

need=(
  "ghcr.io/${HOST_OWNER}/client-sdk:${SHA}"
  "ghcr.io/${HOST_OWNER}/client-wasm:${SHA}"
  "ghcr.io/${HOST_OWNER}/client-linux-x64:${SHA}"
  "ghcr.io/${HOST_OWNER}/client-windows-x64:${SHA}"
  "ghcr.io/${HOST_OWNER}/client-macos-arm64:${SHA}"
)

oras_present() {
  local ref=$1
  docker buildx imagetools inspect "$ref" >/dev/null 2>&1 && return 0
  command -v oras >/dev/null 2>&1 && oras manifest fetch "$ref" >/dev/null 2>&1 && return 0
  return 1
}

missing=false
for ref in "${need[@]}"; do
  if oras_present "$ref"; then
    echo "[soft-host] present $ref"
  else
    echo "[soft-host] missing $ref"
    missing=true
  fi
done

if [[ "$missing" == true ]]; then
  echo "[soft-host] dispatching Client:ci @ ${SHA}${RELEASE_ID:+ (${RELEASE_ID})}"
  # No -f inputs: Soft pins before release_id still accept bare workflow_dispatch.
  gh workflow run ci-client.yml -R "$QTCLIENT_REPO" --ref "$SHA" >/dev/null
  run_id=""
  for _ in $(seq 1 60); do
    run_id="$(gh run list -R "$QTCLIENT_REPO" -w ci-client.yml -e workflow_dispatch -L 30 \
      --json databaseId,headSha,status \
      --jq ".[] | select(.headSha == \"${SHA}\") | .databaseId" | head -n1)"
    [[ -n "$run_id" ]] && break
    sleep 5
  done
  if [[ -z "$run_id" ]]; then
    echo "::error::could not find Client:ci workflow_dispatch for Soft ${SHA}"
    exit 1
  fi
  echo "[soft-host] watching Soft run ${run_id}"
  gh run watch "$run_id" -R "$QTCLIENT_REPO" --interval 30 >/dev/null 2>&1 || true
  conclusion="$(gh run view "$run_id" -R "$QTCLIENT_REPO" --json conclusion --jq .conclusion)"
  echo "[soft-host] Client:ci: ${conclusion}"
  [[ "$conclusion" == "success" ]]
fi

for ref in "${need[@]}"; do
  bash "$SCRIPT_DIR/wait-ghcr-artifact.sh" "$ref" 7200 90
done
echo "[soft-host] all client-* ORAS ready for ${SHA}"
