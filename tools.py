# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""MCP tool definitions for the Kdenlive/MLT editing server."""

import base64
import json
import os
import re
import xml.etree.ElementTree as ET

import edl as edlmod
import media
import render
import xmlgen


class ToolError(Exception):
    pass


def _path(value, what="path"):
    if not value:
        raise ToolError(f"{what} is required")
    return os.path.abspath(os.path.expanduser(str(value)))


def _edl(value):
    p = edlmod.path_of(_path(value, "edl"))
    try:
        return edlmod.load(p), p
    except edlmod.EdlError as exc:
        raise ToolError(str(exc))


def _save(doc, p):
    edlmod.save(doc, p)
    return p


def _sibling(p, suffix, override=None):
    return _path(override or edlmod.derived(p, suffix))


MAX_IMAGE_BYTES = 900_000
MAX_FRAMES = 24


def _image_block(path, max_bytes=MAX_IMAGE_BYTES):
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    return {"type": "image", "data": base64.b64encode(data).decode(), "mimeType": "image/jpeg"}


def _result(text, images=()):
    blocks = [{"type": "text", "text": text}]
    skipped = 0
    for p in list(images)[:MAX_FRAMES]:
        b = _image_block(p)
        if b:
            blocks.append(b)
        else:
            skipped += 1
    if skipped:
        blocks.append({"type": "text",
                       "text": f"({skipped} frame(s) omitted: too large or unreadable; "
                               f"retry with a smaller width)"})
    return blocks


# --------------------------------------------------------------------- tools

def project_create(a):
    def num(key, default):
        value = a.get(key)
        return default if value is None else float(value)

    try:
        doc = edlmod.new(_path(a.get("path")), width=int(num("width", 1920)),
                         height=int(num("height", 1080)), fps=num("fps", 25),
                         name=a.get("name"), profile=a.get("profile"))
    except (edlmod.EdlError, ValueError) as exc:
        raise ToolError(str(exc))
    p = edlmod.path_of(a["path"])
    return f"EDL created: {p}\n\n{edlmod.summarize(doc)}"


def project_show(a):
    doc, p = _edl(a.get("edl"))
    return f"{p}\n\n{edlmod.summarize(doc)}"


def project_set_profile(a):
    doc, p = _edl(a.get("edl"))
    prof = doc["profile"]
    for key, cast in (("width", int), ("height", int), ("fps", float)):
        if a.get(key) is not None:
            prof[key] = cast(a[key])
    if a.get("progressive") is not None:
        prof["progressive"] = bool(a["progressive"])
    _save(doc, p)
    f = edlmod.fps(doc)
    return f"profile now {prof['width']}x{prof['height']} @ {f:g} fps ({p})"


def media_probe(a):
    paths = a.get("paths") or ([a["path"]] if a.get("path") else [])
    if not paths:
        raise ToolError("give path or paths")
    out = []
    for p in paths:
        try:
            info = media.probe(_path(p, "path"))
        except media.MediaError as exc:
            raise ToolError(str(exc))
        out.append(_fmt_media(info))
    return "\n".join(out)


def _fmt_media(i):
    dur = i.get("duration") or 0
    bits = [f"{i['path']}", f"  {dur:.2f}s"]
    if i.get("width"):
        bits.append(f"{i['width']}x{i['height']} @ {i.get('fps', 0):g} fps")
    if i.get("video_codec"):
        bits.append(f"v:{i['video_codec']}")
    if i.get("has_audio"):
        bits.append(f"a:{i.get('audio_codec')} {i.get('sample_rate')}Hz {i.get('channels')}ch")
    if not i.get("has_video"):
        bits.append("audio-only")
    if i.get("is_image"):
        bits.append("image")
    if i.get("interlaced"):
        bits.append("INTERLACED")
    return " ".join(bits)


def media_scan(a):
    try:
        items = media.scan(_path(a.get("dir"), "dir"), recursive=bool(a.get("recursive")))
    except media.MediaError as exc:
        raise ToolError(str(exc))
    if not items:
        return "no media found"
    return "\n".join(_fmt_media(i) for i in items)


