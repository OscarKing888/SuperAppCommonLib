"""Import an exact PhotoCraft revision without rewriting upstream Rust sources.

Run explicitly, never during application startup/build. A manifest owns imported
paths; local modifications must be reconciled before a later import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CRATES = ("geom", "cms", "color", "raster", "doc", "vector", "paint", "algo", "compose")
EXTRAS = ("Cargo.toml", "Cargo.lock", "LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "ATTRIBUTION.md",
          "crates/io/src/camera_raw_xmp.rs")


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def sync(repo: Path, revision: str, *, check: bool = False) -> dict:
    commit = git(repo, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    upstream = ROOT / "upstream"
    manifest_path = ROOT / "upstream.json"
    old = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {"files": {}}
    for relative, expected in old["files"].items():
        path = upstream / relative
        if not path.resolve().is_relative_to(upstream.resolve()):
            raise ValueError("invalid import manifest path")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"locally modified imported file: {relative}; move changes to an adapter first")
    prefixes = tuple(f"crates/{crate}/" for crate in CRATES)
    rows = git(repo, "ls-tree", "-r", "-z", commit).split(b"\0")
    files = {}
    for row in rows:
        if not row:
            continue
        info, raw_path = row.split(b"\t", 1)
        name = raw_path.decode("utf-8")
        if name not in EXTRAS and not name.startswith(prefixes):
            continue
        if info.split()[0] not in (b"100644", b"100755") or ".." in PurePosixPath(name).parts:
            raise ValueError(f"unsupported upstream entry: {name}")
        files[name] = git(repo, "show", f"{commit}:{name}")
    result = {"repository": "https://github.com/storytold/photocraft", "revision": commit,
              "crates": list(CRATES), "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}
    if check:
        if old != result:
            raise ValueError("upstream snapshot does not match the requested revision")
        return result
    # Validate and read everything before replacing any owned file.
    for name, contents in files.items():
        path = upstream / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    for name in old["files"].keys() - files.keys():
        (upstream / name).unlink()
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = sync(args.repository, args.revision, check=args.check)
    print(f"PhotoCraft {result['revision']}: {len(result['files'])} files")
