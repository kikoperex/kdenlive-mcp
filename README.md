# kdenlive-mcp

English | [Español](README.es.md)

[![CI](https://github.com/kikoperex/kdenlive-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/kikoperex/kdenlive-mcp/actions/workflows/ci.yml)
[![License: GPL-2.0-or-later](https://img.shields.io/badge/license-GPL--2.0--or--later-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

An MCP server for editing video on Linux with Kdenlive/MLT: the edit lives in a JSON
EDL, preview frames come back as images, and rendering goes through `melt`.

## Why

Video-editing integrations usually either drive a GUI or ask the model to edit MLT XML
directly. Both are fragile: a GUI needs a display and its state can change underneath
you, while XML has many coupled properties and no useful error messages.

This server keeps the editable document and the build output separate:

- **The EDL JSON is the source of truth.** `project.edl.json` is small, diffable and
  written atomically. Every editing tool modifies this file, never the XML.
- **The `.kdenlive` file is a build artifact.** `export_kdenlive` (and `render` /
  `preview` / `validate` internally) compile the EDL into a real Kdenlive project. You
  can throw it away and regenerate it after any edit.
- **`melt` renders instead of the GUI.** It is the same MLT engine Kdenlive uses, but
  headless, deterministic and scriptable. No X session is needed to edit, preview or
  render.
- **`preview` returns frames as images.** A low-resolution render plus JPEG frames in
  the MCP response lets the agent actually look at the result and fix it, instead of
  guessing from metadata.

A human can still open the compiled `.kdenlive` in the Kdenlive GUI, polish it by hand,
and bring it back into the EDL with `import_kdenlive` (best effort, see
[Limitations](#limitations)).

```text
media_scan --> project_create --> clip_add / clip_update / effect_add --> preview --> frames
                                        |                                  |          |
                                        |                                  |          +--> look, fix, repeat
                                        |                                  +--> low-res .mp4
                                        v
                               project.edl.json  (source of truth)
                                        |
             +--------------------------+--------------------------+
             |                          |                          |
             v                          v                          v
     export_kdenlive               validate                    render
             |                          |                          |
             v                          v                          v
     project.kdenlive         XML + null render            final .mp4
             |
             v
     Kdenlive GUI (optional)
```

## Features

- 20 MCP tools covering projects, media, tracks, clips, effects, preview, render,
  validation, export/import and output checks.
- Media, solid-color and title clips, with fades, linear audio gain and raw MLT filters.
- `preview` renders a small H.264 file and returns up to 24 frames inline as JPEG images.
- `render` produces H.264/AAC with configurable CRF, preset, output size and time range.
  A missing output dimension is derived from the project aspect ratio.
- `validate` checks XML well-formedness, reports missing media and runs a full `melt`
  render to the null consumer.
- `.kdenlive` export and import; `open_in_kdenlive` can launch the GUI when one is
  installed.
- `output_check` reports streams, duration and audio peak level, catching silent renders.
- Zero third-party Python dependencies: standard library only (Python 3.9+).
- Clear validation errors for overlapping clips, duplicate tracks, unknown ids, bad
  profiles and malformed times.
- No GUI or X session required for editing, previewing, validating or rendering.

## Requirements

- **Python 3.9+**. The server uses only the standard library.
- **`ffmpeg` and `ffprobe`** on `PATH`: media probing, frame extraction and loudness
  measurement.
- **`melt`** (from MLT) on `PATH`: compiling is done in Python, but rendering and
  validation run `melt`. This project is developed against MLT 7.42. The full e2e
  suite runs on Debian sid with MLT 7.42 under Xvfb. Ubuntu 24.04 ships MLT 7.22,
  whose `melt` can hang on some projects, so it is not used for the render e2e; a fast
  Python 3.9/3.13 compatibility job runs on Ubuntu instead.
- **A Qt platform for headless runs.** MLT's `kdenlivetitle` and `qtblend` need one even
  when nothing is displayed; use Xvfb (or `QT_QPA_PLATFORM=offscreen`) when
  there is no X/Wayland session. CI runs the full suite under Xvfb.
- **`kdenlive`** (optional): only needed for `open_in_kdenlive` and for opening exported
  projects in the GUI. Tested with Kdenlive 26.08.3.
- **Linux.** The process handling, path assumptions and GUI launching target POSIX
  systems; macOS and Windows are not tested.

## Installation

### From a clone (zero install)

The server has no Python dependencies, so cloning is enough:

```bash
git clone https://github.com/kikoperex/kdenlive-mcp.git
cd kdenlive-mcp
python3 server.py --version       # kdenlive-mcp 1.0.1
python3 server.py --list-tools    # one tool name per line, 20 in total
```

For the examples below the checkout is assumed to be at
`$HOME/mcp/kdenlive-mcp`; adjust the path if yours differs. `server.py` is a thin
launcher for the `kdenlive_mcp` package; the equivalent package entry point is
`python3 -m kdenlive_mcp`, which runs from the checkout root.

### With pipx or uvx

The PyPI distribution is named **`kdenlive-mlt-mcp`** because the `kdenlive-mcp` name is
already owned by another project. Installing that distribution still gives you the
**`kdenlive-mcp` command** and the `kdenlive_mcp` package, so client configurations do
not change. The project ships a `pyproject.toml` with that console script:

```bash
# from a checkout
pipx install .

# from PyPI, once the kdenlive-mlt-mcp distribution is published
pipx install kdenlive-mlt-mcp

# or directly from Git, without a manual clone
uvx --from git+https://github.com/kikoperex/kdenlive-mcp kdenlive-mcp
```

After installing, run `kdenlive-mcp --version` or point the MCP client at the
installed command. Both forms also accept `--list-tools`.

### Register with pi

```bash
pi mcp add kdenlive \
  --description "Kdenlive/MLT video editing: build timelines, render, preview frames" \
  --exposure codemode \
  -- python3 $HOME/mcp/kdenlive-mcp/server.py
```

Renders are slow, and the default per-request timeout of 60 seconds is too short for
real footage. Raise it in `~/.pi/agent/mcp.json`:

```bash
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home() / ".pi/agent/mcp.json"
c = json.loads(p.read_text())
c["mcpServers"]["kdenlive"]["timeout"] = 3600
p.write_text(json.dumps(c, indent=2) + "\n")
EOF

pi mcp list      # kdenlive should be connected and list 20 tools
```

Pi can expose the tools in three modes:

| Exposure | How the model reaches the tools | When to use it |
|---|---|---|
| `codemode` (default) | From codemode scripts; tools are discoverable with `searchTools()` / `describeTool()` | Chaining many edits in one script |
| `deferred` | Found through `tool_search` before use | Keep the tool list small but call tools directly |
| `direct` | Declared to the model like built-in tools on every turn | Frequent, simple use; costs 20 tool declarations |

Set it with `--exposure deferred` (or `direct`) when adding the server, or `"exposure"`
in the server entry in `mcp.json`.

### Generic MCP client

Any stdio MCP client can launch the server directly:

```json
{
  "mcpServers": {
    "kdenlive": {
      "command": "python3",
      "args": ["/home/USER/mcp/kdenlive-mcp/server.py"]
    }
  }
}
```

Replace `/home/USER` with the absolute path on your machine (JSON does not expand
`$HOME`). Timeout keys and exposure modes are client-specific; on pi they are `timeout`
and `exposure` in seconds and mode names as above.

Rendering has two timeouts: the MCP client's per-request timeout (set it high enough for
the whole call) and the tool's own subprocess timeout. `render` defaults to 7200 seconds
and accepts `timeout`; `preview` defaults to 1800 seconds. For long renders, raise both.

Tool calls are handled synchronously on the server's stdio loop. A long render blocks
`ping`, cancellation and any other tool call until it finishes, so clients should raise
their per-request timeout rather than expect to interrupt it.

## Tools

| Tool | Purpose | Main arguments |
|---|---|---|
| `project_create` | Create a new EDL and return its timeline. Defaults to 1920x1080 at 25 fps. | `path`, `width`, `height`, `fps`, `profile` (e.g. `1080p25`, `720p30`, `vertical1080`, `4k30`), `name` |
| `project_show` | Print tracks, clips, in/out points, fades, gain, effects and total duration. | `edl` |
| `project_set_profile` | Change frame size, frame rate or progressive flag. | `edl`, `width`, `height`, `fps`, `progressive` |
| `media_probe` | Inspect one or more media files: duration, resolution, fps, codecs, audio streams, interlacing. | `path` or `paths` |
| `media_scan` | List usable media in a directory with metadata. | `dir`, `recursive` |
| `track_add` | Add a video or audio track; the name defaults to the next free `Vn` / `An`. | `edl`, `type`, `name` |
| `track_remove` | Remove a track and everything on it. | `edl`, `track` |
| `clip_add` | Place a media, color or title clip. Media uses `in`/`out` in source seconds; color needs `duration`; title takes `{text, style}` and a duration (5 s if omitted). `append` places it after the last clip. | `edl`, `track`, `kind`, `media` / `color` / `title`, `start`, `in`, `out`, `duration`, `gain`, `fade_in`, `fade_out`, `label`, `append` |
| `clip_update` | Move, retime, trim, re-gain, fade or relabel a clip; can also move it to another track. Accepts the same timing fields as `clip_add`. | `edl`, `clip_id`, `track`, `start`, `duration`, `in`, `out`, `gain`, `fade_in`, `fade_out`, `label`, `title` |
| `clip_remove` | Delete a clip from the timeline. | `edl`, `clip_id` |
| `clip_split` | Split a clip at an absolute timeline position; both halves must be at least one frame. | `edl`, `clip_id`, `at` |
| `effect_add` | Append a raw MLT filter to a clip, for example `brightness` with `{level: 0.8}` (1.0 is neutral) or `frei0r.cartoon`. Props are passed through unchanged. | `edl`, `clip_id`, `service`, `props` |
| `effect_remove` | Remove all effects from a clip, or only one service. | `edl`, `clip_id`, `service` |
| `preview` | Fast low-resolution render plus up to 24 frames returned as images. | `edl`, `times` (same time formats as below) or `count` (default 4), `width` (default 320), `inline`, `out`, `project_out` |
| `render` | Render the timeline to H.264/AAC. CRF defaults to 20, preset to `medium`. `dry_run` compiles and returns the melt command without encoding. | `edl`, `out`, `crf`, `preset`, `width` or `height` (the other is derived), `start`, `end`, `timeout`, `project_out`, `dry_run` |
| `validate` | Compile the project, check XML parsing and missing media, then run a full `melt` render to the null consumer. | `edl`, `out` (`.kdenlive` path) |
| `export_kdenlive` | Compile the EDL into a `.kdenlive` project for the GUI. | `edl`, `out` |
| `open_in_kdenlive` | Compile the project and launch it in the Kdenlive GUI. Requires `kdenlive` and a graphical session. | `edl`, `out` |
| `import_kdenlive` | Convert an existing `.kdenlive` project back into an EDL (best effort). | `project`, `edl` |
| `output_check` | Inspect a rendered file: streams, duration and audio peak; warns when the output looks silent. | `file` |

Relative to the EDL path, derived files are siblings:

- `edit` becomes `edit.edl.json`; `edit.json` is also accepted as an EDL path.
- `edit.edl.json` compiles to `edit.kdenlive`.
- `render` writes `edit.mp4` unless `out` is given.
- `preview` writes `edit.preview.mp4` unless `out` is given.
- `render` and `preview` accept `project_out` to choose the compiled `.kdenlive` path;
  `render` also accepts `dry_run` to compile and return the melt command without encoding.

## Time formats and semantics

Times may be numbers in seconds or strings in any of these forms:

```text
3.5                 # number, seconds
"3.5s"              # seconds
"87f"               # 87 frames at the project frame rate
"00:00:03.500"      # HH:MM:SS.mmm
```

The EDL stores seconds as floats. Frame values are converted using the project's `fps`,
and generated MLT timecodes are snapped to whole frames. `preview.times` accepts the same
formats, as do `render`'s `start` and `end`.

- `start` is the position on the timeline; `in` and `out` are positions in the source
  media. For a media clip, `duration` is the length on the timeline; when `out` is used,
  `duration = out - in`. If only `in` is given, the clip runs to the end of the source.
- `fade_in` and `fade_out` are seconds. Video fades go through black; audio fades change
  the volume.
- `gain` is a **linear multiplier**: `1.0` is unity, `0.5` is approximately -6 dB, `2.0`
  is approximately +6 dB, and `0` is silent. The generated Kdenlive volume filter stores
  the converted value in dB.
- Clips on the same track are linear and may not overlap. Conflicting edits fail with the
  occupied time range; use `clip_update` to move/trim, or `append` / `append: true` to
  place a clip after the last one.
- Video tracks composite from bottom to top: **V2 covers V1**. Audio tracks mix
  independently.
- Audio on a video track is muted by default (the generated track has `hide="audio"`),
  so music and voice belong on audio tracks. Put audio-only files on an `A` track.

## Notes on the `.kdenlive` format

The generator is written against the Kdenlive 26.08 project format and MLT 7.42. These
properties are load-bearing:

- The document UUID lives in `main_bin` as `kdenlive:docproperties.uuid` and must equal
  the sequence tractor's `kdenlive:sequenceproperties.documentuuid`; otherwise Kdenlive
  treats the sequence as foreign.
- Video tracks are numbered V1..Vn in tractor order, but audio numbering is reversed:
  the last audio track in the tractor is A1. Tracks are therefore emitted V1..Vn, then
  An..A1.
- Video compositing uses `qtblend` and audio mixing uses `mix`, both `always_active` and
  marked with `internal_added` so Kdenlive does not show them as user-added transitions.
- `black_track` is pinned to the content duration. MLT derives a tractor's length from
  its longest track, so this keeps the sequence from running longer than the edit.
- Title clips need an **empty** `resource` and inline `xmldata`; that makes the
  `kdenlivetitle` module read the inline title instead of trying to load a file.
- `brightness.level` is a 0..1 multiplier, while `volume.level` is in **dB** (0 dB is
  unity). Fades are written using those scales.
- The root `<mlt producer="...">` points at the sequence tractor so that
  `melt project.kdenlive` renders the timeline. Kdenlive itself does not read that
  attribute; it looks up `main_bin`.

## Testing

```bash
python3 tests/test_e2e.py
```

The end-to-end test requires `ffmpeg`, `ffprobe` and `melt`. It generates 640x360 sample
media in `/tmp/kdenlive-mcp-test` (override with `KD_TEST_DIR`), starts `server.py`, and
speaks newline-delimited JSON-RPC over stdio like a real client.

The suite has 121 checks and usually finishes in under a minute. It verifies tool schemas, media probing/scanning, editing and
overlap rejection, profile/track changes, `.kdenlive` export and import (including fades
and audio gain), preview image counts and aspect ratio, render duration and sizing, and
actual pixels (color clip, red clip, fade to black, visible title), plus audio peak,
large requests and error handling. CI runs the full suite in a Debian sid container with
MLT 7.42 under Xvfb, with a 5-minute hard timeout (a 300-second outer `timeout`) because
the runner is headless. The e2e job also asserts the `all N checks passed` summary, warns
if the count drops below 120, and warns if MLT drifts from 7.42. A separate fast
compatibility job compiles and smoke-tests the server on Python 3.9, 3.12 and 3.13
without `melt` or the e2e suite.

## Troubleshooting

- **`melt` stops around 96% and never exits (MLT 7.22/Ubuntu 24.04).** Pass a smaller
  `timeout=` to the tool or use MLT ≥ 7.42.
- **`melt` exits with signal 11 (segfault).** This is usually the output size, not the
  timeline. Use even dimensions, preferably multiples of 16: `320x180`, `640x360`,
  `1280x720`. `render` and `preview` snap requested sizes to even pixel counts, but very
  small or unusual sizes can still upset libx264; try another size.
- **`kdenlive --render` segfaults.** On Kdenlive 26.08.3 (Arch), headless rendering
  crashes even on an empty project. This server never uses it: rendering and validation
  go through `melt`.
- **The MCP call times out during a render.** Raise the client's per-request timeout
  (`"timeout": 3600` or more in `mcp.json`) and, for very long jobs, pass a larger
  `timeout` to `render`. The tool's default is 7200 seconds; `preview` defaults to 1800.
  Tool calls are synchronous, so a render also blocks `ping` and cancellation until it
  returns.
- **`required executable not found: melt`, `ffmpeg` or `ffprobe`.** Install MLT and
  FFmpeg and make sure their binaries are on `PATH` (the server also checks `/usr/bin`
  and `/sbin` for `melt`).
- **The render has no audio.** Check it with `output_check`; a peak below -60 dB is
  reported as silent. Audio placed on a video track is muted by design, so move it to an
  audio track.
- **Preview frames are missing.** `preview` caps output at 24 frames and omits individual
  JPEGs larger than about 900 KB. Retry with a smaller `width`.
- **`open_in_kdenlive` fails.** It needs `kdenlive` installed and a graphical session;
  editing, previewing and rendering do not.

## Limitations

- `import_kdenlive` is best effort. It recovers track layout, clip positions and sources,
  color clips, title text, simple brightness fades, and constant audio gain plus simple
  volume fades. It does **not** recover transitions, per-clip effect stacks, arbitrary
  keyframe curves, groups or markers. Clips whose media file no longer exists are
  skipped.
- There is no file locking. Two agents or processes editing the same EDL can overwrite
  each other's changes (the last atomic save wins). Use one EDL per editor or serialize
  edits.
- The EDL model is intentionally small: one sequence, cuts and simple fades, plus raw
  per-clip filters. There are no transitions, keyframed effects, markers, groups, proxy
  workflows or multiple sequences.
- Linux-only assumptions: POSIX process groups, `shutil.which`, GUI launching and
  filesystem paths. No Windows or macOS support is claimed.
- Rendering is fixed to H.264/AAC with libx264; there is no GPU encoding.
- The generated `.kdenlive` targets the Kdenlive 26.08 format. Other versions may load
  it with warnings or reject newer properties.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report issues and submit changes. Run
`python3 tests/test_e2e.py` before opening a pull request.

## Security

Report vulnerabilities privately through GitHub Security Advisories as described in
[SECURITY.md](SECURITY.md), not in a public issue. The server runs `melt`, `ffmpeg` and
(only for `open_in_kdenlive`) the Kdenlive GUI on client-supplied paths, so treat it as a
local tool driven by a trusted client.

## License

kdenlive-mcp is free software licensed under the **GNU General Public License, version 2
or (at your option) any later version** (`GPL-2.0-or-later`). See [LICENSE](LICENSE) for
the full text. It is distributed without any warranty, to the extent permitted by law.

`ffmpeg`, `ffprobe`, `melt`/MLT and Kdenlive are separate programs invoked as
subprocesses. They are not bundled with this repository and are distributed under their
own licenses and terms.
