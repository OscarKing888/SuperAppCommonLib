# PhotoCraft adapter

`upstream/` is an immutable subset of PhotoCraft revision
`369704ac551e8e409800e423ff550c353d5522ef`. Its hashes and original repository are
recorded in `upstream.json`. Preserve the included MIT/Apache licenses and notice.
No PhotoCraft branding, UI, fonts or application assets are used.

The nine original crates implement pixels and adjustments. `bridge/` exposes
owned float32 RGBA arrays through PyO3/numpy, releases the GIL during processing,
checks cancellation between stages, validates inputs and limits allocation.
RAW decoding, XMP, output publication and UI are outside this adapter. The
generated `workspace/` uses a trimmed workspace manifest; imported Rust sources
remain byte-for-byte identical. The adapter's root `Cargo.lock` is authoritative
for this build, independently of the copied upstream lock file.

From a host repository with `app_common` at its root, run its Python interpreter:

```text
python app_common/native/photo_edit/tools/build_native.py
```

Requires rustup toolchain 1.95.0, platform build tools, maturin 1.9.6 and Python
3.11+. Builds use `--locked`; dependency resolution changes require the explicit
`--update-lock` flag. `--prepare-only` generates the workspace without compiling;
`--debug` is development only. The extension and build stamp go into this
checkout's `photo_edit/`; workspace, target, wheel and binaries are ignored.
Never install an entire worktree's `app_common` into the shared environment.

Update workflow:

1. Review a concrete upstream commit in a separate upstream clone.
2. Run `tools/sync_upstream.py <upstream-clone> --revision <full-commit>`.
   The tool refuses to overwrite locally modified snapshot files. Use `--check`
   to verify an unchanged snapshot. It never fetches or changes the source clone.
3. Review the imported diff and dependency manifests. Adjust only `bridge/` and
   the generated-workspace adapter when needed, then explicitly update the lock.
4. Update `photo_edit/model.py`'s algorithm version and `photo_edit/native.py`'s
   expected revision/bridge version. Add an explicit recipe migration if output
   semantics change; unknown versions must remain intact and fail visibly.
5. From the generated workspace run `cargo test -p superbird-photo-edit-native`,
   the relevant upstream `photocraft-algo`, `photocraft-cms` and
   `photocraft-compose` tests. Bridge tests compare all 16 adjustments directly
   with upstream calls, plus identity and Camera RAW settings compatibility.
6. Run host Python model/storage/native/pipeline/export/CLI tests and the viewer
   preview/navigation/UI suites. Compare representative pixels and exported
   16-bit files, then run packaged `--check-photo-edit` on both platforms.
7. Commit the shared repository first; then its host gitlink and integration.

Known upstream test observation on Windows/Rust 1.95: the unchanged
`pixelate::tests::facet_reuse_matches_scalar_in_every_model` test differs only in
NaN sign bits. It is outside the exposed editor operations. Record upstream
results honestly; do not silently patch the vendored implementation to hide it.
