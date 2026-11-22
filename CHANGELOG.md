# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Installable packaging: the server now lives in the `kdenlive_mcp` package with a
  `pyproject.toml` and a `kdenlive-mcp` console script (`pipx install .`), plus a
  `python3 -m kdenlive_mcp` entry point. `python3 server.py` remains a thin
  zero-install launcher.

## [1.0.0] - 2026-11-20

Initial public release.

### Added

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
