# Third-party licenses and notices

Engineering OS itself is licensed under the Apache License, Version 2.0
(see `LICENSE`). This file lists the third-party components that are bundled
with, linked into, or required by the distributed product.

The list is intentionally conservative: if a component is shipped in an
installer, portable archive, or macOS application, it is named here even when
its license only requires a link to the upstream project.

## Rust CLI (`cli/`)

The CLI is built with Cargo. The following crates are resolved in
`cli/Cargo.lock`. Most are dual-licensed `MIT OR Apache-2.0`; the table records
the exact expression reported by `cargo metadata`.

| Crate | Version | License |
| --- | --- | --- |
| block-buffer | 0.10.4 | MIT OR Apache-2.0 |
| bumpalo | 3.20.3 | MIT OR Apache-2.0 |
| cfg-if | 1.0.5 | MIT OR Apache-2.0 |
| cpufeatures | 0.2.17 | MIT OR Apache-2.0 |
| crypto-common | 0.1.7 | MIT OR Apache-2.0 |
| digest | 0.10.7 | MIT OR Apache-2.0 |
| futures-core | 0.3.34 | MIT OR Apache-2.0 |
| futures-task | 0.3.34 | MIT OR Apache-2.0 |
| futures-util | 0.3.34 | MIT OR Apache-2.0 |
| generic-array | 0.14.7 | MIT |
| getrandom | 0.4.3 | MIT OR Apache-2.0 |
| itoa | 1.0.18 | MIT OR Apache-2.0 |
| js-sys | 0.3.106 | MIT OR Apache-2.0 |
| libc | 0.2.191 | MIT OR Apache-2.0 |
| memchr | 2.8.3 | Unlicense OR MIT |
| once_cell | 1.21.4 | MIT OR Apache-2.0 |
| pin-project-lite | 0.2.17 | Apache-2.0 OR MIT |
| proc-macro2 | 1.0.107 | MIT OR Apache-2.0 |
| quote | 1.0.47 | MIT OR Apache-2.0 |
| r-efi | 6.0.0 | MIT OR Apache-2.0 OR LGPL-2.1-or-later |
| rustversion | 1.0.23 | MIT OR Apache-2.0 |
| serde | 1.0.229 | MIT OR Apache-2.0 |
| serde_core | 1.0.229 | MIT OR Apache-2.0 |
| serde_derive | 1.0.229 | MIT OR Apache-2.0 |
| serde_json | 1.0.154 | MIT OR Apache-2.0 |
| sha2 | 0.10.9 | MIT OR Apache-2.0 |
| slab | 0.4.12 | MIT |
| syn | 3.0.9 | MIT OR Apache-2.0 |
| typenum | 1.20.1 | MIT OR Apache-2.0 |
| unicode-ident | 1.0.27 | (MIT OR Apache-2.0) AND Unicode-3.0 |
| uuid | 1.28.0 | Apache-2.0 OR MIT |
| version_check | 0.9.5 | MIT/Apache-2.0 |
| wasm-bindgen | 0.2.129 | MIT OR Apache-2.0 |
| wasm-bindgen-macro | 0.2.129 | MIT OR Apache-2.0 |
| wasm-bindgen-macro-support | 0.2.129 | MIT OR Apache-2.0 |
| wasm-bindgen-shared | 0.2.129 | MIT OR Apache-2.0 |
| zmij | 1.0.23 | MIT |

The full license text of each crate is available from its package on
<https://crates.io/>; the `MIT` and `Apache-2.0` texts below cover the
expressions used by the linked crates.

## Bundled Python runtime (Windows and macOS packages)

Release packages may include a relocatable CPython distribution. The runtime is
staged at build time and is not part of the source tree.

- **CPython 3.11.x** — Python Software Foundation License 2.0.
  <https://docs.python.org/3/license.html>
- **python-build-standalone** — Mozilla Public License 2.0; the build system
  that produces the relocatable interpreter.
  <https://github.com/astral-sh/python-build-standalone>
- The runtime also contains the dependencies selected by
  python-build-standalone (for example OpenSSL, libffi, zlib, bzip2, xz,
  SQLite, and ncurses). Their license texts are shipped inside
  `runtime/python/` as `LICENSE.txt` and the accompanying license files.

## Windows installer

- **NSIS 3** — zlib/libpng license and the NSIS license.
  <https://nsis.sourceforge.io/License>

The NSIS stub is linked into `EngineeringOS-Setup-*.exe`. The installer script
itself (`installer/EngineeringOS.nsi`) is part of Engineering OS.

## Optional external tools

EOS can detect and integrate with tools that are not distributed with EOS.
They remain under their own licenses and are never bundled by this project:

- **OpenCode** — host/runtime integration; see the OpenCode project license.
- **Claude Code, Codex, Gemini CLI, Cursor** — host integrations only.
- **Graphify** — optional repository-graph adapter; see the Graphify project
  license.
- **Ruflo** — optional orchestrator adapter; see the Ruflo project license.

## Documentation and methodology content

The EOS methodology skills (`ai-inventor`, `fable-5-1`, `fable5-thinking`,
`graph-engineering`) are original Engineering OS material, licensed under
Apache-2.0. They do not contain text copied from a model vendor's system
prompt, a university course, or another project's source. Public ideas and
research papers cited in those skills remain the work of their authors and are
cited, not relicensed.

## License texts

### MIT License

Copyright (c) the respective crate authors.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

### Unicode License v3

UNICODE, INC. LICENSE AGREEMENT - DATA FILES AND SOFTWARE

See <https://www.unicode.org/license.txt> for the full text. The license grants
permission to use, copy, modify, and distribute Unicode data files and software,
provided that the copyright and permission notices are retained.

### LGPL-2.1-or-later (r-efi, UEFI targets only)

The `r-efi` crate is dual/tri-licensed and is only selected for UEFI targets.
It is not linked into the Windows or macOS release binaries. If you build for a
UEFI target, read the crate's license and the GNU LGPL 2.1 text at
<https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html>.

### Apache License 2.0

The full text is in `LICENSE` at the repository root.