def track_add(a):
    doc, p = _edl(a.get("edl"))
    try:
        tid = edlmod.add_track(doc, a.get("type", "video"), name=a.get("name"))
    except edlmod.EdlError as exc:
        raise ToolError(str(exc))
    _save(doc, p)
    return f"track {tid} added\n\n{edlmod.summarize(doc)}"


def track_remove(a):
    doc, p = _edl(a.get("edl"))
    tid = a.get("track")
    if any(t["id"] == tid for t in doc["tracks"]):
        doc["tracks"] = [t for t in doc["tracks"] if t["id"] != tid]
        doc["clips"] = [c for c in doc["clips"] if c["track"] != tid]
        _save(doc, p)
        return f"track {tid} removed (with its clips)\n\n{edlmod.summarize(doc)}"
    raise ToolError(f"unknown track {tid}")


def clip_add(a):
    doc, p = _edl(a.get("edl"))
    kind = a.get("kind") or ("media" if a.get("media") else "color" if a.get("color") else "title")
    kwargs = dict(
        kind=kind, media_path=a.get("media"),
        in_=a.get("in"), out=a.get("out"), duration=a.get("duration"),
        color=a.get("color"), title=a.get("title"),
        gain=1.0 if a.get("gain") is None else float(a["gain"]),
        fade_in=a.get("fade_in") or 0.0, fade_out=a.get("fade_out") or 0.0,
        label=a.get("label"),
    )
    try:
        if a.get("append"):
            clip = edlmod.append(doc, a.get("track"), start=a.get("start") or 0.0, **kwargs)
        else:
            clip = edlmod.add_clip(doc, a.get("track"), start=a.get("start") or 0.0, **kwargs)
    except (edlmod.EdlError, media.MediaError) as exc:
        raise ToolError(str(exc))
    _save(doc, p)
    return (f"clip {clip['id']} on {clip['track']} at {clip['start']:.2f}s "
            f"({edlmod.duration_of(doc, clip):.2f}s)\n\n{edlmod.summarize(doc)}")


def clip_update(a):
    doc, p = _edl(a.get("edl"))
    fields = {k: a.get(k) for k in
              ("track", "start", "duration", "in", "out", "gain", "fade_in", "fade_out", "label", "title")
              if k in a}
    if not fields:
        raise ToolError("nothing to update")
    try:
        clip = edlmod.update(doc, a.get("clip_id"), **fields)
    except edlmod.EdlError as exc:
        raise ToolError(str(exc))
    _save(doc, p)
    return f"clip {clip['id']} updated\n\n{edlmod.summarize(doc)}"


def clip_remove(a):
    doc, p = _edl(a.get("edl"))
    try:
        c = edlmod.remove(doc, a.get("clip_id"))
    except edlmod.EdlError as exc:
        raise ToolError(str(exc))
    _save(doc, p)
    return f"clip {c['id']} removed\n\n{edlmod.summarize(doc)}"


def clip_split(a):
    doc, p = _edl(a.get("edl"))
    try:
        left, right = edlmod.split(doc, a.get("clip_id"), a.get("at"))
    except edlmod.EdlError as exc:
        raise ToolError(str(exc))
    _save(doc, p)
    return f"split into {left['id']} and {right['id']}\n\n{edlmod.summarize(doc)}"


def effect_add(a):
    doc, p = _edl(a.get("edl"))
    eff = edlmod.add_effect(doc, a.get("clip_id"), a.get("service"), a.get("props"))
    _save(doc, p)
    return f"effect {eff['service']} added to {a['clip_id']}"


def effect_remove(a):
    doc, p = _edl(a.get("edl"))
    n = edlmod.remove_effects(doc, a.get("clip_id"), a.get("service"))
    _save(doc, p)
    return f"{n} effect(s) removed from {a['clip_id']}"


def export_kdenlive(a):
    doc, p = _edl(a.get("edl"))
    out = _sibling(p, ".kdenlive", a.get("out"))
    try:
        path = xmlgen.write_project(doc, out)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        raise ToolError(f"could not build project: {exc}")
    return f"Kdenlive project written: {path}\nOpen it with: kdenlive {path}"


