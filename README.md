# idleot/release

Public release orchestrator for IdleOT, plus the CDN site tree sync. It is
public so client builds (macOS / Windows included) and waiting on other builds
cost no Actions minutes. Engine source stays in private repos.

## Cutting a release

**One step:** push submodule pins, then tag. Every tag is an **atomic full cut**
(`components=all`): qtclient host + IdleOT packs + server + portal build, then one
promote. Release does **not** depend on qtclient `main` already having published
the qtclient pin — it dispatches qtclient `Client:ci` at that SHA when host ORAS is
missing.

```bash
# After pushing qtclient / crystalserver / portal / idleot-client pins:
git tag -a v1.4.0 -m "notes"
git push origin v1.4.0
```

`idleot/idleot` `tag-release.yml` dispatches **Release** with `ref=v1.4.0`,
`components=all`, and `build_if_missing=true`.

```
resolve (all components)
  -> qtclient host dispatch ‖ server image ‖ portal image
  -> IdleOT packs (after qtclient ORAS) -> assemble tips (qtclient+pack tag)
  -> bundle -> verify -> record-live
  -> deploy server → CDN tip flip → portal | Pages
  -> :latest = live, GitHub Release on the umbrella tag
```

Client path:

1. **Ensure qtclient host** — `scripts/ensure-qtclient-host.sh` dispatches public
   `qtclient` `Client:ci` at the qtclient pin when `client-*` ORAS is missing, then
   waits for `client-sdk` / `client-wasm` / `client-<plat>:<sha>`.
2. **Build IdleOT packs** on this public repo (`build-pack.yml`) — checks out
   private `idleot-client` with `RELEASE_TOKEN`.
3. **Assemble** tip (merge `mods/idleot`) → GHCR slices tagged
   `${idleot_client_sha}-host-${qtclient_sha}` (and aliased to `:${qtclient_sha}`).

qtclient tibia/wasm is never compiled in this repo (only on qtclient).

There is **no portal login gate**. Releases match CIP: bounce/restart the game
world (clients see unreachable / connection-lost), flip
`https://static.idleot.com` tips, then portal. Package updates stay in the
launcher (`/launcher/tibiametadata.json`).

- **Promote** — `scripts/deployctl` with `IDLEOT_DEPLOY_BACKEND=compose-ssh`
  (default): SSH + `docker compose` at `/opt/idleot/stack`. Apps:
  `server` / `portal` / `cdn`.
- **CDN** — thin `ghcr.io/idleot/cdn:runtime-*` (nginx + config). Site tree on
  the host at `/data/idleot-cdn/html` (compose bind-mount). Bundle uploads
  immutable blobs with `sync-cdn.sh blobs`, then Deploy CDN runs
  `sync-cdn.sh flip` for `latest/` + `releases/`. Public tip:
  `https://static.idleot.com`.
- **Auto rollback** — restores server/portal tags and `sync-cdn.sh rollback`
  for `latest/` on the volume.

- **Tag cuts** — always `components=all` (server + clients + portal together).
- **Manual** — `components` = `auto | all | client | server | portal |
  launcher | server+portal | assets` (`auto` = pin-diff since previous `v*`).
- **Rollback** — older tag + `skip_build`.

### Speed (no warm dependency)

Wall-clock is `max(qtclient full matrix, server, portal) + packs + assemble +
promote`. qtclient wasm is usually the long pole. Optional: self-hosted Linux
runners with persistent ccache/Qt for qtclient linked tip + pack FS (label via qtclient /
Release `runs-on`; do not put builders on the game host). Compile caches speed
jobs; Release never assumes qtclient main already published the pin.

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
| WASM | `ghcr.io/idleot/otclient-wasm:<pack>-host-<soft>` (+ qtclient SHA alias) | `build-client.yml` (ensure qtclient host + pack, assemble tip) |
| Desktop store | `ghcr.io/idleot/tibia-desktop:<pack>-host-<soft>` (+ qtclient SHA alias) | same |
| Thin CDN runtime | `ghcr.io/idleot/cdn:runtime-<hash>` | `bundle.yml` (nginx config only) |
| Assets | `ghcr.io/idleot/cdn-assets` | `publish-assets --push` |

