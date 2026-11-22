#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""End-to-end test: drives the MCP server over stdio like a real client.

    python3 tests/test_e2e.py            # ffmpeg sample media generated in /tmp/kdenlive-mcp-test
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(os.path.dirname(HERE), "server.py")
_raw_work = os.environ.get("KD_TEST_DIR", "/tmp/kdenlive-mcp-test")
WORK = os.path.abspath(os.path.expanduser(_raw_work)) if _raw_work.strip() else ""

failures = []
checks = 0


def work_dir_is_safe(path):
    """Only ever delete a scratch directory we can prove is disposable."""
    if not path:
        return False
    path = os.path.abspath(path)
    if path in (os.path.sep, "/"):
        return False
    if os.path.normcase(path) == os.path.normcase(os.path.abspath(os.path.expanduser("~"))):
        return False
    tmp = os.path.abspath(tempfile.gettempdir())
    if os.path.normcase(path) == os.path.normcase(tmp):
        return False
    if "kdenlive-mcp-test" in os.path.basename(os.path.normpath(path)):
        return True
    try:
        return os.path.commonpath([tmp, path]) == tmp
    except ValueError:
        return False


def check(name, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


class Client:
    def __init__(self, env=None):
        # stderr goes to a real temp file: a full pipe can no longer deadlock the
        # server while it is writing tracebacks, but the text is still available
        # when something actually goes wrong.
        self.err_path = os.path.join(WORK, "server-stderr.log")
        self.err = open(self.err_path, "a+", encoding="utf-8")
        self.p = subprocess.Popen([sys.executable, SRV], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.err, text=True,
                                  encoding="utf-8", errors="replace", env=env)
        self.n = 0

    def _stderr(self):
        try:
            self.err.flush()
            self.err.seek(0)
            text = self.err.read()
            return text.decode("utf-8", "replace") if isinstance(text, bytes) else text
        except (OSError, ValueError):
            return ""

    def send_raw(self, payload):
        if not isinstance(payload, str):
            payload = json.dumps(payload)
        self.p.stdin.write(payload + "\n")
        self.p.stdin.flush()

    def read_response(self):
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError(f"server died: {self._stderr()[-4000:]}")
        return json.loads(line)

    def rpc(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(msg)
        return self.read_response()

    def call(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})
        return res["result"]

    def text(self, name, **args):
        res = self.call(name, **args)
        parts = [c.get("text", "") for c in res["content"] if c["type"] == "text"]
        return "\n".join(parts), res.get("isError", False), res["content"]

    def close(self):
        try:
            try:
                self.p.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.p.kill()
                try:
                    self.p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                self.err.close()
            except OSError:
                pass


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


def edl_json(path):
    path = os.path.abspath(path)
    if path.endswith(".edl.json") or path.endswith(".json"):
        return path
    return path + ".edl.json"


def clip_id_of(text):
    match = re.search(r"clip (c[0-9a-f]{10}) on ", text)
    return match.group(1) if match else ""


def xml_level_keyframes(path):
    """Return [(service, [frame, ...])] for every keyframed level property."""
    levels = []
    for flt in ET.parse(path).getroot().iter("filter"):
        props = {p.get("name"): (p.text or "").strip() for p in flt.findall("property")}
        service = props.get("mlt_service")
        value = props.get("level") or ""
        if service not in ("brightness", "volume") or "=" not in value:
            continue
        frames = []
        for part in value.split(";"):
            if "=" not in part:
                continue
            pos, _val = part.split("=", 1)
            try:
                frames.append(int(round(float(pos))))
            except ValueError:
                pass
        levels.append((service, frames))
    return levels


def replace_playlist_producer(src, dst, track_name, producer):
    """Rewrite the first timeline entry of a track to an unresolved producer id."""
    tree = ET.parse(src)
    for playlist in tree.getroot().findall("playlist"):
        props = {p.get("name"): (p.text or "").strip()
                 for p in playlist.findall("property")}
        if props.get("kdenlive:track_name") != track_name:
            continue
        entries = playlist.findall("entry")
        if not entries:
            return False
        entries[0].set("producer", producer)
        tree.write(dst, encoding="utf-8", xml_declaration=True)
        return True
    return False


def main():
    debug = os.environ.get("KD_FAULTHANDLER")
    if debug:
        try:
            import faulthandler
            faulthandler.dump_traceback_later(int(debug), repeat=False)
        except (ImportError, ValueError):
            pass
    if not work_dir_is_safe(WORK):
        print(f"refusing to delete unsafe workspace: {WORK!r}; set KD_TEST_DIR to an "
              f"absolute scratch path under {tempfile.gettempdir()} (or a basename "
              f"containing 'kdenlive-mcp-test')", file=sys.stderr)
        return 2
    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    a, b, music = sample_media()
    edl = os.path.join(WORK, "proj")
    pkg = subprocess.run([sys.executable, "-m", "kdenlive_mcp", "--version"],
                         cwd=os.path.dirname(HERE), capture_output=True, text=True)
    check("python3 -m kdenlive_mcp --version",
          pkg.returncode == 0 and pkg.stdout.strip() == "kdenlive-mcp 1.0.1",
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
        tool_map = {t["name"]: t for t in tl["result"]["tools"]}
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

        vtxt, verr, _ = c.text("validate", edl=edl)
        check("validate", verr is False, vtxt)

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
        check("render leaves no .part file", not os.path.exists(out + ".part"), out + ".part")

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
        check("import_kdenlive", not err and "imported" in txt, txt)
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

        # --- project_create overwrite guard ---
        ow = os.path.join(WORK, "ow")
        txt, err, _ = c.text("project_create", path=ow, width=640, height=360, fps=25)
        check("overwrite: project created", not err and "EDL created" in txt, txt)
        txt, err, _ = c.text("clip_add", edl=ow, track="V1", kind="color",
                             color="red", start=0, duration=1)
        ow_clip = clip_id_of(txt)
        check("overwrite: clip added", not err and bool(ow_clip), txt)
        txt, err, _ = c.text("project_create", path=ow, width=640, height=360, fps=25)
        check("project_create refuses an existing EDL",
              err and "already exists" in txt, txt)
        show, err, _ = c.text("project_show", edl=ow)
        check("overwrite refusal keeps the existing clip",
              not err and ow_clip in show and "1 clip(s)" in show, show)
        txt, err, _ = c.text("project_create", path=ow, width=640, height=360,
                             fps=25, overwrite=True)
        check("project_create overwrite clears the timeline",
              not err and "duration: 0.00s" in txt and "0 clip(s)" in txt, txt)

        # --- validation of negative values and audio-track clip kinds ---
        neg = os.path.join(WORK, "neg")
        c.text("project_create", path=neg, width=640, height=360, fps=25)
        txt, err, _ = c.text("clip_add", edl=neg, track="V1", kind="color",
                             color="red", start=-1, duration=1)
        check("negative start rejected", err and "start" in txt.lower(), txt)
        txt, err, _ = c.text("clip_add", edl=neg, track="A1", kind="media",
                             media=music, start=0, gain=-0.5)
        check("negative gain rejected", err and "gain" in txt.lower(), txt)
        txt, err, _ = c.text("clip_add", edl=neg, track="A1", kind="color",
                             color="red", start=0, duration=1)
        check("color clip on an audio track rejected",
              err and "audio track" in txt.lower(), txt)
        txt, err, _ = c.text("clip_add", edl=neg, track="A1", kind="title",
                             title={"text": "x"}, start=0, duration=1)
        check("title clip on an audio track rejected",
              err and "audio track" in txt.lower(), txt)

        # --- frame snapping ---
        snap = os.path.join(WORK, "snap")
        c.text("project_create", path=snap, width=640, height=360, fps=25)
        txt, err, _ = c.text("clip_add", edl=snap, track="V1", kind="color",
                             color="#102030", start=0, duration=1.5)
        first = clip_id_of(txt)
        check("frame snap: clip id is c + 10 hex",
              not err and len(first) == 11 and first.startswith("c"), txt)
        snap_doc = json.load(open(edl_json(snap)))
        first_clip = next((cl for cl in snap_doc["clips"] if cl["id"] == first), {})
        check("frame snap: 1.5s becomes 1.52s",
              abs(float(first_clip.get("duration", 0)) - 1.52) < 1e-9,
              first_clip.get("duration"))
        txt, err, _ = c.text("clip_add", edl=snap, track="V1", kind="color",
                             color="#203040", start=2.51, duration=1)
        second = clip_id_of(txt)
        snap_doc = json.load(open(edl_json(snap)))
        second_clip = next((cl for cl in snap_doc["clips"] if cl["id"] == second), {})
        check("frame snap: sub-frame start snaps to 2.52s",
              not err and abs(float(second_clip.get("start", -1)) - 2.52) < 1e-9,
              second_clip.get("start"))

        # --- fades clamp to the clip, keyframes stay sorted and in range ---
        fade = os.path.join(WORK, "fade")
        c.text("project_create", path=fade, width=640, height=360, fps=25)
        txt, err, _ = c.text("clip_add", edl=fade, track="V1", kind="color",
                             color="blue", start=0, duration=1,
                             fade_in=0.8, fade_out=0.8)
        check("fade clip accepted", not err, txt)
        fade_xml = os.path.join(WORK, "fade_project.kdenlive")
        txt, err, _ = c.text("export_kdenlive", edl=fade, out=fade_xml)
        check("fade project exported", not err and os.path.exists(fade_xml), txt)
        levels = xml_level_keyframes(fade_xml)
        check("fade keyframes present", bool(levels), levels)
        check("fade keyframes are sorted",
              all(frames == sorted(frames) for _service, frames in levels), levels)
        total_frames = int(round(1.0 * 25))
        check("fade keyframes inside the clip duration",
              all(not frames or max(frames) < total_frames for _service, frames in levels),
              (total_frames, levels))
        txt, err, _ = c.text("validate", edl=fade,
                             out=os.path.join(WORK, "fade_validated.kdenlive"))
        check("fade project validates", not err and "melt: ok" in txt, txt)

        # --- strict EDL validation (hand-edited JSON) ---
        strict = os.path.join(WORK, "strict")
        c.text("project_create", path=strict, width=640, height=360, fps=25)
        c.text("clip_add", edl=strict, track="V1", kind="color", color="red",
               start=0, duration=1)
        strict_doc = json.load(open(edl_json(strict)))
        overlap_doc = json.loads(json.dumps(strict_doc))
        clone = dict(overlap_doc["clips"][0])
        clone["id"] = "cdeadbeef01"
        clone["start"] = 0.5
        overlap_doc["clips"].append(clone)
        overlap_path = os.path.join(WORK, "strict_overlap.edl.json")
        with open(overlap_path, "w") as fh:
            json.dump(overlap_doc, fh)
        txt, err, _ = c.text("validate", edl=overlap_path, skip_melt=True)
        check("validate rejects overlapping clips",
              err and "overlap" in txt.lower(), txt)
        dup_doc = json.loads(json.dumps(strict_doc))
        dup_doc["clips"].append(dict(dup_doc["clips"][0]))
        dup_path = os.path.join(WORK, "strict_duplicate.edl.json")
        with open(dup_path, "w") as fh:
            json.dump(dup_doc, fh)
        txt, err, _ = c.text("validate", edl=dup_path, skip_melt=True)
        check("validate rejects duplicate clip ids",
              err and "duplicate clip id" in txt.lower(), txt)

        # --- effect identity by index ---
        fx = os.path.join(WORK, "fx")
        c.text("project_create", path=fx, width=640, height=360, fps=25)
        txt, err, _ = c.text("clip_add", edl=fx, track="V1", kind="color",
                             color="green", start=0, duration=1)
        fx_clip = clip_id_of(txt)
        txt, err, _ = c.text("effect_add", edl=fx, clip_id=fx_clip,
                             service="brightness", props={"level": 0.8})
        check("effect_add first brightness", not err and "effect 0" in txt, txt)
        txt, err, _ = c.text("effect_add", edl=fx, clip_id=fx_clip,
                             service="brightness", props={"level": 0.9})
        check("effect_add second brightness", not err and "effect 1" in txt, txt)
        txt, err, _ = c.text("effect_remove", edl=fx, clip_id=fx_clip,
                             effect_index=0)
        check("effect_remove by index",
              not err and "1 effect(s) removed" in txt, txt)
        show, err, _ = c.text("project_show", edl=fx)
        check("effect_remove leaves exactly one brightness",
              not err and show.count("brightness") == 1, show)
        fx_doc = json.load(open(edl_json(fx)))
        fx_clip_doc = next(cl for cl in fx_doc["clips"] if cl["id"] == fx_clip)
        check("effect_remove keeps the second effect",
              len(fx_clip_doc.get("effects", [])) == 1 and
              abs(float(fx_clip_doc["effects"][0]["props"]["level"]) - 0.9) < 1e-9,
              fx_clip_doc.get("effects"))

        # --- preview accepts string time values ---
        res = c.call("preview", edl=snap, times=["0.5s", "25f"], width=320)
        imgs = [b for b in res.get("content", []) if b.get("type") == "image"]
        check("preview accepts string times",
              not res.get("isError") and len(imgs) == 2,
              f"images={len(imgs)} error={res.get('isError')}")

        # --- render dry_run and output safety ---
        dry_out = os.path.join(WORK, "dry.mp4")
        txt, err, _ = c.text("render", edl=snap, out=dry_out, crf=0,
                             preset="ultrafast", dry_run=True)
        check("render dry_run reports crf=0",
              not err and "crf=0" in txt and "command:" in txt, txt)
        check("render dry_run writes no output",
              not os.path.exists(dry_out) and not os.path.exists(dry_out + ".part"),
              dry_out)

        safety = os.path.join(WORK, "safety")
        c.text("project_create", path=safety, width=640, height=360, fps=25)
        c.text("clip_add", edl=safety, track="V1", kind="color", color="red",
               start=0, duration=1)
        safety_file = edl_json(safety)
        for tool in ("export_kdenlive", "validate", "render"):
            args = {"edl": safety, "out": safety_file}
            if tool == "validate":
                args["skip_melt"] = True
            txt, err, _ = c.text(tool, **args)
            check(f"{tool} refuses to overwrite its EDL",
                  err and "same file" in txt.lower(), txt)
        show, err, _ = c.text("project_show", edl=safety)
        check("output safety keeps the EDL intact",
              not err and "1 clip(s)" in show, show)

        # --- schemas and annotations ---
        check("project_show is read-only",
              tool_map["project_show"].get("annotations", {}).get("readOnlyHint") is True)
        check("project_create is destructive",
              tool_map["project_create"].get("annotations", {}).get("destructiveHint") is True)
        check("project_create schema has overwrite",
              "overwrite" in tool_map["project_create"]["inputSchema"]["properties"])
        render_props = tool_map["render"]["inputSchema"]["properties"]
        check("render schema has project_out and dry_run",
              "project_out" in render_props and "dry_run" in render_props)
        check("effect_remove schema has effect_index",
              "effect_index" in tool_map["effect_remove"]["inputSchema"]["properties"])
        times_items = tool_map["preview"]["inputSchema"]["properties"]["times"]["items"]
        time_types = {item.get("type") for item in times_items.get("anyOf", [])}
        check("preview times accept numbers and strings",
              time_types == {"number", "string"}, times_items)
        probe_any = tool_map["media_probe"]["inputSchema"].get("anyOf", [])
        probe_required = {key for branch in probe_any for key in branch.get("required", [])}
        check("media_probe schema requires path or paths",
              {"path", "paths"} <= probe_required, probe_any)
        profile_enum = tool_map["project_create"]["inputSchema"]["properties"]["profile"].get("enum", [])
        check("profile enum has 1080p2997",
              "1080p2997" in profile_enum, profile_enum)

        # --- cheap validation, per-file probe errors, track count, profile ---
        t0 = time.monotonic()
        txt, err, _ = c.text("validate", edl=snap, skip_melt=True)
        elapsed = time.monotonic() - t0
        check("validate skip_melt reports skipped",
              not err and "skipped" in txt.lower(), txt)
        check("validate skip_melt is quick", elapsed < 5.0, f"{elapsed:.2f}s")

        missing = os.path.join(WORK, "missing.mp4")
        txt, err, _ = c.text("media_probe", paths=[a, missing])
        check("media_probe reports per-file errors",
              not err and missing in txt and "error:" in txt, txt)

        trk = os.path.join(WORK, "trk")
        c.text("project_create", path=trk, width=640, height=360, fps=25)
        c.text("track_add", edl=trk, type="video")
        c.text("clip_add", edl=trk, track="V2", kind="media", media=b, start=0)
        txt, err, _ = c.text("track_remove", edl=trk, track="V2")
        check("track_remove reports the clip count",
              not err and "1 clip(s)" in txt, txt)

        txt, err, _ = c.text("project_set_profile", edl=neg, width=641)
        check("project_set_profile rejects odd width",
              err and "even" in txt.lower(), txt)

        # --- import hardening: DTD rejection and dropped missing media ---
        dtd = os.path.join(WORK, "dtd.kdenlive")
        with open(dtd, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?>\n<!DOCTYPE mlt [<!ENTITY x "y">]>\n<mlt/>\n')
        txt, err, _ = c.text("import_kdenlive", project=dtd,
                             edl=os.path.join(WORK, "dtd.edl.json"))
        check("import rejects DTD/entity declarations",
              err and ("dtd" in txt.lower() or "entity" in txt.lower()), txt)

        imp = os.path.join(WORK, "imp")
        c.text("project_create", path=imp, width=640, height=360, fps=25)
        c.text("clip_add", edl=imp, track="V1", kind="media", media=b, start=0)
        imp_xml = os.path.join(WORK, "imp_source.kdenlive")
        txt, err, _ = c.text("export_kdenlive", edl=imp, out=imp_xml)
        check("import hardening source exported",
              not err and os.path.exists(imp_xml), txt)
        missing_media = os.path.join(WORK, "gone.mp4")
        with open(imp_xml, encoding="utf-8") as fh:
            imp_text = fh.read()
        broken = os.path.join(WORK, "imp_missing.kdenlive")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write(imp_text.replace(b, missing_media))
        txt, err, _ = c.text("import_kdenlive", project=broken,
                             edl=os.path.join(WORK, "imp_missing.edl.json"))
        check("import reports dropped missing media",
              not err and "dropped 1 clip(s)" in txt, txt)

        # --- import cursor advances past an unresolved producer ---
        cursor = os.path.join(WORK, "cursor")
        c.text("project_create", path=cursor, width=640, height=360, fps=25)
        c.text("clip_add", edl=cursor, track="V1", kind="media", media=b,
               start=0, duration=1)
        c.text("clip_add", edl=cursor, track="V1", kind="color", color="red",
               start=2.5, duration=1)
        cursor_xml = os.path.join(WORK, "cursor_source.kdenlive")
        txt, err, _ = c.text("export_kdenlive", edl=cursor, out=cursor_xml)
        check("import cursor source exported",
              not err and os.path.exists(cursor_xml), txt)
        broken_xml = os.path.join(WORK, "cursor_broken.kdenlive")
        replaced = replace_playlist_producer(cursor_xml, broken_xml, "V1",
                                             "does-not-exist")
        check("import cursor fixture edited", replaced)
        broken_edl = os.path.join(WORK, "cursor_broken.edl.json")
        txt, err, _ = c.text("import_kdenlive", project=broken_xml, edl=broken_edl)
        cursor_src = json.load(open(edl_json(cursor)))
        cursor_src_v1 = sorted((cl for cl in cursor_src["clips"] if cl["track"] == "V1"),
                               key=lambda cl: cl["start"])
        cursor_imp = json.load(open(broken_edl))
        cursor_imp_v1 = sorted((cl for cl in cursor_imp["clips"] if cl["track"] == "V1"),
                               key=lambda cl: cl["start"])
        src_start = float(cursor_src_v1[1]["start"]) if len(cursor_src_v1) > 1 else -1
        imp_start = float(cursor_imp_v1[0]["start"]) if cursor_imp_v1 else -2
        check("import cursor is not shifted by an unresolved entry",
              not err and abs(imp_start - src_start) < 1e-3,
              f"source={src_start} imported={imp_start}")

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

        # --- transport: invalid batches, deep JSON, ASCII stdio ---
        c.send_raw("[]")
        resp = c.read_response()
        check("empty batch -> -32600 with null id",
              resp.get("error", {}).get("code") == -32600 and resp.get("id") is None,
              resp)
        c.send_raw("{}")
        resp = c.read_response()
        check("empty object -> -32600 with null id",
              resp.get("error", {}).get("code") == -32600 and resp.get("id") is None,
              resp)
        depth = 3000
        c.send_raw("[" * depth + "]" * depth)
        resp = c.read_response()
        if isinstance(resp, list):
            # Newer CPython JSON decoders accept 3000 levels as a valid (but
            # nonsensical) batch.  Go past their C-stack limit so the server
            # still has to report a parse error; older 3.x raises on 3000.
            depth = 1000000
            c.send_raw("[" * depth + "]" * depth)
            resp = c.read_response()
        check("deep JSON -> -32700 with null id",
              isinstance(resp, dict) and
              resp.get("error", {}).get("code") == -32700 and resp.get("id") is None,
              resp if not isinstance(resp, list) else f"batch response: {type(resp).__name__}")
        pong = c.rpc("ping")
        check("stream recovers after deep JSON",
              pong.get("id") == c.n and pong.get("result") == {}, pong)

        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "ascii", "LC_ALL": "C", "PYTHONUTF8": "0"})
        nonascii = Client(env=env)
        try:
            init = nonascii.rpc("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "añejo", "version": "0"}})
            check("ASCII stdio server handles a non-ASCII request",
                  init.get("result", {}).get("serverInfo", {}).get("name") == "kdenlive-mcp",
                  init)
            pong = nonascii.rpc("ping")
            check("ASCII stdio server stays alive",
                  pong.get("result") == {}, pong)
        finally:
            nonascii.close()
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