def validate(a):
    doc, p = _edl(a.get("edl"))
    out = _sibling(p, ".kdenlive", a.get("out"))
    path = xmlgen.write_project(doc, out)
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        raise ToolError(f"generated XML is not well formed: {exc}")
    missing = sorted({c["media"] for c in doc["clips"]
                      if c.get("kind") == "media" and not os.path.exists(c["media"])})
    check = render.melt_check(path)
    lines = [f"project: {path}",
             f"xml: ok ({os.path.getsize(path)} bytes)",
             f"timeline: {edlmod.timeline_duration(doc):.2f}s, {len(doc['clips'])} clip(s)"]
    if missing:
        lines.append("missing media:")
        lines += ["  " + m for m in missing]
    lines.append(f"melt: {'ok' if check['ok'] else 'FAILED'} (rc={check['returncode']})")
    if check["problems"]:
        lines.append("problems:")
        lines += ["  " + ln for ln in check["problems"]]
    return "\n".join(lines)


def _even(value):
    """Snap a size to an even pixel count (libx264 requires even dimensions)."""
    return max(2, int(round(float(value) / 2.0)) * 2)


def _render_size(doc, width, height):
    """Fill in a missing render dimension from the project aspect ratio."""
    prof = doc["profile"]
    pw, ph = float(prof["width"]), float(prof["height"])
    if width is not None and height is None:
        width = _even(width)
        height = _even(width * ph / pw)
    elif height is not None and width is None:
        height = _even(height)
        width = _even(height * pw / ph)
    elif width is not None and height is not None:
        width, height = _even(width), _even(height)
    return width, height


def render_project(a):
    doc, p = _edl(a.get("edl"))
    project = xmlgen.write_project(doc, _sibling(p, ".kdenlive", a.get("project_out")))
    out = _sibling(p, ".mp4", a.get("out"))
    width, height = _render_size(doc, a.get("width") or None, a.get("height") or None)
    try:
        info = render.melt_render(
            project, out, fps=edlmod.fps(doc),
            crf=int(a.get("crf") or 20), preset=a.get("preset") or "medium",
            width=width, height=height,
            start=media.parse_time(a["start"], edlmod.fps(doc)) if a.get("start") else None,
            end=media.parse_time(a["end"], edlmod.fps(doc)) if a.get("end") else None,
            timeout=int(a.get("timeout") or 7200))
    except render.RenderError as exc:
        raise ToolError(str(exc))
    return (f"rendered {info['out']}\n  {info['duration']:.2f}s, "
            f"{info['width']}x{info['height']}, {info['size']/1e6:.1f} MB")


def preview(a):
    doc, p = _edl(a.get("edl"))
    project = xmlgen.write_project(doc, _sibling(p, ".kdenlive", a.get("project_out")))
    out = _sibling(p, ".preview.mp4", a.get("out"))
    width = _even(a.get("width") or 320)
    aspect = doc["profile"]["height"] / doc["profile"]["width"]
    height = _even(width * aspect)
    times = a.get("times")
    if times:
        times = list(times)[:MAX_FRAMES]
    count = min(max(1, int(a.get("count") or 4)), MAX_FRAMES)
    try:
        info, frames = render.preview(project, out, times=times,
                                      count=count, width=width,
                                      height=height, fps=edlmod.fps(doc),
                                      timeout=int(a.get("timeout") or 1800))
    except (render.RenderError, media.MediaError) as exc:
        raise ToolError(str(exc))
    text = (f"preview rendered: {info['out']} ({info['duration']:.2f}s, {width}px)\n"
            f"frames: {len(frames)}\n\n{edlmod.summarize(doc)}")
    return _result(text, frames if a.get("inline", True) else [])


def open_project(a):
    doc, p = _edl(a.get("edl"))
    project = xmlgen.write_project(doc, _sibling(p, ".kdenlive", a.get("out")))
    try:
        render.open_in_kdenlive(project)
    except render.RenderError as exc:
        raise ToolError(str(exc))
    return f"Kdenlive opened on {project}"


