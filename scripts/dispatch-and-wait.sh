#!/usr/bin/env bash
# Dispatch a workflow and wait for it.
# Usage: dispatch-and-wait.sh <owner/repo> <workflow-file> <release_id> [key=value ...]
#
# Env:
#   DISPATCH_REF   git ref to run (default: main). Soft host publish uses the
#                  umbrella Soft pin SHA so Release never depends on Soft main.
#
# The run is found by its run-name, which must contain the release_id. Only the
# final conclusion is printed: this runs in a public repo, so step names and
# logs of private engine runs stay out of these logs.
set -euo pipefail

REPO="$1"
WORKFLOW="$2"
RELEASE_ID="$3"
shift 3

REF="${DISPATCH_REF:-main}"

fields=(-f "release_id=${RELEASE_ID}")
for kv in "$@"; do fields+=(-f "$kv"); done

gh workflow run "$WORKFLOW" -R "$REPO" --ref "$REF" "${fields[@]}" >/dev/null
echo "[dispatch] ${REPO##*/} ${WORKFLOW} @ ${REF} (${RELEASE_ID})"

run_id=""
for _ in $(seq 1 36); do
  run_id="$(gh run list -R "$REPO" -w "$WORKFLOW" -e workflow_dispatch -L 20 \
    --json databaseId,displayTitle \
    --jq ".[] | select(.displayTitle | contains(\"${RELEASE_ID}\")) | .databaseId" | head -n1)"
  [[ -n "$run_id" ]] && break
  sleep 5
done
if [[ -z "$run_id" ]]; then
  echo "::error::could not find the dispatched ${WORKFLOW} run in ${REPO##*/}"
  exit 1
fi

gh run watch "$run_id" -R "$REPO" --interval 30 >/dev/null 2>&1 || true
conclusion="$(gh run view "$run_id" -R "$REPO" --json conclusion --jq .conclusion)"
echo "[dispatch] ${REPO##*/} ${WORKFLOW}: ${conclusion}"
[[ "$conclusion" == "success" ]]
