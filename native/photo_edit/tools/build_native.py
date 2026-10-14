"""Build the pinned photo editor extension beside the current checkout's Python code.

Uses the caller's interpreter and an isolated, generated Cargo workspace. Never
installs app_common into the shared virtualenv or edits the upstream snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build_fingerprint() -> str:
    digest = hashlib.sha256()
    paths = [ROOT / name for name in ("upstream.json", "Cargo.lock", "rust-toolchain.toml", "tools/build_native.py")]
    paths += sorted(p for p in (ROOT / "bridge").rglob("*") if p.is_file())
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    digest.update(str(sysconfig.get_config_var("EXT_SUFFIX")).encode())
    return digest.hexdigest()


def copy_changed(source: Path, destination: Path) -> None:
    if not destination.exists() or source.read_bytes() != destination.read_bytes():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def write_changed(path: Path, text: str) -> None:
    data = text.encode("utf-8")
    if not path.exists() or path.read_bytes()!=data:
        path.write_bytes(data)


def dependency_notices(workspace: Path) -> str:
    """Collect original license text for resolved runtime/build dependencies."""
    version = subprocess.check_output(["rustc","-vV"],cwd=workspace,text=True)
    host = next(line.split(": ",1)[1] for line in version.splitlines() if line.startswith("host: "))
    metadata = json.loads(subprocess.check_output(["cargo","metadata","--locked","--offline",
        "--format-version","1","--filter-platform",host],cwd=workspace))
    packages = {p["id"]:p for p in metadata["packages"]}
    nodes = {p["id"]:p for p in metadata["resolve"]["nodes"]}
    pending = [p["id"] for p in packages.values() if p["name"]=="superbird-photo-edit-native"]
    selected = set()
    while pending:
        key = pending.pop()
        if key in selected:
            continue
        selected.add(key)
        pending.extend(d["pkg"] for d in nodes[key]["deps"] if any(k["kind"]!="dev" for k in d["dep_kinds"]))
    parts = ["Rust adapter dependency notices (including build tools).\nOriginal PhotoCraft notices are provided separately.\n"]
    for package in sorted((packages[key] for key in selected if packages[key]["source"]),key=lambda p:(p["name"],p["version"])):
        folder = Path(package["manifest_path"]).parent
        paths = {p for p in folder.iterdir() if p.is_file() and
                 p.name.upper().startswith(("LICENSE","LICENCE","COPYING","NOTICE"))}
        if package.get("license_file"):
            paths.add(folder/package["license_file"])
        if not paths:
            raise RuntimeError(f"Missing license text for {package['name']} {package['version']}")
        parts.append(f"\n{'='*72}\n{package['name']} {package['version']}\n{package.get('repository') or ''}\nLicense: {package.get('license') or ''}\n")
        for path in sorted(paths):
            parts.append(f"\n--- {path.name} ---\n"+path.read_text("utf-8"))
    return "\n".join(parts)


def prepare() -> Path:
    meta = json.loads((ROOT / "upstream.json").read_text("utf-8"))
    workspace = ROOT / "workspace"
    for name, expected in meta["files"].items():
        source = ROOT / "upstream" / name
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError(f"PhotoCraft snapshot modified: {name}")
        if name.startswith("crates/") and not name.startswith("crates/io/"):
            copy_changed(source, workspace / name)
    for source in (ROOT / "bridge").rglob("*"):
        if source.is_file():
            copy_changed(source, workspace / "bridge" / source.relative_to(ROOT / "bridge"))
    copy_changed(ROOT / "upstream/crates/io/src/camera_raw_xmp.rs", workspace / "reference/camera_raw_xmp.rs")
    upstream = tomllib.loads((ROOT / "upstream/Cargo.toml").read_text("utf-8"))
    package = upstream["workspace"]["package"]
    manifest = ['[workspace]', 'resolver = "3"', 'members = ["crates/*", "bridge"]',
                '[workspace.package]']
    manifest += [f'{key} = {json.dumps(value)}' for key, value in package.items()]
    manifest += ['[workspace.dependencies]']
    manifest += [f'photocraft-{crate} = {{ path = "crates/{crate}" }}' for crate in meta["crates"]]
    manifest += ['serde = { version = "1", features = ["derive", "rc"] }', 'serde_json = "1"',
                 'rayon = "1"', 'proptest = "1"', 'criterion = { version = "0.5", default-features = false }',
                 '[workspace.lints.rust]', 'unsafe_code = "forbid"',
                 '[workspace.lints.clippy]', 'dbg_macro = "warn"', 'todo = "warn"',
                 '[profile.release]', 'lto = "thin"', 'codegen-units = 1',
                 '[profile.dev]', 'opt-level = 1', 'debug = 1']
    workspace.mkdir(parents=True, exist_ok=True)
    write_changed(workspace / "Cargo.toml","\n".join(manifest) + "\n")
    write_changed(workspace / "upstream_revision.txt",meta["revision"])
    if (ROOT / "Cargo.lock").exists():
        copy_changed(ROOT / "Cargo.lock", workspace / "Cargo.lock")
    return workspace


def build(*, update_lock=False, prepare_only=False, debug=False) -> Path:
    workspace = prepare()
    env = dict(os.environ, PYO3_PYTHON=sys.executable, CARGO_TARGET_DIR=str(ROOT / "target"))
    if update_lock:
        subprocess.run(["cargo", "generate-lockfile"], cwd=workspace, env=env, check=True)
        copy_changed(workspace / "Cargo.lock", ROOT / "Cargo.lock")
    if prepare_only:
        return workspace
    if not (ROOT / "Cargo.lock").exists():
        raise RuntimeError("Missing dependency lock; maintainers must run --update-lock explicitly")
    out = ROOT / "wheels"
    command = [sys.executable, "-m", "maturin", "build", "--manifest-path", "bridge/Cargo.toml",
               "--out", str(out), "--interpreter", sys.executable, "--locked"]
    if not debug:
        command.append("--release")
    subprocess.run(command, cwd=workspace, env=env, check=True)
    wheels = list(out.glob("*.whl"))
    wheel = max(wheels, key=lambda p: p.stat().st_mtime_ns)
    with zipfile.ZipFile(wheel) as archive:
        entries = [n for n in archive.namelist() if Path(n).name.startswith("_native") and n.endswith((".pyd", ".so"))]
        if len(entries) != 1:
            raise RuntimeError("Native wheel must contain exactly one extension")
        destination = ROOT.parents[1] / "photo_edit" / ("_native" + sysconfig.get_config_var("EXT_SUFFIX"))
        data = archive.read(entries[0])
        if not destination.exists() or destination.read_bytes()!=data:
            temp = destination.with_name(destination.name + ".tmp")
            temp.write_bytes(data)
            temp.replace(destination)
    notices = destination.with_name("_native.notices.txt")
    write_changed(notices,dependency_notices(workspace))
    destination.with_name("_native.build.json").write_text(json.dumps({
        "fingerprint": build_fingerprint(), "binary": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "notices": hashlib.sha256(notices.read_bytes()).hexdigest(),
        "release": not debug,
    }),encoding="utf-8")
    print(f"Built {destination}")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-lock", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    build(update_lock=args.update_lock, prepare_only=args.prepare_only, debug=args.debug)