def output_check(a):
    p = _path(a.get("file"), "file")
    info = media.probe(p)
    lines = [_fmt_media(info)]
    try:
        levels = media.loudness(p)
        if levels:
            lines.append("audio: " + ", ".join(f"{k} {v} dB" for k, v in levels.items()))
        if levels.get("max_volume", -99) < -60:
            lines.append("WARNING: output looks silent")
    except media.MediaError:
        pass
    return "\n".join(lines)


def import_project(a):
    src = _path(a.get("project"), "project")
    if not os.path.exists(src):
        raise ToolError(f"project not found: {src}")
    try:
        doc = parse_kdenlive(src)
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"could not import: {exc}")
    out = edlmod.path_of(a.get("edl") or src.rsplit(".", 1)[0] + ".edl.json")
    edlmod.save(doc, out)
    return (f"imported {src} -> {out}\n"
            f"(best effort: per-clip effects, keyframes and transitions are not\n"
            f"carried over, only clip positions, sources and fades)\n\n{edlmod.summarize(doc)}")


# -------------------------------------------------------------- xml importing

def _tc_to_seconds(value, fps):
    if value is None:
        return 0.0
    return media.parse_time(value, fps)


def _props(el):
    out = {}
    for p in el.findall("property"):
        out[p.get("name")] = (p.text or "").strip()
    return out


def parse_kdenlive(path):
    tree = ET.parse(path)
    root = tree.getroot()
    prof = root.find("profile")
    fps = 25.0
    width, height = 1920, 1080
    if prof is not None:
        try:
            fps = int(prof.get("frame_rate_num")) / int(prof.get("frame_rate_den"))
        except (TypeError, ValueError, ZeroDivisionError):
            fps = 25.0
        width = int(prof.get("width") or width)
        height = int(prof.get("height") or height)
    services = {}
    for el in root:
        sid = el.get("id")
        if sid:
            services[sid] = el
    main_bin = services.get("main_bin")
    doc_uuid = ""
    if main_bin is not None:
        doc_uuid = _props(main_bin).get("kdenlive:docproperties.uuid", "")
    seq = None
    for el in root.findall("tractor"):
        if doc_uuid and el.get("id", "").strip("{}") == doc_uuid.strip("{}"):
            seq = el
            break
    if seq is None:
        for el in root.findall("tractor"):
            if any(p.get("name", "").startswith("kdenlive:sequenceproperties")
                   for p in el.findall("property")):
                seq = el
                break
    if seq is None:
        raise ValueError("no sequence tractor found")

    doc = {
        "edl_version": edlmod.EDL_VERSION,
        "name": os.path.splitext(os.path.basename(path))[0],
        "profile": {"width": width, "height": height, "fps": fps,
                    "progressive": True, "sample_aspect": 1.0},
        "tracks": [], "clips": [], "media": {},
    }
    # bin names by kdenlive id
    bin_names = {}
    for sid, el in services.items():
        pr = _props(el)
        if "kdenlive:id" in pr:
            bin_names[pr["kdenlive:id"]] = pr.get("kdenlive:clipname", "")

    video, audio = [], []
    for tr in seq.findall("track"):
        pid = tr.get("producer")
        hide = tr.get("hide") or ""
        svc = services.get(pid)
        if svc is None or svc.tag != "playlist":
            continue
        pr = _props(svc)
        if pr.get("kdenlive:playlistid") in ("black_track", "overlay_track", "timeline_preview"):
            continue
        is_audio = pr.get("kdenlive:audio_track") == "1" or hide == "video"
        (audio if is_audio else video).append(svc)
    audio.reverse()   # tractor holds An..A1, the EDL wants A1..An
    for kind, playlists in (("video", video), ("audio", audio)):
        for i, pl in enumerate(playlists, start=1):
            tid = f"{'V' if kind == 'video' else 'A'}{i}"
            doc["tracks"].append({"id": tid, "type": kind, "name": tid})
            _import_track(doc, pl, tid, fps, services, bin_names)
    if not doc["tracks"]:
        raise ValueError("no tracks recovered")
    return doc


