# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""ffprobe / ffmpeg helpers. Zero external dependencies."""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess

MIME_VIDEO = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".ogv", ".wmv", ".flv")
MIME_AUDIO = (".wav", ".mp3", ".aac", ".flac", ".ogg", ".opus", ".m4a", ".wma", ".aiff", ".aif")
MIME_IMAGE = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".exr", ".dpx", ".svg")
MEDIA_EXT = MIME_VIDEO + MIME_AUDIO + MIME_IMAGE

TC_RE = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


class MediaError(Exception):
    pass


def bin_path(name, *fallbacks):
    p = shutil.which(name)
    for f in fallbacks:
        if not p and os.path.exists(f):
            p = f
    if not p:
        raise MediaError(f"required executable not found: {name}")
    return p


def run(cmd, timeout=600, cwd=None):
    """Run a command, return (rc, stdout, stderr) as text.

    stdin is /dev/null (never the MCP pipe) and the child gets its own process
    group so a timeout kills the whole tool chain.
    """
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, cwd=cwd, start_new_session=True)
    except OSError as exc:
        return 127, "", f"could not run {cmd[0]}: {exc}"
    try:
        out_b, err_b = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except OSError:
            p.kill()
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return 124, "", f"timeout after {timeout}s: {' '.join(map(str, cmd))}"
    return p.returncode, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")


def parse_time(value, fps=25.0):
    """Parse a flexible time value into seconds.

    Accepts numbers (seconds), '12.5s', '150f' (frames), 'HH:MM:SS.mmm'.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    m = TC_RE.match(s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    if s.endswith("f"):
        return float(s[:-1]) / fps
    if s.endswith("s"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        raise MediaError(f"cannot parse time value {value!r}")


def tc(seconds, fps=25.0):
    """Seconds -> MLT timecode HH:MM:SS.mmm (frame snapped)."""
    frames = max(0, int(round(seconds * fps)))
    total_ms = int(round(frames * 1000.0 / fps))
    h, rem = divmod(total_ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def frames(seconds, fps=25.0):
    return max(0, int(round(seconds * fps)))


def probe(path):
    """ffprobe one file into a compact dict."""
    ffprobe = bin_path("ffprobe", "/usr/bin/ffprobe")
    if not os.path.exists(path):
        raise MediaError(f"file not found: {path}")
    rc, out, err = run([
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ])
    if rc != 0:
        raise MediaError(f"ffprobe failed for {path}: {err.strip()[:400]}")
    data = json.loads(out or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    aud = [s for s in streams if s.get("codec_type") == "audio"]
    info = {
        "path": os.path.abspath(path),
        "duration": float(fmt.get("duration") or 0.0),
        "size": int(fmt.get("size") or 0),
        "format": fmt.get("format_name", ""),
        "has_video": vs is not None,
        "has_audio": bool(aud),
        "audio_streams": len(aud),
        "audio_codec": aud[0].get("codec_name") if aud else None,
        "sample_rate": int(aud[0].get("sample_rate", 0)) if aud else 0,
        "channels": int(aud[0].get("channels", 0)) if aud else 0,
        "video_codec": None,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "interlaced": False,
        "is_image": False,
    }
    if vs:
        info["video_codec"] = vs.get("codec_name")
        info["width"] = int(vs.get("width") or 0)
        info["height"] = int(vs.get("height") or 0)
        rate = vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "0/1"
        try:
            num, den = rate.split("/")
            fps = float(num) / float(den) if float(den) else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        info["fps"] = round(fps, 4)
        # field_order alone lies (libx264 tags progressive streams "tt"), so ask a
        # decoded frame whether it is really interlaced.
        info["interlaced"] = False
        if vs.get("field_order") in ("tt", "bb", "tb", "bt"):
            rc2, out2, _e2 = run([ffprobe, "-v", "error", "-select_streams", "v:0",
                                  "-read_intervals", "%+#1", "-show_entries",
                                  "frame=interlaced_frame", "-of", "csv=p=0", path], timeout=60)
            flags = [int(tok) for tok in re.findall(r"\d+", out2)] if rc2 == 0 else []
            info["interlaced"] = 1 in flags
        info["is_image"] = vs.get("codec_name") in ("png", "mjpeg", "bmp", "webp", "tiff", "gif") and info["duration"] <= 0.05
        if not info["duration"] and info["is_image"]:
            info["duration"] = 5.0
    if not info["has_video"] and info["has_audio"]:
        info["is_image"] = False
    return info


def file_hash(path):
    """Stable short hash Kdenlive-style (md5 of path + size + mtime)."""
    st = os.stat(path)
    h = hashlib.md5()
    h.update(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()


def scan(directory, recursive=False):
    directory = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(directory):
        raise MediaError(f"not a directory: {directory}")
    found = []
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for f in files:
                if f.lower().endswith(MEDIA_EXT):
                    found.append(os.path.join(root, f))
    else:
        for f in sorted(os.listdir(directory)):
            p = os.path.join(directory, f)
            if os.path.isfile(p) and f.lower().endswith(MEDIA_EXT):
                found.append(p)
    out = []
    for p in sorted(found):
        try:
            info = probe(p)
        except MediaError as exc:
            info = {"path": os.path.abspath(p), "error": str(exc)}
        out.append(info)
    return out


def extract_frames(source, times, out_dir, width=640, quality=4):
    """Grab frames at given seconds. Returns list of image paths."""
    ffmpeg = bin_path("ffmpeg", "/usr/bin/ffmpeg")
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for t in times:
        t = max(0.0, float(t))
        name = f"frame_{t:09.3f}.jpg"
        out = os.path.join(out_dir, name)
        cmd = [ffmpeg, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", source,
               "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-q:v", str(quality), out]
        rc, _o, err = run(cmd, timeout=120)
        if rc != 0 or not os.path.exists(out):
            raise MediaError(f"ffmpeg frame extract failed at {t}s: {err.strip()[:300]}")
        made.append(out)
    return made


def loudness(path):
    """Peak / mean volume via ffmpeg volumedetect."""
    ffmpeg = bin_path("ffmpeg", "/usr/bin/ffmpeg")
    rc, _o, err = run([ffmpeg, "-hide_banner", "-i", path, "-af", "volumedetect",
                       "-f", "null", "-"], timeout=600)
    stats = {}
    for key in ("mean_volume", "max_volume"):
        m = re.search(rf"{key}: (-?[\d.]+) dB", err)
        if m:
            stats[key] = float(m.group(1))
    return stats


def normalize_color(value):
    """'#rrggbb', 'rrggbb', 'red', or '0xRRGGBBAA' -> MLT color string."""
    s = str(value).strip()
    if re.match(r"^0x[0-9a-fA-F]{8}$", s):
        return s
    if s.startswith("#"):
        s = s[1:]
    if re.match(r"^[0-9a-fA-F]{6}$", s):
        return f"0x{s.lower()}ff"
    if re.match(r"^[a-zA-Z]+$", s):
        named = {
            "black": "0x000000ff", "white": "0xffffffff", "red": "0xff0000ff",
            "green": "0x00ff00ff", "blue": "0x0000ffff", "gray": "0x808080ff",
            "grey": "0x808080ff", "transparent": "0x00000000",
        }
        if s.lower() in named:
            return named[s.lower()]
    raise MediaError(f"invalid color: {value!r}")
