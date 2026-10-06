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
resolve -> build client (here) | build server, AAC (dispatched to private repos)
        -> deploy server + AAC   (only after every selected build succeeded)
        -> promote client         (bundle.yml with the exact slice tags) -> deploy CDN
        -> :latest = live, GitHub Release on the umbrella tag
```

- **auto** — a component ships when its submodule pointer changed since the
  previous `v*` tag (`apps/otclient` → client, `apps/crystalserver` → server,
  `apps/slenderaac` → AAC). Bump a pointer to include a component; add
  `[release:all]` to the tag annotation to force everything.
- **Manual** — run **Release** with `components` = `all | client | server |
  aac | server+aac | assets`. `assets` re-bundles the live client with the
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
- Artifacts are only the encrypted desktop zips the CDN serves anyway
  (1-day retention). Slice and bundle images are private GHCR packages.
- No `pull_request` workflows; outside contributors need approval; the default
  token is read-only; secrets live in the `release` environment (main only).

## CDN bundle layout

All URLs inside manifests are relative to the bundle root.

```
latest/client-version.json   WASM revision + relative js/wasm/data paths
latest/desktop.json          schema 1; per-platform url/size/sha256
latest/sources.json          producer pointers + revision history (pruning)
client/<rev>/wasm/otclient.{js,wasm,data}[.gz|.br]   last 3 revisions
desktop/<rev>/idleot-{windows-x64,linux-x64,macos-arm64}.zip   last 2 revisions
assets/things/manifest.json + things-<version>.zip
assets/store/...             store icons (Crystal coinImagesURL)
healthz
```

| Slice | Image | Built by |
|-------|-------|----------|
| WASM | `ghcr.io/idleot/otclient-wasm:<otclient sha>` | `build-client.yml` |
| Desktop zips | `ghcr.io/idleot/otclient-desktop:<otclient sha>` | `build-client.yml` |
| Things + store | `ghcr.io/idleot/cdn-assets` | local `make -C apps/otclient publish-assets ARGS=--push` (CIP files never touch CI) |

Targets — repository variable `IDLEOT_CDN_TARGETS`: `image` (default), `pages`,
or `image,pages`. **image**: Coolify `idleot-cdn` runs `ghcr.io/idleot/cdn:<tag>`.
**pages**: the same tree on GitHub Pages (1 GB cap, no custom headers, makes the
things zip public; WASM still loads through the AAC same-origin `/cdn` proxy).

## Setup

| Name | Where | Purpose |
|------|-------|---------|
| `RELEASE_TOKEN` | `release` env secret | fine-grained PAT: Contents read on `otclient` + `idleot`, Actions read/write on `crystalserver` + `slenderaac` |
| `OTC_ENCRYPTION_PASSWORD`, `OTC_ENCRYPTION_HEADER` | `release` env secret | alphanumeric; password 100+ chars |
| `COOLIFY_URL`, `COOLIFY_TOKEN` | `release` env secret | Coolify API |
| `COOLIFY_SERVER_UUID`, `COOLIFY_AAC_UUID`, `COOLIFY_CDN_UUID` | `release` env variable | Coolify apps |
| `IDLEOT_CDN_TARGETS` | repo variable | `image` / `pages` / `image,pages` |

Every private package (`otclient-wasm`, `otclient-desktop`, `cdn`,
`cdn-assets`, `crystalserver`, `slenderaac`) grants this repo **Actions access:
Admin** under *Package settings → Manage Actions access*, so `GITHUB_TOKEN`
can read, push, retag and prune without changing package visibility.
