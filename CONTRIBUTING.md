# Contributing to kdenlive-mcp

Thanks for taking the time to improve the project. This is a small,
zero-dependency MCP server, so setup is short and the review bar is mostly about
keeping it portable and predictable.

## Requirements

- Python 3.9 or newer (the code must run on 3.9, even if you develop on a newer
  interpreter).
- `ffmpeg` and `ffprobe` on `PATH`.
- `melt`, the MLT command-line renderer used by the tests and by the server.
  The package is called `melt` on Debian/Ubuntu, `mlt` on Arch and Fedora. The
  full e2e suite runs against MLT 7.42 (Debian sid); Ubuntu 24.04's MLT 7.22 can
  hang on some projects.

```bash
# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg melt

# Arch
sudo pacman -S ffmpeg mlt

# Fedora (enable RPM Fusion for ffmpeg if needed)
sudo dnf install ffmpeg mlt
```

Optional: `kdenlive` itself, only if you want to open exported projects in the
GUI. Rendering never needs the GUI.

No Python packages are required. Do not add a `requirements.txt` or a runtime
dependency unless there is a very strong reason; stdlib only is a project goal.

## Setup and smoke test

```bash
git clone <your-fork>
cd kdenlive-mcp
python3 server.py --version      # kdenlive-mcp 1.0.1
python3 server.py --list-tools   # the 20 tool names
```

The server speaks newline-delimited JSON-RPC over stdio, so it can also be
driven by hand or with `echo '{"jsonrpc":"2.0",...}' | python3 server.py`.

## Tests

```bash
python3 tests/test_e2e.py
```

The suite has 121 checks and usually finishes in under a minute. It generates
sample media with ffmpeg, talks to the server over stdio like a real MCP client,
renders with `melt`, and checks pixels (fades, colour clips, titles, audio mix).
It prints every check and exits non-zero on failure. CI splits validation in
two: a fast compatibility job runs `py_compile` and the server smoke tests on
Python 3.9, 3.12 and 3.13 without `melt`, and the full suite runs once in a
Debian sid container with MLT 7.42 under Xvfb. The e2e job sets
`KD_MELT_TIMEOUT=90` to bound every `melt` call (the harness default is 120) and
has a 5-minute hard timeout (a 300-second outer `timeout`), so a local run that
takes much longer than a minute or two is a regression, not just slow hardware.
It also asserts the `all N checks passed` summary, warns if the check count
drops below 120, and warns if MLT drifts from 7.42. Ubuntu 24.04 ships MLT
7.22, whose `melt` can hang on some projects, so it is not used for the render
e2e.

By default it works in `/tmp/kdenlive-mcp-test`. Set `KD_TEST_DIR` to use
another directory and avoid collisions with parallel runs:

```bash
KD_TEST_DIR="$(mktemp -d)/kdenlive-mcp-test" python3 tests/test_e2e.py
```

The e2e job installs `ffmpeg` and `melt` from apt in the Debian sid container
(MLT 7.42; the Qt module ships with the MLT package). Please run the suite
locally before opening a pull request; a targeted manual render is not a
substitute for the suite.

On a headless machine (no X/Wayland session), run the suite under Xvfb. MLT's `kdenlivetitle` and `qtblend` need a Qt platform
even though nothing is displayed:

```bash
xvfb-run -a python3 tests/test_e2e.py
```

### Debugging CI hangs

- `KD_FAULTHANDLER=30` dumps the server's stack traces to stderr after 30
  seconds. The hook is opt-in and one-shot: it fires once and does not keep
  printing, so leave it unset for normal runs.
- `KD_MELT_TIMEOUT` defaults to 120 seconds. The test harness injects it into
  every `validate`, `render` and `preview` call, so a hung `melt` fails the
  check instead of blocking the job. CI sets it to 90 so two hung calls still
  fit inside the 300-second outer timeout.
- MLT 7.22 (Ubuntu 24.04) can hang around 96% on some projects. That is why
  the e2e job runs Debian sid with MLT 7.42; check `melt --version` before
  blaming the test.

## Coding guidelines

- **Stdlib only.** No third-party imports in the server or tests.
- **Python 3.9 compatible.** Avoid `match`, `X | Y` type syntax,
  `list[str]` at runtime, `tomllib`, and other 3.10+ features.
- **Keep tool schemas documented.** Every MCP tool lives in `kdenlive_mcp/tools.py` with a
  name, description and `inputSchema`. If you change behaviour, update the
  description and schema so agents can discover it.
- **Errors are `ToolError`.** Raise `tools.ToolError` for expected failures
  (bad input, missing file, unsupported value). The dispatcher turns it into an
  MCP `isError` response instead of crashing the server. Unexpected exceptions
  are caught and reported too, but do not rely on that.
- **No `shell=True`.** Call `ffmpeg`, `ffprobe` and `melt` with argument lists
  and pass user paths as separate arguments. Quote nothing by hand.
- **Timeline semantics matter.** The EDL JSON is the source of truth and the
  `.kdenlive` file is compiled from it; video tracks composite bottom to top,
  and audio on video tracks is muted. Keep `kdenlive_mcp/edl.py`/`kdenlive_mcp/xmlgen.py` changes
  consistent with the documented format notes in `README.md`.
- **Keep fixes narrow.** Avoid unrelated reformatting or refactors in a bug-fix
  PR; it makes review and bisecting harder.
- **Generated artifacts are gitignored.** Do not commit `*.edl.json`,
  `*.kdenlive`, renders (`*.mp4`, `*.mov`, `*.mkv`, `*.webm`, audio), preview
  frames or temp files. Use a scratch directory such as `/tmp` for experiments.

There is intentionally no lint/format job in CI; match the existing style
(readable, commented where the MLT/Kdenlive XML is non-obvious).

## Issues and pull requests

- Open an issue for a reproducible bug or a proposed feature. Include your OS,
  Python version, `ffmpeg -version`, `melt --version`, the tool call or EDL that
  triggers the problem, and the full error text.
- Keep each pull request focused. Describe what changed and why, and mention
  the tests you ran.
- Add or update an e2e check when the change affects timeline behaviour,
  generated XML or rendering. Pixel checks are welcome but keep them fast and
  deterministic.
- Small documentation fixes are welcome as-is.
- CI must pass on all supported Python versions before merge. Maintainers may ask for a
  change if it breaks 3.9 compatibility or introduces a dependency.

## Licensing

This project is licensed under **GPL-2.0-or-later**. By submitting a
contribution you agree to license it under the same terms (inbound = outbound).
No CLA is required.

Please keep the existing SPDX header on source files and add one to new files:

```python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) <year> <your name>
```