def _import_track(doc, playlist, tid, fps, services, bin_names):
    cursor = 0.0
    for child in playlist:
        if child.tag == "blank":
            cursor += _tc_to_seconds(child.get("length"), fps)
            continue
        if child.tag != "entry":
            continue
        prod = services.get(child.get("producer"))
        if prod is None:
            continue
        pr = _props(prod)
        in_ = _tc_to_seconds(child.get("in"), fps)
        out = _tc_to_seconds(child.get("out"), fps)
        dur = max(1.0 / fps, out - in_ + 1.0 / fps)
        service = pr.get("mlt_service", "")
        resource = pr.get("resource", "")
        clip = {"id": edlmod.new_id(), "track": tid, "start": round(cursor, 6),
                "duration": round(dur, 6), "gain": 1.0, "effects": []}
        if service in ("color", "colour"):
            clip.update(kind="color", color=resource)
        elif service == "kdenlivetitle":
            text = ""
            m = re.search(r"<content[^>]*>(.*?)</content>", pr.get("xmldata", ""), re.S)
            if m:
                text = m.group(1)
            clip.update(kind="title",
                        title={"text": text, "duration": round(dur, 6)})
        else:
            if not resource or not os.path.exists(resource):
                cursor += dur
                continue
            clip.update(kind="media", media=os.path.abspath(resource),
                        **{"in": round(in_, 6), "out": round(in_ + dur, 6)})
            info = doc["media"].get(clip["media"])
            if not info:
                try:
                    doc["media"][clip["media"]] = media.probe(clip["media"])
                except media.MediaError:
                    pass
        # simple fades/gain from brightness/volume keyframes
        for flt in prod.findall("filter"):
            fp = _props(flt)
            if fp.get("mlt_service") == "brightness" and fp.get("level"):
                kf = _parse_kf(fp["level"], fps)
                if kf and kf[0][1] == 0:
                    clip["fade_in"] = round(kf[1][0] - kf[0][0], 6) if len(kf) > 1 else 0
                if kf and kf[-1][1] == 0:
                    clip["fade_out"] = round(kf[-1][0] - kf[-2][0], 6) if len(kf) > 1 else 0
            elif fp.get("mlt_service") == "volume" and fp.get("level"):
                _import_volume(clip, fp["level"], fps)
        doc["clips"].append(clip)
        cursor += dur


def _kf_time(pos, fps):
    """MLT keyframe positions are frames; timecodes stay timecodes."""
    s = str(pos).strip()
    if re.match(r"^\d+:\d{1,2}:\d{1,2}(?:\.\d+)?$", s):
        return _tc_to_seconds(s, fps)
    if s.endswith("f"):
        s = s[:-1]
    try:
        return float(s) / fps
    except ValueError:
        return _tc_to_seconds(s, fps)


def _parse_kf(value, fps):
    points = []
    for part in str(value).split(";"):
        if "=" not in part:
            continue
        pos, val = part.split("=", 1)
        try:
            points.append((_kf_time(pos, fps), float(val)))
        except (ValueError, media.MediaError):
            continue
    return points


def _import_volume(clip, value, fps):
    """Import Kdenlive's Volume filter (dB level) as gain and fades."""
    text = str(value or "").strip()
    if not text:
        return
    if "=" not in text:
        try:
            db = float(text)
        except ValueError:
            return
        clip["gain"] = round(10.0 ** (db / 20.0), 6)
        return
    kf = _parse_kf(text, fps)
    if not kf:
        return
    silent = -99.0
    # The non-silent part of the curve carries the constant gain.
    plateau = next((v for _t, v in kf if v > silent), None)
    if plateau is not None:
        clip["gain"] = round(10.0 ** (plateau / 20.0), 6)
    if kf[0][1] <= silent:
        plateau_pt = next(((t, v) for t, v in kf[1:] if v > silent), None)
        if plateau_pt is not None:
            fade = plateau_pt[0] - kf[0][0]
            if fade > 0:
                clip["fade_in"] = round(fade, 6)
    if kf[-1][1] <= silent:
        before = next(((t, v) for t, v in reversed(kf[:-1]) if v > silent), None)
        if before is not None:
            fade = kf[-1][0] - before[0]
            if fade > 0:
                clip["fade_out"] = round(fade, 6)


