# Bundled macOS ExifTool

- Version: **13.50** (the entry script and the entire `lib/` tree match).
- Official repository: <https://github.com/exiftool/exiftool>.
- Official release: <https://github.com/exiftool/exiftool/releases/tag/13.50>.
- Pinned upstream commit: `e9609a9bcc0d32bd252a709a562fb822d6dd86f7`.
- Upstream history: <https://exiftool.org/history.html>.
- Copyright 2003–2026 Phil Harvey. See the unmodified upstream `LICENSE` and
  `README.upstream` (upstream `README`) for the Perl Artistic License / GPL terms.

`exiftool`, `lib/`, `LICENSE`, and `README.upstream` are unmodified upstream bytes
from this commit. `SHA256SUMS` records every imported file and its SHA-256 digest;
the offline regression test verifies the complete inventory and contents.
Upstream source was retrieved with Git from the official repository, checking out
the `13.50` tag and verifying its full commit ID above.

The former 13.49 entry script lacked its Perl modules. This complete 13.50 runtime
also includes the upstream macOS security update (CVE-2026-3102); mixing a 13.49
entry script with 13.50 modules is not supported. The Windows runtime is separate.

Keep the directory together when packaging or copying it. The script uses
`#!/usr/bin/env perl` and loads `lib/` relative to itself. macOS supplies
`/usr/bin/perl`; Homebrew ExifTool or an external Perl module installation is not
required. Validate the bundled script directly in an environment without user
Perl settings or Homebrew in `PATH`, then exercise actual metadata read/write.
