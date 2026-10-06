# idleot/release

Public release orchestrator for IdleOT, plus the CDN bundle. It is public so
the client builds (macOS / Windows included) and the time spent waiting on
other builds cost no Actions minutes. Engine source stays in private repos.

## Cutting a release

Tag the umbrella repo (private `idleot/idleot`). Its submodule pointers pin
every engine:

```bash
git tag -a v1.4.0 -m "notes [release:all]"   # [release:all] optional
git push origin v1.4.0
```

`idleot/idleot` `tag-release.yml` dispatches **Release** here with `ref=v1.4.0`.

```
resolve -> build client, launcher (here) | build server, portal (dispatched to private repos)
        -> deploy server + portal (only after every selected build succeeded)
        -> promote client         (bundle.yml with the exact slice tags) -> deploy CDN
        -> :latest = live, GitHub Release on the umbrella tag
```

- **auto** — a component ships when its submodule pointer changed since the
  previous `v*` tag (`apps/otclient` → client, `apps/crystalserver` → server,
  `apps/portal` → portal; older tags with `apps/slenderaac` still resolve). Bump a pointer to include a component; add
  `[release:all]` to the tag annotation to force everything. The launcher
  ships when the pinned portal's `apps/launcher/package.json` `version` changed
  (bump it to publish a launcher; `launcher:<version>` is never rebuilt).
- **Manual** — run **Release** with `components` = `all | client | server |
  portal | launcher | server+portal | assets` (`aac` is an alias for `portal`). `assets` re-bundles the live client with the
  newest `cdn-assets` (after `publish-assets --push`).
- **Rollback** — run **Release** with an older tag and `skip_build`. Images are
  immutable `:<sha>` tags (last 10 kept), so nothing rebuilds.
- Builds are skipped when the `:<sha>` image already exists.

## Privacy

This repo is public; the engines are not.

- Client Lua/OTUI/OTMOD ship encrypted (`OTC_ENCRYPTION_*`, otclient
  `tools/publish/encrypt_assets.py` + `ENABLE_ENCRYPTION`). This is
  obfuscation — the key is inside the binary — not a secret store.
- Compiler output is written to files; logs show only an error summary
  (`-w`, no carets). Cross-repo builds report only their conclusion.
- No layer or compiler caches of product code; only third-party vcpkg/emsdk.
- Artifacts are only the encrypted desktop content store the CDN serves
  anyway (1-day retention). Slice and bundle images are private
  GHCR packages.
- No `pull_request` workflows; outside contributors need approval; the default
  token is read-only; secrets live in the `release` environment (main only).

## CDN bundle layout

All URLs inside manifests are relative to the bundle root.

```
latest/client-version.json   {revision, productSha, assetsRevision} (portal login needUpdate check)
latest/desktop.json          schema 2, kind "launcher": per-platform launcher installer url/file/format/size/sha256
latest/update.json           fixed {engine: "launcher"}: pre-launcher installs offer a reinstall from /download
latest/sources.json          producer pointers + revision history (pruning)
latest/release.json          schema 3: targets.{web,desktop}.{content,things} / targets.<platform>.files, web.wasm
releases/<rev>/*.json        v3 file lists {revision, engine, entry?, files{path: {sha256, size, exec?}}, packs?}
releases/things-<rev>/content-things.json   sprites (data/things/<version>), merged into web/desktop by client-sync
content/<aa>/<sha256>        content-addressed blobs (immutable; pruned with releases/)
packs/<aa>/<sha256>.zip      ~16 MB zips of list blobs (one round trip for cold installs)
client/<rev>/wasm/otclient.{js,wasm}[.gz|.br]   last 3 revisions
launcher/latest.json         Tauri updater manifest (absolute URLs, minisign signatures)
launcher/<version>/IdleOT-Setup.exe | IdleOT.dmg | IdleOT.app.tar.gz | IdleOT-x86_64.AppImage (+ .sig)   last 2 versions
assets/store/...             store icons (Crystal coinImagesURL; committed in portal apps/web)
assets/outfits/manifest.json + <rev>/<looktype>/...   portal outfit frames (rendered by make extract)
assets/items/manifest.json + <rev>/<id>.png           portal item sprites (rendered by make extract)
healthz
```