Targets — `IDLEOT_CDN_TARGETS`: `image` (default), `pages`, or `image,pages`.

## Setup

| Name | Where | Purpose |
|------|-------|---------|
| `RELEASE_TOKEN` | `release` env secret | PAT for private repos / dispatch |
| `CDN_SSH_HOST`, `CDN_SSH_KEY` | `release` env secret | rsync site tree + compose promote |
| `CDN_SSH_USER` | `release` env secret (optional) | default `root` |
| `IDLEOT_CDN_URL` | repo / env variable | default `https://static.idleot.com` (never `cdn.idleot.com`) |
| `IDLEOT_TIP_VARIANT` | assemble env (optional) | CipSoft tip `packages[].variant`: `regular` (prod, default), `test` (qa), `beta` (remote-dev), `preview` (reserved). Sets `directoryname` to `IdleOT{,Test,Beta,Preview}.app` |
| `IDLEOT_CDN_TARGETS` | repo variable | `image` / `pages` / `image,pages` |
| `CDN_REMOTE_ROOT` | `release` env variable (optional) | default `/data/idleot-cdn` |
| `COMPOSE_REMOTE_DIR` | `release` env variable (optional) | default `/opt/idleot/stack` |
| macOS / Windows signing secrets | `release` env (optional) | as before |

Host one-time: umbrella `deploy/stack/bootstrap.sh` (or OpenTofu SSH
bootstrap) installs Traefik + compose + Portainer and bind-mounts
`/data/idleot-cdn/html`. See [`deploy/README.md`](../../deploy/README.md).

Desktop launchers baked with `https://cdn.idleot.com` (retired) must be
reinstalled from `/download` after a release that ships `static.idleot.com`
as `IDLEOT_CDN_URL`. Tip `tibiametadata.json` absolute URLs also use static.

## Binary pack / update contract (CIP)

Desktop and browser share one tip protocol (see
[`docs/reverse-engineering/tibia-launcher-packaging.md`](../../docs/reverse-engineering/tibia-launcher-packaging.md)):

| Layer | CDN path | Installer |
|-------|----------|-----------|
| Launcher binary | `launcher/launcher-*-current/` | Bootstrap / first run |
| Client engine | `launcher/tibiaclient-*-current/` + `content/` | Launcher `installPackage` |
| Things / assets | `launcher/assets-current/` + `content/` (+ `/packs` zips on web) | Launcher `installAssets` / things packs **before Play** |
| Live tip | `latest/release.json`, `tibiametadata.json` | Tip flip only |
| CIP tips | `/launcher/…`, `/hints.json`, `/images/…` | Same origin as IdleOT `/latest` |

`emit_cip` (during Bundle assemble) writes `tibiametadata.json` for
`windows` / `macos` / `linux` / `web`. Payloads are lzma-alone, content-addressed
under `content/<aa>/<sha>`. Version stamps: `package.json.version` and
`assets.json.sha256`. Tip channel: set `IDLEOT_TIP_VARIANT` when assembling
(`regular` / `test` / `beta`); launcher clients set `IDLEOT_VARIANT` (+ optional
`IDLEOT_CDN_URL` for remote-dev) to install into the matching
`packages/<directoryname>/`. Local play uses `make client-start` (no tip).

Release path for binaries:

```text
tag → resolve → build missing images → assemble+emit_cip
  → sync-cdn blobs → server bounce (if needed) → sync-cdn flip latest/releases
```

- **Desktop:** `tibia-desktop` + installers → `tibiaclient-{windows,macos,linux}-current`
- **Browser:** `otclient-wasm` → `tibiaclient-web-current` + `launcher-web-current`
- **Things:** shared `assets-current` + packs for all platforms
- Portal `/play` and the desktop launcher both read `/launcher/tibiametadata.json` only

## Desktop launcher

The Qt launcher ships with the client/CDN bundle. Installers are listed by
`/download` from `latest/desktop.json`.

Every private package (`otclient-wasm`, `tibia-desktop`, `cdn`, `cdn-assets`,
`crystalserver`) grants this repo **Actions access: Admin**. Portal images stay
in `idleot/portal`; release only points Coolify at `portal:<sha>`.
