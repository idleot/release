# idleot/cdn

The IdleOT CDN as one versioned bundle. The same tree is served either by the
Coolify image (`ghcr.io/idleot/cdn`, `cdn.idleot.com`) or by GitHub Pages, so
every client can point `IDLEOT_ASSET_BASE` at either origin.

## Layout

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

## Producers

| Slice | Image | Built by |
|-------|-------|----------|
| WASM | `ghcr.io/idleot/otclient-wasm` | `idleot/otclient` `deploy-wasm.yml` |
| Desktop zips | `ghcr.io/idleot/otclient-desktop` | `idleot/otclient` `release-desktop.yml` |
| Things + store | `ghcr.io/idleot/cdn-assets` | local `make -C apps/otclient publish-assets ARGS=--push` (CIP files never touch CI) |

Each producer sends a `repository_dispatch` (`cdn-bundle`) here when
`CDN_DISPATCH_TOKEN` is set; otherwise run **CDN bundle** manually.

## Targets

Repository variable `IDLEOT_CDN_TARGETS`: `image` (default), `pages`, or `image,pages`.

- **image** — Coolify app `idleot-cdn` runs `ghcr.io/idleot/cdn:latest`; the
  workflow calls the Coolify deploy API. Roll back by deploying an older tag.
- **pages** — the same `site/` tree is deployed to GitHub Pages (requires a
  public repo or a paid plan, and Pages enabled with source "GitHub Actions"). Pages caps the
  site at 1 GB, sends no custom cache/CORP headers and makes the CIP-derived
  things zip public; WASM still loads through the AAC same-origin `/cdn` proxy.

Switch origins by changing `IDLEOT_ASSET_BASE` on the AAC (and in the desktop
client env), or by pointing the `cdn.idleot.com` DNS at Pages.

## Secrets / variables

| Name | Kind | Purpose |
|------|------|---------|
| `GHCR_TOKEN` | secret | read producer packages + push `cdn` |
| `COOLIFY_URL`, `COOLIFY_TOKEN` | secret | image target deploy |
| `COOLIFY_CDN_UUID` | variable | Coolify `idleot-cdn` application |
| `IDLEOT_CDN_TARGETS` | variable | `image` / `pages` / `image,pages` |
