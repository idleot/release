#!/usr/bin/env python3
"""Stamp IdleOT pack mod.json urls for production tips.

Local monorepo packs keep 127.0.0.1 loginBase; release CI must rewrite before
the tip is published. Used by build-pack (via module.sh) and tip assemble.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mod_json", type=Path)
    p.add_argument("--site", required=True, help="loginBase (portal origin)")
    p.add_argument("--asset", required=True, help="assetBase (CDN tip)")
    p.add_argument("--payments", required=True, help="paymentsBase")
    args = p.parse_args()

    path: Path = args.mod_json
    doc = json.loads(path.read_text())
    urls = doc.setdefault("urls", {})
    urls["loginBase"] = args.site.rstrip("/")
    urls["assetBase"] = args.asset.rstrip("/")
    urls["paymentsBase"] = args.payments.rstrip("/")
    path.write_text(json.dumps(doc, indent=2) + "\n")
    print(
        f"[stamp-mod-urls] {path}: loginBase={urls['loginBase']} "
        f"assetBase={urls['assetBase']} paymentsBase={urls['paymentsBase']}"
    )


if __name__ == "__main__":
    main()
