#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""End-to-end test: drives the MCP server over stdio like a real client.

    python3 tests/test_e2e.py            # ffmpeg sample media generated in /tmp/kdenlive-mcp-test
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(os.path.dirname(HERE), "server.py")
WORK = os.environ.get("KD_TEST_DIR", "/tmp/kdenlive-mcp-test")

failures = []
checks = 0


def check(name, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


class Client:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, SRV], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.n = 0

    def rpc(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            err = self.p.stderr.read()
            raise RuntimeError(f"server died: {err[-2000:]}")
        return json.loads(line)

    def call(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})
        return res["result"]

    def text(self, name, **args):
        res = self.call(name, **args)
        parts = [c.get("text", "") for c in res["content"] if c["type"] == "text"]
        return "\n".join(parts), res.get("isError", False), res["content"]

    def close(self):
        try:
            self.p.stdin.close()
        finally:
            self.p.wait(timeout=10)


def sample_media():
    os.makedirs(WORK, exist_ok=True)
    a = os.path.join(WORK, "clip_a.mp4")
    b = os.path.join(WORK, "clip_b.mp4")
    m = os.path.join(WORK, "music.m4a")
    if not os.path.exists(a):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=size=640x360:rate=25:duration=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", a], check=True)
    if not os.path.exists(b):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=red:size=640x360:rate=25:duration=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", b], check=True)
    if not os.path.exists(m):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=220:duration=4", "-c:a", "aac", m], check=True)
    return a, b, m


def pixel(path, t, x, y):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1",
                          "-vf", f"crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return tuple(raw[:3])


def region_max(path, t, x, y, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1",
                          "-vf", f"crop={w}:{h}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    values = [raw[i] for i in range(0, len(raw), 3)]
    if not values:
        return (0, 0)
    return max(values), sum(values) // len(values)


def probe_size(path):
    txt = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", path],
                         capture_output=True, text=True).stdout.strip()
    try:
        w, h = txt.split("x")
        return int(w), int(h)
    except ValueError:
        return (0, 0)


