#!/usr/bin/env python3
"""Stamp IdleOT pack mod.json urls for production tips.

Local monorepo packs keep 127.0.0.1 loginBase / launchersettings URLS; release
CI must rewrite before the tip is published. Used by build-pack and tip assemble.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_HARDWARE_RECEIVER = "/clientservices/hardwarereceiver.php"


def stamp(doc: dict, *, site: str, asset: str, payments: str) -> dict:
    """Rewrite production bases in place; return the urls object for logging."""
    doc.pop("native", None)
    site = site.rstrip("/")
    urls = doc.setdefault("urls", {})
    urls["loginBase"] = site
    urls["assetBase"] = asset.rstrip("/")
    urls["paymentsBase"] = payments.rstrip("/")
    # CIP launchersettings URLS.systemSurvey is absolute (portal hardware receiver).
    survey = doc.setdefault("launchersettings", {}).setdefault("URLS", {})
    survey["systemSurvey"] = site + _HARDWARE_RECEIVER
    return urls


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mod_json", type=Path)
    p.add_argument("--site", required=True, help="loginBase (portal origin)")
    p.add_argument("--asset", required=True, help="assetBase (CDN tip)")
    p.add_argument("--payments", required=True, help="paymentsBase")
    args = p.parse_args()

    path: Path = args.mod_json
    doc = json.loads(path.read_text())
    urls = stamp(
        doc, site=args.site, asset=args.asset, payments=args.payments
    )
    path.write_text(json.dumps(doc, indent=2) + "\n")
    survey = (
        doc.get("launchersettings", {})
        .get("URLS", {})
        .get("systemSurvey", "")
    )
    print(
        f"[stamp-mod-urls] {path}: loginBase={urls['loginBase']} "
        f"assetBase={urls['assetBase']} paymentsBase={urls['paymentsBase']} "
        f"systemSurvey={survey}"
    )


if __name__ == "__main__":
    main()
