"""Regenerate linux/release.json, the manifest every installed copy checks at game launch.

Run after changing anything a player should receive, then commit and push to the fork:
    python linux/make_release.py            # bump revision, rehash tool/runtime/checker/overlay
    python linux/make_release.py --check    # verify the committed hashes without changing anything
    python linux/make_release.py --bump     # new revision without file changes: players re-validate

Fixes on top of the author's package go to linux/overlay/<path inside the mod folder>,
e.g. linux/overlay/src/mod.lua replaces mods/ru_voice_separate_525/src/mod.lua.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "linux/release.json"
PACKAGE = {
    "name": "Voices-of-Embervale-RU-0.3.3-beta-Nexus.zip",
    "url": "https://github.com/Daber-devNull/voices-of-embervale-ru/releases/download/v0.3.3-beta/"
           "Voices-of-Embervale-RU-0.3.3-beta-Nexus.zip",
    "sha256": "064497de79459c626e4e9a1e34f3a8dfbc8b667bd4e241ecc5e316447806ece4",
    "files": 1764,
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entry(rel: str) -> dict:
    data = (ROOT / rel).read_bytes()
    if b"\r\n" in data and rel.endswith((".py", ".lua", ".sh")):
        sys.exit(f"{rel} has CRLF line endings; GitHub serves LF, the hash would not match")
    return {"path": rel, "sha256": sha(data)}


def build(revision: int) -> dict:
    checker = entry("linux/bin/emm-check.gz")
    checker["unpacked_sha256"] = sha(gzip.decompress((ROOT / checker["path"]).read_bytes()))
    overlay_root = ROOT / "linux/overlay"
    overlay = {path.relative_to(overlay_root).as_posix(): entry(path.relative_to(ROOT).as_posix())
               for path in sorted(overlay_root.rglob("*")) if path.is_file()} if overlay_root.is_dir() else {}
    return {
        "revision": revision,
        "name": "0.3.3-beta+steamos",
        "maps": 1118,
        "package": PACKAGE,
        "tool": entry("linux/ru_voice_steamos.py"),
        "runtime": entry("linux/bin/dbghelp.dll"),
        "checker": checker,
        "overlay": overlay,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--bump", action="store_true")
    args = parser.parse_args()
    current = json.loads(RELEASE.read_text(encoding="utf-8")) if RELEASE.is_file() else {"revision": 0}
    if args.check:
        fresh = build(current["revision"])
        if fresh != current:
            sys.exit("release.json is out of date: run python linux/make_release.py")
        print(f"release.json OK (revision {current['revision']})")
        return
    unchanged = build(current["revision"])
    if unchanged == current and not args.bump:
        print(f"Nothing changed (revision {current['revision']})")
        return
    release = build(current["revision"] + 1)
    RELEASE.write_bytes((json.dumps(release, ensure_ascii=False, indent=2) + "\n").encode())
    print(f"release.json -> revision {release['revision']}")


if __name__ == "__main__":
    main()
