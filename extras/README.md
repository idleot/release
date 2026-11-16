# CDN `/extras` (Magical Archive)

Loose `spells.json` + `spells-previews.json` for `/play` (`ensureSpellsExtrasFromCdn`).

Preferred source is the `cdn-assets` image (`publish_assets.sh` → `SLICE/extras/`).
These checked-in copies bridge older assets tips until the next `publish-assets --push`.

Refresh from a machine with extract:

```bash
cp apps/qtclient/data/assets/spells*.json apps/release/extras/
```
