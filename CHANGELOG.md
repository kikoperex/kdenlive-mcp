# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- CI: full e2e on Debian sid/MLT 7.42, Python 3.9/3.13 compatibility job,
  bounded melt calls; documented MLT 7.22 hang.

## [1.0.1] - 2026-11-22

### Added

- `render` accepts `project_out` to choose where the compiled `.kdenlive` file
  is written, and `dry_run` to compile/validate without encoding.
- `preview.times` accepts the same time formats as the other tools.
- `SECURITY.md` with the private reporting channel, supported versions and
  threat model, plus a weekly Dependabot configuration for GitHub Actions.
- Release workflow that checks the package metadata and publishes the sdist and
  wheel as GitHub Release assets.
- CI packaging job that builds the sdist/wheel, verifies them with
  `twine check --strict`, installs the wheel into a venv and smoke-tests the
  `kdenlive-mcp` command.

### Changed

- The PyPI distribution is named `kdenlive-mlt-mcp` because `kdenlive-mcp` is
  already owned by another project. The installed command is still
  `kdenlive-mcp` and the import package is still `kdenlive_mcp`.
- CI installs `libmltqt7` and runs headless with `QT_QPA_PLATFORM=offscreen`,
  adds Python 3.13 to the matrix, cancels superseded runs and runs the e2e suite
  under a 20-minute step timeout and a 900-second `timeout --signal=KILL`.
- The e2e suite now reports 120 checks and usually finishes in under a minute.
- Corrected `LICENSE` to the byte-exact GPLv2 text.

### Fixed

- Transport crash hardening: nested JSON values, non-UTF8 stdin and malformed
  requests no longer desync or kill the stdio server.
- Overwrite guards for `project_create` and `import_kdenlive` so an existing
  project is not clobbered by accident.
- Strict EDL validation for overlapping clips, duplicate ids, negative starts,
  oversize fades and other invalid edits.
- Timeline timecodes are snapped to whole frames consistently.
- Atomic writes for EDL, rendered output and generated XML, so interrupted jobs
  do not leave truncated files.
- Stable effect indexes when adding and removing effects, plus stricter effect
  property validation.
- Typed time schemas so clients see the accepted formats, and hardened XML
  import for malformed or hostile `.kdenlive` files.
- Image responses are capped by a byte budget so previews cannot blow up the
  MCP response.

## [1.0.0] - 2026-11-22

Initial public release.

### Added

- Installable packaging: the server lives in the `kdenlive_mcp` package with a
  `pyproject.toml` and a `kdenlive-mcp` console script (`pipx install .`), plus a
  `python3 -m kdenlive_mcp` entry point. `python3 server.py` remains a thin
  zero-install launcher.
- MCP server (stdio JSON-RPC) with 20 tools for creating projects, probing and
  scanning media, adding/removing tracks, placing and editing clips, applying
  MLT effects, previewing, validating, rendering, and importing/exporting
  Kdenlive projects.
- EDL JSON as the editable document, compiled to a `.kdenlive` project so the
  result can still be opened and polished in the Kdenlive GUI.
- Rendering through `melt` (the same MLT engine Kdenlive uses) to H.264/AAC,
  with control over CRF, preset, output size and time range.
- `preview` renders a low-resolution version and returns extracted frames as
  images so an agent can see the result.
- Best-effort import of an existing `.kdenlive` project back into EDL form,
  preserving clip position, source, fades and audio gain where possible.
- `validate` checks that generated XML is well formed and completes a full
  render to the null consumer; `output_check` reports stream information and
  audio peak level to catch silent renders.
- End-to-end test suite that generates media with ffmpeg, drives the server over
  stdio and verifies rendered pixels (fades to black, colour clips, titles and
  audio mixing).
- Zero third-party Python dependencies; supports Python 3.9+.
