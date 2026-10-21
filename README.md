# idleot/release

Public release orchestrator for IdleOT, plus the CDN site tree sync. It is
public so client builds (macOS / Windows included) and waiting on other builds
cost no Actions minutes. Engine source stays in private repos.

## Cutting a release

**Warm images first**, then tag. The release path does not compile by default.

```bash
# After pushing otclient / crystalserver / portal SHAs that will be tagged:
gh workflow run warm-client.yml -R idleot/release -f otclient_sha=<full-otclient-sha>
# crystalserver: build-image runs on push to main (or workflow_dispatch)
# portal: dispatch build-image.yml in idleot/portal

git tag -a v1.4.0 -m "notes [release:all]"   # [release:all] optional
git push origin v1.4.0
```

`idleot/idleot` `tag-release.yml` dispatches **Release** here with `ref=v1.4.0`
and `build_if_missing=false`. Missing `:<sha>` tags fail fast.

```
resolve -> require warm GHCR images (or build_if_missing)
        -> bundle      (assemble site; push thin nginx runtime; artifact site/)
        -> close-gate  (record live tags; portal stops issuing game sessions)
        -> deploy CDN volume sync | server | portal | Pages   (in parallel)
        -> open-gate   (portal confirms latest/release.json is the new revision)
        -> :latest = live, GitHub Release on the umbrella tag
```

When images are already warm, the flip is assemble + rsync + Coolify switches
(typically minutes, not an hour). Pass `build_if_missing=true` only when you
intentionally want the release to compile.

- **Gate** — `scripts/gate.sh` calls the portal `POST /api/release/gate`
  (Bearer `RELEASE_GATE_TOKEN`). Closing it also deletes every game session
  key. Opening waits until the portal reads the new `latest/release.json`
  revision. The gate closes only for server or client releases.
- **CDN** — thin `ghcr.io/idleot/cdn:runtime-*` (nginx + config). The site tree
  lives on the Coolify host at `/data/idleot-cdn/html` (source of truth for
  blobs). Bundle pulls only `latest/` + `releases/` into CI, assembles deltas,
  then `sync-cdn.sh` merges content/packs/client/launcher additively and
  replaces `latest/` + `releases/`. Coolify mounts the volume read-only.
- **Auto rollback** — restores server/portal Coolify tags and
  `sync-cdn.sh rollback` for `latest/` on the volume.
- **Stuck closed:** `SITE_URL=https://www.idleot.com RELEASE_GATE_TOKEN=... bash scripts/gate.sh open`.

- **auto** — a component ships when its submodule pointer changed since the
  previous `v*` tag. Add `[release:all]` to force everything.
- **Manual** — `components` = `all | client | server | portal | launcher |
  server+portal | assets`.
- **Rollback** — older tag + `skip_build`.
- **Warm client** — `warm-client.yml` (or release with `build_if_missing`).

## Privacy

This repo is public; the engines are not.

- Product client is **tibia** (compiled QML + gamecore). Desktop slice is
  `tibia-desktop`; WASM slice is tibia artifacts under `otclient-wasm`.
- Compiler output goes to files; logs show only an error summary.
- Opaque caches only: pinned Qt SDK/emsdk and **ccache** object stores (no
  source trees in cache artifacts).
- Site artifacts retain 2 days; slice images are private GHCR packages.
- No `pull_request` workflows; secrets live in the `release` environment.

## CDN bundle layout

All URLs inside manifests are relative to the bundle root.

```
latest/client-version.json   {revision, productSha, assetsRevision}
latest/desktop.json          launcher installers
latest/release.json          schema 3 targets
releases/<rev>/*.json        v3 file lists
content/<aa>/<sha256>        content-addressed blobs
packs/<aa>/<sha256>.zip      zip packs
client/<rev>/wasm/...        WASM (kept revisions)
launcher/...                 launcher installers
assets/...                   store / outfits / items
healthz
```

| Slice | Image | Built by |
|-------|-------|----------|
| WASM | `ghcr.io/idleot/otclient-wasm:<otclient sha>` | `build-client.yml` / `warm-client.yml` |
| Desktop store | `ghcr.io/idleot/tibia-desktop:<otclient sha>` | same |
| Thin CDN runtime | `ghcr.io/idleot/cdn:runtime-<hash>` | `bundle.yml` (nginx config only) |
| Assets | `ghcr.io/idleot/cdn-assets` | `publish-assets --push` |

Targets — `IDLEOT_CDN_TARGETS`: `image` (default), `pages`, or `image,pages`.

## Setup

| Name | Where | Purpose |
|------|-------|---------|
| `RELEASE_TOKEN` | `release` env secret | PAT for private repos / dispatch |
| `COOLIFY_URL`, `COOLIFY_TOKEN` | `release` env secret | Coolify API |
| `RELEASE_GATE_TOKEN` | `release` env secret | login gate |
| `CDN_SSH_HOST`, `CDN_SSH_KEY` | `release` env secret | rsync site tree to `/data/idleot-cdn` |
| `CDN_SSH_USER` | `release` env secret (optional) | default `root` |
| `COOLIFY_SERVER_UUID`, `COOLIFY_PORTAL_UUID`, `COOLIFY_CDN_UUID` | `release` env variable | Coolify apps |
| `IDLEOT_CDN_TARGETS` | repo variable | `image` / `pages` / `image,pages` |
| `CDN_REMOTE_ROOT` | `release` env variable (optional) | default `/data/idleot-cdn` |
| macOS / Windows signing secrets | `release` env (optional) | as before |

Host one-time: `deploy/coolify-host-setup.sh` seeds `/data/idleot-cdn/html` and
documents Coolify `custom_docker_run_options`:
`-v /data/idleot-cdn/html:/usr/share/nginx/html:ro`. Set Coolify
**concurrent builds ≥ 3** so server/portal/CDN can flip together.

## Desktop launcher

The Qt launcher ships with the client/CDN bundle. Installers are listed by
`/download` from `latest/desktop.json`.

Every private package (`otclient-wasm`, `tibia-desktop`, `cdn`, `cdn-assets`,
`crystalserver`) grants this repo **Actions access: Admin**. Portal images stay
in `idleot/portal`; release only points Coolify at `portal:<sha>`.
