#!/usr/bin/env python3
"""Fail if tip mods/<id>/mod.json is not loadable by qtclient ModLoader.

Catches merge-launcher-brand clobbering the pack meta with a launcher stub
(name/priority/urls only — no id / QTCLIENT_MOD_API). Linked tip: no native/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <mods/<id>/mod.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"missing {path}", file=sys.stderr)
        return 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    mid = (doc.get("id") or "").strip()
    api = int(doc.get("QTCLIENT_MOD_API") or 0)
    native = (doc.get("native") or "").strip()
    errors: list[str] = []
    if not mid:
        errors.append("missing non-empty id")
    if api < 1:
        errors.append(f"QTCLIENT_MOD_API must be >= 1 (got {api})")
    if native:
        errors.append(f"native={native!r} must be omitted (linked tip; no SIDE_MODULE)")
    if errors:
        print(f"::error::{path} is not a loadable tip pack: {'; '.join(errors)}", file=sys.stderr)
        print(json.dumps(doc, indent=2), file=sys.stderr)
        return 1
    print(f"[assert-tip-mod-json] ok id={mid} api={api} (linked tip FS)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
