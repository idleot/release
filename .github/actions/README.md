# CI build cache composites

Vendored from Soft (`qtclient/qtclient/.github/actions/cache-*`).
Keep in sync when Soft changes the contract (paths, keys, GHCR tip tags).

Use as `uses: ./.github/actions/cache-restore` (must live in **this** repo —
remote Soft refs and `./_qtclient/...` break Release workflow startup).

Pack jobs must checkout **this** repo before idleot-client (into a subdir).
Checking idleot-client into `$GITHUB_WORKSPACE` wipes `.github/actions`.
