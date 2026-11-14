#!/usr/bin/env bash
# Ensure qtclient published client-* ORAS for SHA. If any slice is
# missing, wait on an existing Client:ci for that SHA (push/PR/dispatch), or
# dispatch Client:ci, then wait for ORAS.
#
# Usage: ensure-qtclient-host.sh <qtclient_sha> [release_id]
# Env: GH_TOKEN, HOST_OWNER (default qtclient), QTCLIENT_REPO (default qtclient/qtclient)
#
# workflow_dispatch --ref must be a branch or tag name (not a bare SHA). When
# SHA is tip of main/master we dispatch that branch; otherwise we create (or
# reuse) tag qtclient-host-<12hex>. Prefer watching an in-flight qtclient run so
# Release does not need cross-org workflow dispatch rights when qtclient already
# built on push (typical after qtclient PR merge).
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

find_qtclient_run() {
  # Prefer live runs (pending/queued/in_progress), else newest successful
  # completed run for this SHA. Skip completed failures so a bad prior
  # Client:ci does not block re-dispatch / ORAS publish.
  # "pending" is common right after push — must not be treated as terminal.
  gh run list -R "$QTCLIENT_REPO" -w ci-client.yml -L 40 \
    --json databaseId,headSha,status,conclusion,event \
    --jq "
      [.[] | select(.headSha == \"${SHA}\")] as \$all |
      (
        \$all | map(select(.status == \"in_progress\" or .status == \"queued\" or .status == \"pending\" or .status == \"waiting\"))
      ) + (
        \$all | map(select(.status == \"completed\" and .conclusion == \"success\"))
      ) |
      .[0].databaseId // empty
    "
}

watch_qtclient_run() {
  local run_id=$1
  echo "[qtclient-host] watching qtclient run ${run_id}"
  # gh run watch returns immediately on some pending states; poll until settled.
  local status conclusion
  for _ in $(seq 1 240); do
    status="$(gh run view "$run_id" -R "$QTCLIENT_REPO" --json status --jq .status)"
    conclusion="$(gh run view "$run_id" -R "$QTCLIENT_REPO" --json conclusion --jq '.conclusion // empty')"
    if [[ "$status" == "completed" ]]; then
      break
    fi
    # Also try watch once runners are live (no-op if still pending).
    if [[ "$status" == "in_progress" || "$status" == "queued" ]]; then
      gh run watch "$run_id" -R "$QTCLIENT_REPO" --interval 30 >/dev/null 2>&1 || true
      conclusion="$(gh run view "$run_id" -R "$QTCLIENT_REPO" --json conclusion --jq '.conclusion // empty')"
      status="$(gh run view "$run_id" -R "$QTCLIENT_REPO" --json status --jq .status)"
      [[ "$status" == "completed" ]] && break
    else
      sleep 15
    fi
  done
  echo "[qtclient-host] Client:ci: ${conclusion:-unknown} (status=${status})"
  [[ "$conclusion" == "success" ]]
}

dispatch_ref_for_sha() {
  local tip
  for branch in main master; do
    tip="$(gh api "repos/${QTCLIENT_REPO}/commits/${branch}" --jq .sha 2>/dev/null || true)"
    if [[ "$tip" == "$SHA" ]]; then
      echo "$branch"
      return 0
    fi
  done
  local tag="qtclient-host-${SHA:0:12}"
  if ! gh api "repos/${QTCLIENT_REPO}/git/ref/tags/${tag}" >/dev/null 2>&1; then
    echo "[qtclient-host] creating tag ${tag} @ ${SHA}" >&2
    gh api -X POST "repos/${QTCLIENT_REPO}/git/refs" \
      -f ref="refs/tags/${tag}" -f sha="$SHA" >/dev/null
  fi
  echo "$tag"
}

missing=false
for ref in "${need[@]}"; do
  if oras_present "$ref"; then
    echo "[qtclient-host] present $ref"
  else
    echo "[qtclient-host] missing $ref"
    missing=true
  fi
done

if [[ "$missing" == true ]]; then
  run_id="$(find_qtclient_run || true)"
  reuse=""
  if [[ -n "$run_id" ]]; then
    status="$(gh run view "$run_id" -R "$QTCLIENT_REPO" --json status,conclusion,event \
      --jq '"\(.event) \(.status) \(.conclusion)"')"
    echo "[qtclient-host] found Client:ci ${run_id} (${status})${RELEASE_ID:+ for ${RELEASE_ID}}"
    # pending/waiting/queued/in_progress → wait; completed success → done;
    # completed failure → fall through and dispatch a fresh Client:ci.
    if [[ "$status" == *" completed "* ]]; then
      conclusion="$(gh run view "$run_id" -R "$QTCLIENT_REPO" --json conclusion --jq '.conclusion // empty')"
      if [[ "$conclusion" == "success" ]]; then
        reuse=1
        echo "[qtclient-host] Client:ci: ${conclusion}"
      else
        echo "[qtclient-host] prior Client:ci ${conclusion} — dispatching a new run"
        run_id=""
      fi
    else
      reuse=1
      watch_qtclient_run "$run_id"
    fi
  fi
  if [[ -z "$reuse" ]]; then
    ref="$(dispatch_ref_for_sha)"
    echo "[qtclient-host] dispatching Client:ci @ ${ref} (${SHA})${RELEASE_ID:+ (${RELEASE_ID})}"
    if ! gh workflow run ci-client.yml -R "$QTCLIENT_REPO" --ref "$ref"; then
      echo "::error::failed to dispatch qtclient Client:ci (need workflow scope on ${QTCLIENT_REPO}, or wait for qtclient push CI)" >&2
      exit 1
    fi
    run_id=""
    for _ in $(seq 1 60); do
      run_id="$(find_qtclient_run || true)"
      # Prefer the newest non-failed / in-flight run after dispatch.
      [[ -n "$run_id" ]] && break
      sleep 5
    done
    if [[ -z "$run_id" ]]; then
      echo "::error::could not find Client:ci for qtclient ${SHA}" >&2
      exit 1
    fi
    watch_qtclient_run "$run_id"
  fi
fi

for ref in "${need[@]}"; do
  bash "$SCRIPT_DIR/wait-ghcr-artifact.sh" "$ref" 7200 90
done
echo "[qtclient-host] all client-* ORAS ready for ${SHA}"
