# CI build cache composites

Vendored from qtclient (`qtclient/qtclient/.github/actions/cache-*`).
Keep in sync when qtclient changes the contract (paths, keys, GHCR tip tags).

Use as `uses: ./.github/actions/cache-restore` (must live in **this** repo —
remote qtclient refs and `./_qtclient/...` break Release workflow startup).

Pack jobs must checkout **this** repo before idleot-client (into a subdir).
Checking idleot-client into `$GITHUB_WORKSPACE` wipes `.github/actions`.

`unique` keys vcpkg Actions blobs; `unique_ccache` (optional) keys ccache —
pack/tip jobs pass the qtclient SHA so consecutive IdleOT cuts share objects
instead of a new primary key per `pack_tag`.
