# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |
| < 1.0 | No |

Security fixes are made on the latest 1.0.x release. Please upgrade before
reporting an issue that may already be fixed.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it privately through GitHub Security Advisories:

<https://github.com/kikoperex/kdenlive-mcp/security/advisories/new>

Please include the affected version, your Python/MLT/ffmpeg versions, a minimal
reproduction (tool call, EDL snippet or command), and the impact you expect.
You will get an acknowledgement as soon as possible, and we will coordinate a
fix and disclosure date with you. There is no bug bounty.

## Scope and threat model

`kdenlive-mcp` is a local stdio MCP server. It is meant to be launched by a
trusted MCP client on the same machine, not exposed to a network.

- The server runs `melt`, `ffmpeg`/`ffprobe` (and, only for
  `open_in_kdenlive`, the Kdenlive GUI) on paths supplied by the client. Tool
  arguments are intentionally local filesystem paths, so a caller that can
  invoke the tools can read/write files the server user can access.
- Rendering and preview write derived files next to the EDL path. Treat the
  EDL, media paths and output paths as trusted input from the client.
- `open_in_kdenlive` launches a GUI process detached from the server. It needs a
  graphical session and the `kdenlive` binary; it does not sandbox the project
  or its media.
- The repository contains no secrets, tokens or credentials. Do not commit
  API keys, private media or generated projects.

Vulnerabilities in `ffmpeg`, MLT/`melt` or Kdenlive itself should be reported to
those projects. Report here if `kdenlive-mcp` passes unsafe input to them or
misuses their output.
