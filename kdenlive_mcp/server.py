# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""Kdenlive / MLT editing MCP server (stdio, newline-delimited JSON-RPC).

No third-party dependencies: only Python 3.9+ and the system tools
ffmpeg/ffprobe/melt/kdenlive.

    {"mcpServers": {"kdenlive": {"command": "python3",
                                 "args": ["/home/USER/mcp/kdenlive-mcp/server.py"]}}}
"""

import json
import sys
import traceback

from . import __version__
from . import tools

MAX_LINE = 16 * 1024 * 1024
SERVER_NAME = "kdenlive-mcp"
SERVER_VERSION = __version__
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """Kdenlive/MLT editing server. The document you edit is an EDL (JSON); \
the .kdenlive project is compiled from it, so Kdenlive can still open the result.

Workflow: media_scan/media_probe -> project_create -> clip_add (kind media|color|title) \
-> preview (renders low-res and returns frames as images so you can see the result) \
-> fix -> render (final H.264/AAC) or open_in_kdenlive (polish by hand).

Times accept plain seconds or '12.5s', '150f', '00:00:12.500'. Audio gain is a linear \
multiplier (0.5 = -6 dB, 2.0 = +6 dB). Video tracks composite bottom to top: V2 covers V1. \
Audio on a video track is muted by default, so music/voice normally goes on an audio track."""


def log(message):
    sys.stderr.write(f"[{SERVER_NAME}] {message}\n")
    sys.stderr.flush()


def _tool_list():
    listed = []
    for t in tools.TOOLS:
        entry = {"name": t["name"], "description": t["description"],
                 "inputSchema": t["inputSchema"]}
        if "annotations" in t:
            entry["annotations"] = t["annotations"]
        listed.append(entry)
    return listed


HANDLERS = {t["name"]: t["handler"] for t in tools.TOOLS}


def handle_call(params):
    name = params.get("name")
    args = params.get("arguments") or {}
    handler = HANDLERS.get(name)
    if handler is None:
        raise LookupError(f"unknown tool: {name}")
    try:
        content = handler(args)
    except tools.ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:  # noqa: BLE001 - report, never kill the server
        log("tool crash:\n" + traceback.format_exc())
        return {"content": [{"type": "text", "text": f"{name} failed: {exc}"}], "isError": True}
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    elif isinstance(content, dict) and "type" in content:
        content = [content]
    return {"content": content}


def dispatch(method, params):
    if method == "initialize":
        wanted = (params or {}).get("protocolVersion")
        version = wanted if wanted in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tool_list()}
    if method == "tools/call":
        return handle_call(params or {})
    if method == "resources/list":
        return {"resources": []}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "prompts/list":
        return {"prompts": []}
    if method in ("notifications/initialized", "notifications/cancelled", "logging/setLevel"):
        return None
    raise NotImplementedError(method)


def _read_line():
    """Read one full request line; report and drain anything over MAX_LINE."""
    parts = []
    size = 0
    too_long = False
    while True:
        chunk = sys.stdin.readline(65536)
        if not chunk:
            break
        newline = chunk.endswith("\n")
        if not too_long:
            parts.append(chunk)
            size += len(chunk)
            content_size = size - 1 if newline else size
            if content_size > MAX_LINE:
                too_long = True
                parts.clear()
        if newline:
            break
    if too_long:
        return "", True
    return "".join(parts), False


def _configure_stdio():
    """Force UTF-8 on the pipes so accented paths/titles never crash a request."""
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main():
    _configure_stdio()
    _debug = os.environ.get("KD_FAULTHANDLER")
    if _debug:
        try:
            import faulthandler
            faulthandler.dump_traceback_later(int(_debug), repeat=False)
        except (ImportError, ValueError):
            pass
    if "--version" in sys.argv:
        print(f"{SERVER_NAME} {SERVER_VERSION}")
        return 0
    if "--list-tools" in sys.argv:
        for t in tools.TOOLS:
            print(t["name"])
        return 0
    log(f"ready {SERVER_VERSION} ({len(tools.TOOLS)} tools)")

    try:
        while True:
            line, too_long = _read_line()
            if too_long:
                log(f"request exceeds {MAX_LINE} bytes; discarded")
                _write({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700,
                                  "message": f"parse error: request exceeds {MAX_LINE} bytes"}})
                continue
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, RecursionError) as exc:
                log(f"bad json: {exc}")
                _write({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": f"parse error: {exc}"}})
                continue
            if isinstance(msg, list):
                if not msg:
                    _write(_invalid_request())
                    continue
                responses = [r for r in (_handle_one(m) for m in msg) if r is not None]
                if responses:
                    _write(responses)
                continue
            response = _handle_one(msg)
            if response is not None:
                _write(response)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    return 0


def _write(payload):
    """Serialize a response, surviving odd filenames and closed pipes."""
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, default=str) + "\n")
        sys.stdout.flush()
    except (OSError, UnicodeEncodeError) as exc:
        log(f"cannot write response: {exc}")


def _invalid_request():
    return {"jsonrpc": "2.0", "id": None,
            "error": {"code": -32600, "message": "invalid request"}}


def _handle_one(msg):
    if not isinstance(msg, dict):
        return _invalid_request()
    method = msg.get("method")
    msg_id = msg.get("id")
    if not isinstance(method, str):
        return _invalid_request()
    try:
        result = dispatch(method, msg.get("params"))
    except NotImplementedError:
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    except LookupError as exc:
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32602, "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001
        log("dispatch crash:\n" + traceback.format_exc())
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32603, "message": str(exc)}}
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


if __name__ == "__main__":
    sys.exit(main())
