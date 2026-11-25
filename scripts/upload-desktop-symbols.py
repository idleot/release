#!/usr/bin/env python3
"""Breakpad symbols of a packaged desktop tip → the portal's symbol store.

    upload-desktop-symbols.py <dump_syms> <tip dir> [<extra binary or .pdb>...]

Dumps every Mach-O / ELF / PE file under the tip (client, launcher and the Qt
the tip ships), asks the portal whether it already has that module
(GET /api/telemetry/symbols?code_file=&debug_id=) and uploads only the missing
ones (POST multipart `file`). Env: TOKEN (QTCAAC_SYMBOL_UPLOAD_TOKEN), SITE.

The symbols go straight to the portal, never into an artifact or the log: this
repository is public. The log names modules and debug ids only.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

MAGICS = (
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit
    b"\xca\xfe\xba\xbe",  # Mach-O universal
    b"\x7fELF",
    b"MZ",  # PE
)
SKIP_SUFFIXES = {".a", ".o", ".obj", ".lib", ".qm", ".qml", ".json", ".png", ".txt"}


def is_binary(path: Path) -> bool:
    if path.is_symlink() or not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
        return False
    try:
        with path.open("rb") as f:
            head = f.read(4)
    except OSError:
        return False
    return any(head.startswith(m) for m in MAGICS)


# Per-request budget. A hung portal must not stall tip artifacts (v1.0.219
# linux sat in this script for >40m dumping every Qt .so).
REQUEST_TIMEOUT_S = 30
# Stop after this many consecutive portal failures (site down / auth).
MAX_CONSECUTIVE_FAILURES = 5
# Tip ships dozens of Qt libs; crash reports need the game + launcher first.
TIP_NAME_ALLOW = {
    "client",
    "client.exe",
    "tibia",
    "tibia.exe",
    "launcher",
    "launcher.exe",
}


def request(method: str, url: str, token: str, body: bytes | None = None, ctype: str | None = None) -> int:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code
    except (urllib.error.URLError, TimeoutError):
        return 0


def upload(site: str, token: str, sym: Path, name: str) -> int:
    boundary = uuid.uuid4().hex
    head = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
        "Content-Type: text/plain\r\n\r\n"
    ).encode()
    body = head + sym.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return request("POST", f"{site}/api/telemetry/symbols", token, body, f"multipart/form-data; boundary={boundary}")


def tip_binaries(tip: Path) -> list[Path]:
    """Client/launcher under tip/bin (+ .app on macOS), not the full Qt tree."""
    out: list[Path] = []
    bin_dir = tip / "bin"
    if not bin_dir.is_dir():
        return out
    for path in sorted(bin_dir.rglob("*")):
        if not is_binary(path):
            continue
        if path.name in TIP_NAME_ALLOW or path.stem in TIP_NAME_ALLOW:
            out.append(path)
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    dump_syms, tip = sys.argv[1], Path(sys.argv[2])
    token = os.environ.get("TOKEN", "")
    site = os.environ.get("SITE", "").rstrip("/")
    if not token or not site:
        print("[symbols] TOKEN/SITE not set — skip")
        return 0
    files = tip_binaries(tip)
    files += [Path(p) for p in sys.argv[3:] if Path(p).is_file()]
    print(f"[symbols] {len(files)} candidate binaries under {tip}")
    stored = present = failed = 0
    consecutive_fail = 0
    seen: set[tuple[str, str]] = set()
    with tempfile.TemporaryDirectory() as tmp:
        for path in files:
            out = Path(tmp) / "module.sym"
            run = subprocess.run([dump_syms, str(path), "-o", str(out)], capture_output=True)
            if run.returncode != 0 or not out.is_file():
                continue  # not a module dump_syms reads (scripts, data, PE without PDB)
            with out.open("r", encoding="utf-8", errors="replace") as f:
                parts = f.readline().split()
            if len(parts) < 5 or parts[0] != "MODULE":
                continue
            debug_id, code_file = parts[3], " ".join(parts[4:])
            if (code_file, debug_id) in seen:
                continue
            seen.add((code_file, debug_id))
            query = urllib.parse.urlencode({"code_file": code_file, "debug_id": debug_id})
            if request("GET", f"{site}/api/telemetry/symbols?{query}", token) == 204:
                present += 1
                consecutive_fail = 0
                continue
            code = upload(site, token, out, f"{Path(code_file).stem}.sym")
            if code == 201:
                stored += 1
                consecutive_fail = 0
                print(f"[symbols] {code_file} {debug_id} stored")
            else:
                failed += 1
                consecutive_fail += 1
                print(f"[symbols] {code_file} {debug_id} → HTTP {code}")
                if consecutive_fail >= MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"[symbols] aborting after {consecutive_fail} consecutive portal failures",
                        file=sys.stderr,
                    )
                    break
    print(f"[symbols] {stored} uploaded, {present} already stored, {failed} failed")
    return 1 if failed and not stored and not present else 0


if __name__ == "__main__":
    sys.exit(main())