# ------------------------------------------------------------------ registry

TOOLS = [
    {
        "name": "project_create",
        "description": "Create a new edit project (EDL JSON). Returns a timeline overview.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "path of the EDL ('.edl.json' is appended if missing)"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "fps": {"type": "number"},
                "profile": {"type": "string", "description": "shortcut: 1080p25, 1080p30, 1080p24, 720p25, 720p30, 1080p60, vertical1080, 4k30"},
                "name": {"type": "string"},
            },
            "required": ["path"],
        },
        "handler": project_create,
    },
    {
        "name": "project_show",
        "description": "Show the timeline of a project: tracks, clips, in/out points, fades, duration.",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}}, "required": ["edl"]},
        "handler": project_show,
    },
    {
        "name": "project_set_profile",
        "description": "Change the project frame size / frame rate.",
        "inputSchema": {
            "type": "object",
            "properties": {"edl": {"type": "string"}, "width": {"type": "integer"},
                           "height": {"type": "integer"}, "fps": {"type": "number"},
                           "progressive": {"type": "boolean"}},
            "required": ["edl"],
        },
        "handler": project_set_profile,
    },
    {
        "name": "media_probe",
        "description": "Inspect one or more media files (duration, resolution, fps, codecs, audio info).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"},
                           "paths": {"type": "array", "items": {"type": "string"}}},
        },
        "handler": media_probe,
    },
    {
        "name": "media_scan",
        "description": "List usable media in a directory with their metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {"dir": {"type": "string"}, "recursive": {"type": "boolean"}},
            "required": ["dir"],
        },
        "handler": media_scan,
    },
    {
        "name": "track_add",
        "description": "Add a video or audio track.",
        "inputSchema": {
            "type": "object",
            "properties": {"edl": {"type": "string"}, "type": {"type": "string", "enum": ["video", "audio"]},
                           "name": {"type": "string"}},
            "required": ["edl", "type"],
        },
        "handler": track_add,
    },
    {
        "name": "track_remove",
        "description": "Remove a track and everything on it.",
        "inputSchema": {
            "type": "object",
            "properties": {"edl": {"type": "string"}, "track": {"type": "string"}},
            "required": ["edl", "track"],
        },
        "handler": track_remove,
    },
    {
        "name": "clip_add",
        "description": ("Place a clip on a track. kind=media uses 'media' (in/out are source seconds), "
                        "kind=color uses 'color' + duration, kind=title uses 'title' {text, style} + duration. "
                        "Times accept seconds or '12.5s', '150f', 'HH:MM:SS.mmm'. start=append places it right "
                        "after the last clip on the track."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "edl": {"type": "string"},
                "track": {"type": "string"},
                "kind": {"type": "string", "enum": ["media", "color", "title"]},
                "media": {"type": "string"},
                "color": {"type": "string", "description": "#rrggbb or a color name"},
                "title": {"type": "object", "properties": {
                    "text": {"type": "string"},
                    "style": {"type": "object", "properties": {
                        "font": {"type": "string"}, "size": {"type": "integer"},
                        "color": {"type": "string"}, "outline": {"type": "integer"},
                        "outline_color": {"type": "string"}, "x": {"type": "integer"},
                        "y": {"type": "integer"}, "weight": {"type": "integer"}}}}},
                "start": {"description": "timeline position in seconds"},
                "in": {"description": "source in point"},
                "out": {"description": "source out point"},
                "duration": {"description": "clip length"},
                "gain": {"type": "number", "description": "audio gain multiplier, 1.0 = original"},
                "fade_in": {"type": "number"},
                "fade_out": {"type": "number"},
                "label": {"type": "string"},
                "append": {"type": "boolean", "description": "append after the last clip of the track"},
            },
            "required": ["edl", "track"],
        },
        "handler": clip_add,
    },
    {
        "name": "clip_update",
        "description": "Move, retime, trim or re-gain an existing clip (same fields as clip_add).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "edl": {"type": "string"}, "clip_id": {"type": "string"},
                "track": {"type": "string"}, "start": {}, "duration": {}, "in": {}, "out": {},
                "gain": {"type": "number"}, "fade_in": {"type": "number"},
                "fade_out": {"type": "number"}, "label": {"type": "string"},
                "title": {"type": "object"},
            },
            "required": ["edl", "clip_id"],
        },
        "handler": clip_update,
    },
    {
        "name": "clip_remove",
        "description": "Delete a clip from the timeline.",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "clip_id": {"type": "string"}},
                        "required": ["edl", "clip_id"]},
        "handler": clip_remove,
    },
    {
        "name": "clip_split",
        "description": "Split a clip at an absolute timeline position.",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "clip_id": {"type": "string"},
                                                         "at": {}},
                        "required": ["edl", "clip_id", "at"]},
        "handler": clip_split,
    },
    {
        "name": "effect_add",
        "description": ("Add an MLT filter to a clip, e.g. service 'brightness' with props "
                        "{level: 1.2}, 'saturation' {saturation: 1.3}, 'frei0r.cartoon', "
                        "'affine' {transition.geometry: '0=0 0 1920 1080 1'}. Props are raw MLT values."),
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "clip_id": {"type": "string"},
                                                         "service": {"type": "string"}, "props": {"type": "object"}},
                        "required": ["edl", "clip_id", "service"]},
        "handler": effect_add,
    },
    {
        "name": "effect_remove",
        "description": "Remove effects from a clip (all, or just one service).",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "clip_id": {"type": "string"},
                                                         "service": {"type": "string"}},
                        "required": ["edl", "clip_id"]},
        "handler": effect_remove,
    },
    {
        "name": "export_kdenlive",
        "description": "Compile the EDL into a .kdenlive project you can open and polish in the Kdenlive GUI.",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "out": {"type": "string"}},
                        "required": ["edl"]},
        "handler": export_kdenlive,
    },
    {
        "name": "validate",
        "description": "Compile and validate the project: XML well-formedness plus a full melt render to the null consumer.",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "out": {"type": "string"}},
                        "required": ["edl"]},
        "handler": validate,
    },
    {
        "name": "render",
        "description": "Render the project to a file with melt (H.264/AAC). Use crf 18-23 for quality, preset ultrafast for speed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "edl": {"type": "string"}, "out": {"type": "string"},
                "crf": {"type": "integer"}, "preset": {"type": "string"},
                "width": {"type": "integer"}, "height": {"type": "integer"},
                "start": {}, "end": {}, "timeout": {"type": "integer"},
            },
            "required": ["edl", "out"],
        },
        "handler": render_project,
    },
    {
        "name": "preview",
        "description": ("Fast low-resolution render plus frames returned as images, so you can "
                        "actually look at the result. Best loop: edit -> preview -> look -> fix."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "edl": {"type": "string"}, "out": {"type": "string"},
                "times": {"type": "array", "items": {"type": "number"},
                          "description": "timeline seconds to grab"},
                "count": {"type": "integer", "description": "frames spread over the timeline when times is omitted"},
                "width": {"type": "integer"},
                "inline": {"type": "boolean", "description": "return the frames as images (default true)"},
            },
            "required": ["edl"],
        },
        "handler": preview,
    },
    {
        "name": "open_in_kdenlive",
        "description": "Write the project and open it in the Kdenlive GUI.",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "out": {"type": "string"}},
                        "required": ["edl"]},
        "handler": open_project,
    },
    {
        "name": "output_check",
        "description": "Inspect a rendered file: streams, duration, audio peak level (catches silent renders).",
        "inputSchema": {"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]},
        "handler": output_check,
    },
    {
        "name": "import_kdenlive",
        "description": "Import an existing .kdenlive project into an EDL (best effort: positions, sources, fades).",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "edl": {"type": "string"}},
                        "required": ["project"]},
        "handler": import_project,
    },
]
