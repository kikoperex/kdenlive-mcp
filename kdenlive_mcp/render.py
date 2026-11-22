# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""Rendering helpers: melt (primary) and frame extraction."""

import os
import shutil
import subprocess

from . import media


class RenderError(Exception):
    pass


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# The render goes to `<out>.part`, so ffmpeg cannot infer the container from the
# file name: tell the MLT avformat consumer explicitly.
_CONTAINERS = {
    "mp4": "mp4", "m4v": "mp4", "mov": "mov", "mkv": "matroska",
    "webm": "webm", "avi": "avi", "ts": "mpegts", "mts": "mpegts",
    "mpg": "mpeg", "mpeg": "mpeg", "flv": "flv", "ogv": "ogg",
}


def _container(out):
    ext = os.path.splitext(out)[1].lstrip(".").lower()
    return _CONTAINERS.get(ext, "mp4")


def melt_render(project, out, fps=25.0, crf=20, preset="medium", width=None, height=None,
                start=None, end=None, dry_run=False, extra=None, timeout=7200):
    """Render an MLT/Kdenlive XML with melt (the same engine Kdenlive uses)."""
    melt = media.bin_path("melt", "/usr/bin/melt", "/sbin/melt")
    out = os.path.abspath(os.path.expanduser(out))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    part = out + ".part"
    container = _container(out)
    cmd = [melt, project]
    if start is not None or end is not None:
        cmd += [f"in={media.frames(start or 0.0, fps)}"]
        if end is not None:
            cmd += [f"out={max(0, media.frames(end, fps) - 1)}"]
    consumer = [f"-consumer", f"avformat:{part}",
                "vcodec=libx264", f"crf={crf}", f"preset={preset}",
                "acodec=aac", "ab=192k", "ar=48000", "channels=2",
                "pix_fmt=yuv420p", f"f={container}",
                "real_time=-1", "progress=1", "rescale=bicubic"]
    if container in ("mp4", "mov"):
        consumer.append("movflags=+faststart")
    if width and height:
        if int(width) % 4 or int(height) % 4:
            raise RenderError(
                f"render size {int(width)}x{int(height)} is not a multiple of 4; "
                "libx264/yuv420p cannot encode it")
        consumer += [f"s={int(width)}x{int(height)}"]
    if extra:
        consumer += [str(x) for x in extra]
    cmd += consumer
    if dry_run:
        return {"cmd": cmd, "out": out}
    # never let a previous partial render confuse the size check / os.replace
    _unlink(part)
    rc, _o, err = media.run(cmd, timeout=timeout)
    ok = rc == 0 and os.path.exists(part) and os.path.getsize(part) > 0
    if not ok:
        _unlink(part)
        tail = "\n".join([ln for ln in err.strip().splitlines()
                           if "duplicate" not in ln.lower()
                           and "dlopen" not in ln.lower()][-12:])
        if rc < 0:
            raise RenderError(
                f"melt crashed (signal {-rc}) while rendering. This is usually the output "
                f"size: try another even width/height (multiples of 16 are safest, e.g. 320x180, 640x360).\n{tail}")
        raise RenderError(f"melt render failed (rc={rc}):\n{tail}")
    try:
        os.replace(part, out)
    except OSError as exc:
        _unlink(part)
        raise RenderError(f"could not move the render into place ({out}): {exc}")
    info = media.probe(out)
    return {
        "out": out,
        "duration": info.get("duration"),
        "size": info.get("size"),
        "width": info.get("width"),
        "height": info.get("height"),
        "has_audio": info.get("has_audio"),
        "command": " ".join(cmd),
    }


def melt_check(project, timeout=900):
    """Full parse + render into the null consumer. Catches broken XML/graph."""
    melt = media.bin_path("melt", "/usr/bin/melt", "/sbin/melt")
    rc, _o, err = media.run([melt, project, "-consumer", "null", "progress=0", "real_time=-1"],
                            timeout=timeout)
    noise = ("duplicate", "dlopen", "shared object", "swscaler", "current frame",
             "mlt_repository", "deprecated pixel", "could not update timestamps",
             "dropping frame", "non-monotonous")
    problems = [ln for ln in err.splitlines()
                if ln.strip() and not any(n in ln.lower() for n in noise)]
    ok = rc == 0 and not any(("error" in ln.lower() or "fail" in ln.lower()) for ln in problems)
    return {"ok": ok, "returncode": rc, "problems": problems[-10:]}


_CHILDREN = []


def _reap_children():
    """Drop already-exited children from the registry (poll reaps them)."""
    alive = []
    for child in _CHILDREN:
        try:
            if child.poll() is None:
                alive.append(child)
        except OSError:
            pass
    _CHILDREN[:] = alive


def open_in_kdenlive(project):
    """Launch the Kdenlive GUI on a project (detached).

    Launched children are kept so exited ones can be reaped on the next call
    (no zombies) and the pid is returned for diagnostics.
    """
    kdenlive = shutil.which("kdenlive")
    if not kdenlive:
        raise RenderError("kdenlive is not installed")
    _reap_children()
    child = subprocess.Popen([kdenlive, project], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    _CHILDREN.append(child)
    return {"launched": kdenlive, "project": project, "pid": child.pid}


def preview(project, out, times=None, count=None, width=320, height=None, crf=28,
            preset="ultrafast", fps=25.0, timeout=1800):
    """Fast low-res render used as the agent's visual feedback loop."""
    if height is None:
        height = max(4, int(round(width * 9 / 16 / 4)) * 4)
    info = melt_render(project, out, fps=fps, crf=crf, preset=preset,
                       width=width, height=height, timeout=timeout)
    duration = info.get("duration") or 0.0
    if times is None:
        n = int(count or 4)
        if duration <= 0:
            times = [0.0]
        else:
            times = [duration * (i + 0.5) / n for i in range(n)]
    frames = media.extract_frames(out, times, os.path.join(os.path.dirname(out), ".frames"),
                                  width=width)
    return info, frames