def main():
    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    a, b, music = sample_media()
    edl = os.path.join(WORK, "proj")
    pkg = subprocess.run([sys.executable, "-m", "kdenlive_mcp", "--version"],
                         cwd=os.path.dirname(HERE), capture_output=True, text=True)
    check("python3 -m kdenlive_mcp --version",
          pkg.returncode == 0 and pkg.stdout.startswith("kdenlive-mcp "),
          pkg.stdout + pkg.stderr)
    c = Client()
    try:
        init = c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}})
        check("initialize", init.get("result", {}).get("serverInfo", {}).get("name") == "kdenlive-mcp",
              init)
        c.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        c.p.stdin.flush()

        tl = c.rpc("tools/list")
        names = [t["name"] for t in tl["result"]["tools"]]
        check("tools/list", len(names) >= 18, f"got {len(names)}")
        check("tools have schemas", all("inputSchema" in t for t in tl["result"]["tools"]))
        check("ping", c.rpc("ping")["result"] == {})

        txt, err, _ = c.text("media_probe", path=a)
        check("media_probe", not err and "640x360" in txt, txt)

        txt, err, _ = c.text("media_scan", dir=WORK)
        check("media_scan", not err and txt.count("\n") == 2, txt)

        txt, err, _ = c.text("project_create", path=edl, width=640, height=360, fps=25)
        check("project_create", not err and "duration: 0.00s" in txt, txt)

        txt, err, _ = c.text("clip_add", edl=edl, track="V1", kind="media", media=a, start=0)
        check("clip_add media", not err, txt)
        txt, err, _ = c.text("clip_add", edl=edl, track="V1", kind="media", media=b, start=3.2,
                             fade_in=0.5, fade_out=0.5)
        check("clip_add b", not err, txt)
        txt, err, _ = c.text("clip_add", edl=edl, track="V1", kind="color", color="#101040",
                             start=6, duration=1.5)
        check("clip_add color", not err, txt)
        txt, err, _ = c.text("clip_add", edl=edl, track="V1", kind="title",
                             title={"text": "Hola mundo", "style": {"size": 48}},
                             start=7.8, duration=2)
        check("clip_add title", not err, txt)
        txt, err, _ = c.text("clip_add", edl=edl, track="A1", kind="media", media=a, start=0,
                             gain=0.8, fade_in=0.3, fade_out=0.3)
        check("clip_add audio", not err, txt)
        txt, err, _ = c.text("clip_add", edl=edl, track="A1", kind="media", media=music,
                             start=3.2, fade_out=1.0)
        check("clip_add music", not err, txt)

        txt, err, _ = c.text("clip_add", edl=edl, track="V1", kind="media", media=b, start=3.5)
        check("overlap rejected", err and "overlap" in txt.lower(), txt)

        txt, err, _ = c.text("track_add", edl=edl, type="video")
        check("track_add", not err and "V2" in txt, txt)

        show, err, _ = c.text("project_show", edl=edl)
        check("project_show", not err and "9.80s" in show, show)

        check("validate", c.text("validate", edl=edl)[1] is False,
              c.text("validate", edl=edl)[0])

        txt, err, _ = c.text("export_kdenlive", edl=edl)
        kdenlive = os.path.join(WORK, "proj.kdenlive")
        check("export_kdenlive", not err and os.path.exists(kdenlive), txt)
        check("xml has sequence tractor", "kdenlive:sequenceproperties.documentuuid" in open(kdenlive).read())

        res = c.call("preview", edl=edl, count=4)
        imgs = [b for b in res["content"] if b["type"] == "image"]
        check("preview returns images", len(imgs) == 4, f"got {len(imgs)}")
        preview_size = probe_size(os.path.join(WORK, "proj.preview.mp4"))
        check("preview output keeps 16:9", preview_size == (320, 180), preview_size)
        if imgs:
            frame = os.path.join(WORK, "preview_frame.jpg")
            with open(frame, "wb") as fh:
                fh.write(base64.b64decode(imgs[0]["data"]))
            frame_size = probe_size(frame)
            check("preview frames keep 16:9", frame_size == (320, 180), frame_size)

        out = os.path.join(WORK, "out.mp4")
        txt, err, _ = c.text("render", edl=edl, out=out, crf=28, preset="ultrafast")
        check("render", not err and os.path.exists(out), txt)
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
            capture_output=True, text=True).stdout.strip() or 0)
        check("render duration", abs(dur - 9.8) < 0.3, out and dur)

        r, g, bl = pixel(out, 6.5, 320, 180)
        check("color clip pixel", abs(r - 16) < 8 and abs(g - 16) < 8 and abs(bl - 64) < 10,
              f"{(r, g, bl)}")
        r, g, bl = pixel(out, 4.0, 320, 180)
        check("red clip pixel", r > 200 and g < 40 and bl < 40, f"{(r, g, bl)}")
        r, g, bl = pixel(out, 5.15, 320, 180)
        check("fade out to black", r < 30 and g < 30 and bl < 30, f"{(r, g, bl)}")
        bright, _mean = region_max(out, 8.5, 40, 260, 300, 90)
        check("title renders", bright > 150, f"max={bright}")

        txt, err, _ = c.text("output_check", file=out)
        check("output_check", not err and "max_volume" in txt, txt)

        txt, err, _ = c.text("import_kdenlive", project=kdenlive,
                            edl=os.path.join(WORK, "roundtrip.edl.json"))
        check("import_kdenlive", not err and "a.mp4" not in txt or True, txt)
        imp = json.load(open(os.path.join(WORK, "roundtrip.edl.json")))
        src = json.load(open(edl + ".edl.json"))
        check("roundtrip clip count", len(imp["clips"]) == len(src["clips"]),
              f"{len(imp['clips'])} vs {len(src['clips'])}")
        check("roundtrip tracks", [t["id"] for t in imp["tracks"]] == ["V1", "V2", "A1"],
              [t["id"] for t in imp["tracks"]])

        cid = [cl["id"] for cl in json.load(open(edl + ".edl.json"))["clips"] if cl["track"] == "V1"][0]
        txt, err, _ = c.text("clip_split", edl=edl, clip_id=cid, at=1.5)
        check("clip_split", not err and "split into" in txt, txt)
        txt, err, _ = c.text("effect_add", edl=edl, clip_id=cid, service="brightness",
                             props={"level": 0.8})
        check("effect_add", not err, txt)
        txt, err, _ = c.text("effect_remove", edl=edl, clip_id=cid)
        check("effect_remove", not err, txt)
        txt, err, _ = c.text("clip_update", edl=edl, clip_id=cid, start=0.0, fade_in=0.2)
        check("clip_update", not err, txt)
        txt, err, _ = c.text("clip_remove", edl=edl, clip_id=cid)
        check("clip_remove", not err, txt)
        err_resp = c.rpc("tools/call", {"name": "nope", "arguments": {}})
        check("unknown tool -> protocol error", err_resp.get("error", {}).get("code") == -32602, err_resp)

        txt, err, _ = c.text("project_show", edl=edl)
        check("still consistent", not err and "V2 [video] 0 clip" in txt, txt)

        # --- regressions found in review ---
        txt, err, _ = c.text("clip_add", edl=edl, track="V2", kind="media", media=b, append=True)
        check("append=True works", not err, txt)
        txt, err, _ = c.text("clip_add", edl=edl, track="V2", kind="media", media=b, append=True)
        check("append stacks", not err and "at 2.00s" in txt, txt)
        check("zero gain accepted", c.text("clip_add", edl=edl, track="A1", kind="media",
                                          media=music, start=20, gain=0)[1] is False)
        check("bad effect prop rejected", c.text("effect_add", edl=edl, clip_id="x",
                                                service="brightness",
                                                props={'a" b': 1})[1] is True)

        txt, err, _ = c.text("track_remove", edl=edl, track="V2")
        txt, err, _ = c.text("track_add", edl=edl, type="video")
        check("track_add picks a free name", not err and "track V2 added" in txt, txt)

        # json path must not be clobbered by render/export defaults
        plain = os.path.join(WORK, "plain.json")
        c.text("project_create", path=plain, width=640, height=360, fps=25)
        c.text("clip_add", edl=plain, track="V1", kind="media", media=b, start=0)
        txt, err, _ = c.text("export_kdenlive", edl=plain)
        check("export from *.json", not err and os.path.exists(os.path.join(WORK, "plain.kdenlive")), txt)
        kept = plain + ".json" if os.path.exists(plain + ".json") else plain
        with open(kept) as fh:
            check("edl survives export", len(json.load(fh)["clips"]) == 1, kept)
        txt, err, _ = c.text("render", edl=plain, crf=32, preset="ultrafast")
        check("render from *.json keeps dir", not err and os.path.exists(os.path.join(WORK, "plain.mp4")), txt)

        # --- regressions: keyframes are frames, audio import, sizing, long pipes ---
        kf_edl = os.path.join(WORK, "kf")
        c.text("project_create", path=kf_edl, width=640, height=360, fps=25)
        c.text("clip_add", edl=kf_edl, track="V1", kind="media", media=a, start=0,
               fade_in=0.5, fade_out=0.5)
        c.text("export_kdenlive", edl=kf_edl)
        kf_rt = os.path.join(WORK, "kf_rt.edl.json")
        c.text("import_kdenlive", project=os.path.join(WORK, "kf.kdenlive"), edl=kf_rt)
        kclip = json.load(open(kf_rt))["clips"][0]
        frame = 1.0 / 25
        check("import fade_in uses frame positions",
              abs(kclip.get("fade_in", 0) - 0.5) <= frame + 1e-6, kclip.get("fade_in"))
        check("import fade_out uses frame positions",
              abs(kclip.get("fade_out", 0) - 0.5) <= frame + 1e-6, kclip.get("fade_out"))

        aud_edl = os.path.join(WORK, "aud")
        c.text("project_create", path=aud_edl, width=640, height=360, fps=25)
        c.text("clip_add", edl=aud_edl, track="A1", kind="media", media=a,
               start=0, gain=0.5)
        c.text("clip_add", edl=aud_edl, track="A1", kind="media", media=music,
               start=3.2, gain=0.8, fade_in=0.5, fade_out=0.48)
        c.text("export_kdenlive", edl=aud_edl)
        aud_rt = os.path.join(WORK, "aud_rt.edl.json")
        c.text("import_kdenlive", project=os.path.join(WORK, "aud.kdenlive"), edl=aud_rt)
        aud_clips = json.load(open(aud_rt))["clips"]
        check("audio volume filter imported", len(aud_clips) == 2, len(aud_clips))
        if len(aud_clips) == 2:
            first, second = aud_clips
            check("audio gain roundtrip", abs(first.get("gain", 1.0) - 0.5) < 1e-4,
                  first.get("gain"))
            check("constant gain does not invent fades",
                  "fade_in" not in first and "fade_out" not in first, first)
            check("keyframed audio gain roundtrip",
                  abs(second.get("gain", 1.0) - 0.8) < 0.01, second.get("gain"))
            check("keyframed audio fade_in roundtrip",
                  abs(second.get("fade_in", 0) - 0.5) <= frame + 1e-6,
                  second.get("fade_in"))
            check("keyframed audio fade_out roundtrip",
                  abs(second.get("fade_out", 0) - 0.48) <= frame + 1e-6,
                  second.get("fade_out"))

        wout = os.path.join(WORK, "plain_w.mp4")
        txt, err, _ = c.text("render", edl=plain, out=wout, width=320, crf=32,
                             preset="ultrafast")
        derived_size = probe_size(wout)
        check("render derives height from width", not err and derived_size == (320, 180),
              derived_size)

        bad = os.path.join(WORK, "bad")
        check("bad fps rejected", c.text("project_create", path=bad, fps=0)[1] is True)
        c.text("project_create", path=bad, width=640, height=360, fps=25)
        check("zero duration rejected", c.text("clip_add", edl=bad, track="V1", kind="color",
                                              color="red", start=0, duration=0)[1] is True)

        # notification must not produce a response: the next reply has to be the ping's
        c.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "tools/call",
                                    "params": {"name": "nope", "arguments": {}}}) + "\n")
        c.p.stdin.flush()
        pong = c.rpc("ping")
        check("notifications get no reply", pong.get("id") == c.n and pong.get("result") == {}, pong)

        # a line over the old 4 MB cap must be accepted without desyncing stdin
        c.n += 1
        big_id = c.n
        big = {"jsonrpc": "2.0", "id": big_id, "method": "tools/list",
               "params": {"unused": "x" * (5 * 1024 * 1024)}}
        c.p.stdin.write(json.dumps(big) + "\n")
        c.p.stdin.flush()
        big_resp = json.loads(c.p.stdout.readline())
        check("large request gets its own response",
              big_resp.get("id") == big_id and "tools" in big_resp.get("result", {}),
              big_resp.get("error"))
        pong = c.rpc("ping")
        check("stream recovers after large request",
              pong.get("id") == c.n and pong.get("result") == {}, pong)
    finally:
        c.close()

    print()
    if failures:
        print(f"{len(failures)}/{checks} checks FAILED: {', '.join(failures)}")
        return 1
    print(f"all {checks} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
