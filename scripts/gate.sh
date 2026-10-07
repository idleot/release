#!/usr/bin/env bash
# Portal release gate: closes logins while server, portal and CDN switch over.
#   gate.sh close                  close logins and drop game sessions
#                                  (exit 3: the live portal has no gate yet)
#   gate.sh open [expectRevision]  reopen once the portal sees the CDN serving
#                                  expectRevision (latest/release.json revision)
# Env: SITE_URL, RELEASE_GATE_TOKEN, GATE_OPEN_TIMEOUT (seconds, default 180).
# Output is limited to statuses — no URLs or response bodies, since this runs
# in a public repo.
set -euo pipefail

: "${SITE_URL:?}" "${RELEASE_GATE_TOKEN:?}"
URL="${SITE_URL%/}/api/release/gate"

post() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST --max-time 30 \
    -H "Authorization: Bearer ${RELEASE_GATE_TOKEN}" -H "Content-Type: application/json" \
    -d "$1" "$URL")" || true
  echo "${code:-000}"
}

cmd="${1:-}"
case "$cmd" in
  close)
    for _ in $(seq 1 6); do
      code="$(post '{"open": false}')"
      case "$code" in
        2*) echo "[gate] closed"; exit 0 ;;
        404) echo "::warning::portal has no release gate yet — switching without one"; exit 3 ;;
        401|403) echo "::error::gate rejected the token (HTTP ${code})"; exit 1 ;;
      esac
      sleep 10
    done
    echo "::error::gate close failed (HTTP ${code})"; exit 1
    ;;
  open)
    expect="${2:-}"
    body="$(EXPECT="$expect" python3 -c 'import json,os; d={"open": True}; os.environ["EXPECT"] and d.update(expectRevision=os.environ["EXPECT"]); print(json.dumps(d))')"
    deadline=$((SECONDS + ${GATE_OPEN_TIMEOUT:-180}))
    while :; do
      code="$(post "$body")"
      case "$code" in
        2*) echo "[gate] open${expect:+ at revision ${expect}}"; exit 0 ;;
        401|403|404) echo "::error::gate open failed (HTTP ${code})"; exit 1 ;;
      esac
      # 409: the portal does not see the new revision on the CDN yet; 5xx/000: restarting.
      (( SECONDS < deadline )) || { echo "::error::gate did not open (HTTP ${code})"; exit 1; }
      echo "[gate] waiting (HTTP ${code})"
      sleep 10
    done
    ;;
  *)
    echo "usage: gate.sh close | open [expectRevision]" >&2; exit 2 ;;
esac