| Slice | Image | Built by |
|-------|-------|----------|
| WASM | `ghcr.io/idleot/otclient-wasm:<otclient sha>` | `build-client.yml` |
| v3 engine/content lists, blobs and packs | `ghcr.io/idleot/otclient-desktop:<otclient sha>` | `build-client.yml` |
| Launcher installers + signed update artifacts | `ghcr.io/idleot/launcher:<version>` | `build-launcher.yml` (Tauri, from `idleot/portal` `apps/launcher`) |
| Things (content-things list) + store + outfits + items | `ghcr.io/idleot/cdn-assets` | local `make -C apps/otclient publish-assets ARGS=--push` (CIP files never touch CI) |

Targets — repository variable `IDLEOT_CDN_TARGETS`: `image` (default), `pages`,
or `image,pages`. **image**: Coolify `idleot-cdn` runs `ghcr.io/idleot/cdn:<tag>`.
**pages**: the same tree on GitHub Pages (1 GB cap, no custom headers, makes the
things blobs public; WASM still loads through the portal same-origin `/cdn` proxy).

## Setup

| Name | Where | Purpose |
|------|-------|---------|
| `RELEASE_TOKEN` | `release` env secret | fine-grained PAT: Contents read on `otclient` + `idleot` + `portal` (launcher source), Actions read/write on `crystalserver` + `portal` |
| `TAURI_SIGNING_PRIVATE_KEY` (+ optional `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`) | `release` env secret | signs launcher updates; the public key is `apps/launcher/src-tauri/tauri.conf.json` `plugins.updater.pubkey` |
| `OTC_ENCRYPTION_PASSWORD`, `OTC_ENCRYPTION_HEADER` | `release` env secret | alphanumeric; password 100+ chars |
| `COOLIFY_URL`, `COOLIFY_TOKEN` | `release` env secret | Coolify API |
| `COOLIFY_SERVER_UUID`, `COOLIFY_PORTAL_UUID` (falls back to `COOLIFY_AAC_UUID`), `COOLIFY_CDN_UUID` | `release` env variable | Coolify apps |
| `IDLEOT_CDN_TARGETS` | repo variable | `image` / `pages` / `image,pages` |
| `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` + vars `TRUSTED_SIGNING_ENDPOINT`, `TRUSTED_SIGNING_ACCOUNT`, `TRUSTED_SIGNING_PROFILE` | `release` env (optional) | Windows signing (Azure Trusted Signing) of the client and launcher; unsigned when absent |
| `MACOS_CERTIFICATE_P12` (base64), `MACOS_CERTIFICATE_PASSWORD`, `MACOS_SIGN_IDENTITY`, `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | `release` env secret (optional) | macOS Developer ID signing + notarization of the client and launcher; ad-hoc when absent |
| `IDLEOT_SITE_URL`, `IDLEOT_CDN_URL` | `release` env variable (optional) | portal / CDN origins baked into the launcher and `launcher/latest.json` (default production) |

## Desktop launcher

The IdleOT launcher (`build-launcher.yml`) is the only desktop installer:
Windows NSIS (per-user), macOS DMG, Linux AppImage, listed by `/download`
from `latest/desktop.json`. It updates itself from `launcher/latest.json` and
installs/updates the game (engine + `content-desktop` + `content-things`) from
`latest/release.json`, the same lists the browser `/play` syncs. Release game
builds (`release_env.lua` `IDLEOT_REVISION`) refuse to start outside the
launcher and send players to `/download`. Installs from before the launcher
read the fixed `latest/update.json`, whose foreign engine makes them offer the
same reinstall.

Every private package (`otclient-wasm`, `otclient-desktop`, `launcher`, `cdn`,
`cdn-assets`, `crystalserver`) grants this repo **Actions access:
Admin** under *Package settings → Manage Actions access*, so `GITHUB_TOKEN`
can read, push, retag and prune without changing package visibility.
`portal` is the exception: this repo never touches it. The release points
Coolify at `ghcr.io/idleot/portal:<sha>` (setting the image name too) and
dispatches `idleot/portal` `build-image.yml` with `latest=true` to move `:latest`.
