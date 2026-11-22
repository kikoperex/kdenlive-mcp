# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""MCP tool definitions for the Kdenlive/MLT editing server."""

import base64
import json
import math
import os
import re
import xml.etree.ElementTree as ET

from . import edl as edlmod
from . import media
from . import render
from . import xmlgen


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


def _safe_output(edl_path, doc, out, what="output"):
    """Resolve an output path, refusing to clobber the EDL or any input media."""
    p = _path(out, what)
    protected = [("project EDL", edl_path)]
    protected += [("input media", m) for m in (doc.get("media") or {})]
    target = os.path.normcase(os.path.realpath(p))
    for label, item in protected:
        try:
            other = os.path.normcase(os.path.realpath(_path(item, label)))
        except (ToolError, OSError, ValueError):
            continue
        if target == other:
            raise ToolError(f"{what} {p} is the same file as the {label}; "
                            f"choose another output path")
    return p


MAX_IMAGE_BYTES = 900_000
MAX_FRAMES = 24
MAX_TOTAL_IMAGE_BYTES = 6_000_000
TIME_DESCRIPTION = "seconds, or '12.5s', '150f', 'HH:MM:SS.mmm'"


def _time_schema(description=None):
    text = description or "time"
    if TIME_DESCRIPTION not in text:
        text = f"{text} ({TIME_DESCRIPTION})"
    return {"anyOf": [{"type": "number"}, {"type": "string"}],
            "description": text}


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
    used = 0
    for i, p in enumerate(images):
        if i >= MAX_FRAMES:
            skipped += 1
            continue
        b = _image_block(p)
        if not b:
            skipped += 1
            continue
        size = len(b["data"])
        if used + size > MAX_TOTAL_IMAGE_BYTES:
            skipped += 1
            continue
        used += size
        blocks.append(b)
    if skipped:
        blocks.append({"type": "text",
                       "text": f"({skipped} frame(s) omitted: too large, unreadable, "
                               f"or over the {MAX_TOTAL_IMAGE_BYTES // 1_000_000} MB image "
                               f"budget; retry with a smaller width or fewer frames)"})
    return blocks


# --------------------------------------------------------------------- tools

def project_create(a):
    def num(key, default):
        value = a.get(key)
        return default if value is None else float(value)

    p = edlmod.path_of(_path(a.get("path")))
    try:
        doc = edlmod.new(p, width=int(num("width", 1920)),
                         height=int(num("height", 1080)), fps=num("fps", 25),
                         name=a.get("name"), profile=a.get("profile"),
                         overwrite=bool(a.get("overwrite")))
    except (edlmod.EdlError, ValueError) as exc:
        raise ToolError(str(exc))
    return f"EDL created: {p}\n\n{edlmod.summarize(doc)}"


def project_show(a):
    doc, p = _edl(a.get("edl"))
    return f"{p}\n\n{edlmod.summarize(doc)}"


def project_set_profile(a):
    doc, p = _edl(a.get("edl"))
    prof = doc["profile"]
    try:
        for key in ("width", "height"):
            if a.get(key) is not None:
                value = int(a[key])
                if value <= 0 or value % 2:
                    raise ValueError(f"{key} must be a positive even number")
                prof[key] = value
        if a.get("fps") is not None:
            value = float(a["fps"])
            if not math.isfinite(value) or value <= 0:
                raise ValueError("fps must be a finite number > 0")
            prof["fps"] = value
        if a.get("progressive") is not None:
            prof["progressive"] = bool(a["progressive"])
        _save(doc, p)
    except (edlmod.EdlError, ValueError, TypeError) as exc:
        raise ToolError(str(exc))
    f = edlmod.fps(doc)
    return f"profile now {prof['width']}x{prof['height']} @ {f:g} fps ({p})"


def media_probe(a):
    paths = a.get("paths") or ([a["path"]] if a.get("path") else [])
    if not paths:
        raise ToolError("give path or paths")
    out = []
    for item in paths:
        p = _path(item, "path")
        try:
            info = media.probe(p)
        except media.MediaError as exc:
            out.append(f"{p} error: {exc}")
            continue
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
    try:
        limit = 1000 if a.get("limit") is None else int(a["limit"])
    except (TypeError, ValueError):
        raise ToolError("limit must be a positive integer")
    if limit <= 0:
        raise ToolError("limit must be a positive integer")
    lines = [_fmt_media(i) for i in items[:limit]]
    if len(items) > limit:
        lines.append(f"(showing {limit} of {len(items)} files; raise limit to see more)")
    return "\n".join(lines)


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
        removed = len([c for c in doc["clips"] if c["track"] == tid])
        doc["tracks"] = [t for t in doc["tracks"] if t["id"] != tid]
        doc["clips"] = [c for c in doc["clips"] if c["track"] != tid]
        _save(doc, p)
        return (f"track {tid} removed with {removed} clip(s)\n\n"
                f"{edlmod.summarize(doc)}")
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
    except (edlmod.EdlError, media.MediaError) as exc:
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
    try:
        eff = edlmod.add_effect(doc, a.get("clip_id"), a.get("service"), a.get("props"))
    except (edlmod.EdlError, media.MediaError) as exc:
        raise ToolError(str(exc))
    clip = edlmod.find(doc, a.get("clip_id"))
    index = len(clip.get("effects", [])) - 1
    _save(doc, p)
    props = json.dumps(eff.get("props") or {}, ensure_ascii=False, sort_keys=True)
    return (f"effect {index} added to {clip['id']}: {eff['service']} {props}")


def effect_remove(a):
    doc, p = _edl(a.get("edl"))
    kwargs = {}
    if a.get("effect_index") is not None:
        try:
            kwargs["index"] = int(a["effect_index"])
        except (TypeError, ValueError):
            raise ToolError("effect_index must be a non-negative integer")
        if kwargs["index"] < 0:
            raise ToolError("effect_index must be a non-negative integer")
    try:
        n = edlmod.remove_effects(doc, a.get("clip_id"), a.get("service"), **kwargs)
    except (edlmod.EdlError, media.MediaError) as exc:
        raise ToolError(str(exc))
    _save(doc, p)
    return f"{n} effect(s) removed from {a['clip_id']}"


def export_kdenlive(a):
    doc, p = _edl(a.get("edl"))
    out = _safe_output(p, doc, _sibling(p, ".kdenlive", a.get("out")), "out")
    try:
        path = xmlgen.write_project(doc, out)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        raise ToolError(f"could not build project: {exc}")
    return f"Kdenlive project written: {path}\nOpen it with: kdenlive {path}"


def validate(a):
    doc, p = _edl(a.get("edl"))
    out = _safe_output(p, doc, _sibling(p, ".kdenlive", a.get("out")), "out")
    try:
        timeout = 900 if a.get("timeout") is None else int(a["timeout"])
    except (TypeError, ValueError):
        raise ToolError("timeout must be a positive integer")
    if timeout <= 0:
        raise ToolError("timeout must be a positive integer")
    try:
        path = xmlgen.write_project(doc, out)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        raise ToolError(f"could not build project: {exc}")
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        raise ToolError(f"generated XML is not well formed: {exc}")
    missing = sorted({c["media"] for c in doc["clips"]
                      if c.get("kind") == "media" and not os.path.exists(c["media"])})
    lines = [f"project: {path}",
             f"xml: ok ({os.path.getsize(path)} bytes)",
             f"timeline: {edlmod.timeline_duration(doc):.2f}s, {len(doc['clips'])} clip(s)"]
    if missing:
        lines.append("missing media:")
        lines += ["  " + m for m in missing]
    if a.get("skip_melt"):
        lines.append("melt: skipped (skip_melt=true)")
        return "\n".join(lines)
    try:
        check = render.melt_check(path, timeout=timeout)
    except media.MediaError as exc:
        raise ToolError(str(exc))
    lines.append(f"melt: {'ok' if check['ok'] else 'FAILED'} (rc={check['returncode']})")
    if check["problems"]:
        lines.append("problems:")
        lines += ["  " + ln for ln in check["problems"]]
    return "\n".join(lines)


def _even4(value):
    """Snap a size to a multiple of 4 (libx264/yuv420p needs 4-aligned sizes)."""
    return max(4, int(round(float(value) / 4.0)) * 4)


def _render_size(doc, width, height):
    """Fill in a missing render dimension from the project aspect ratio."""
    prof = doc["profile"]
    pw, ph = float(prof["width"]), float(prof["height"])
    if width is not None and height is None:
        width = _even4(width)
        height = _even4(width * ph / pw)
    elif height is not None and width is None:
        height = _even4(height)
        width = _even4(height * pw / ph)
    elif width is not None and height is not None:
        width, height = _even4(width), _even4(height)
    return width, height


def render_project(a):
    doc, p = _edl(a.get("edl"))
    fps = edlmod.fps(doc)
    total = edlmod.timeline_duration(doc)
    project_out = _safe_output(p, doc, _sibling(p, ".kdenlive", a.get("project_out")),
                               "project_out")
    out = _safe_output(p, doc, _sibling(p, ".mp4", a.get("out")), "out")
    if os.path.realpath(out) == os.path.realpath(project_out):
        raise ToolError("out and project_out must be different paths")
    try:
        start = media.parse_time(a.get("start"), fps)
        end = media.parse_time(a.get("end"), fps)
    except media.MediaError as exc:
        raise ToolError(str(exc))
    if start is None:
        start = 0.0
    if end is None:
        end = total
    tolerance = 1.0 / fps + 1e-9 if fps > 0 else 1e-9
    if start < 0:
        raise ToolError("start must be >= 0")
    if end <= start:
        raise ToolError(f"end ({end:.3f}s) must be after start ({start:.3f}s)")
    if end > total + tolerance:
        raise ToolError(f"end ({end:.3f}s) is past the end of the timeline ({total:.3f}s)")
    try:
        crf = 20 if a.get("crf") is None else int(a["crf"])
        timeout = 7200 if a.get("timeout") is None else int(a["timeout"])
    except (TypeError, ValueError):
        raise ToolError("crf and timeout must be integers")
    if not 0 <= crf <= 51:
        raise ToolError("crf must be between 0 and 51")
    if timeout <= 0:
        raise ToolError("timeout must be a positive integer")
    preset = "medium" if a.get("preset") is None else a.get("preset")
    for name in ("width", "height"):
        if a.get(name) is not None:
            try:
                value = float(a[name])
            except (TypeError, ValueError):
                raise ToolError(f"{name} must be a positive number")
            if not math.isfinite(value) or value <= 0:
                raise ToolError(f"{name} must be a positive number")
    width, height = _render_size(doc,
                                 None if a.get("width") is None else a.get("width"),
                                 None if a.get("height") is None else a.get("height"))
    dry_run = bool(a.get("dry_run"))
    project = xmlgen.write_project(doc, project_out)
    try:
        info = render.melt_render(
            project, out, fps=fps, crf=crf, preset=preset,
            width=width, height=height,
            start=start if a.get("start") is not None else None,
            end=end if a.get("end") is not None else None,
            timeout=timeout, dry_run=dry_run)
    except (render.RenderError, media.MediaError) as exc:
        raise ToolError(str(exc))
    if dry_run:
        return (f"dry run: project {project}\nrender -> {info['out']}\n"
                f"command: {' '.join(str(x) for x in info['cmd'])}")
    return (f"rendered {info['out']}\n  {info['duration'] or 0:.2f}s, "
            f"{info['width']}x{info['height']}, {info['size']/1e6:.1f} MB")


def preview(a):
    doc, p = _edl(a.get("edl"))
    fps = edlmod.fps(doc)
    project_out = _safe_output(p, doc, _sibling(p, ".kdenlive", a.get("project_out")),
                               "project_out")
    out = _safe_output(p, doc, _sibling(p, ".preview.mp4", a.get("out")), "out")
    if os.path.realpath(out) == os.path.realpath(project_out):
        raise ToolError("out and project_out must be different paths")
    width = a.get("width") if a.get("width") is not None else 320
    try:
        width = float(width)
    except (TypeError, ValueError):
        raise ToolError("width must be a positive number")
    if not math.isfinite(width) or width <= 0:
        raise ToolError("width must be a positive number")
    width = min(max(_even4(width), 16), 1920)
    aspect = doc["profile"]["height"] / doc["profile"]["width"]
    height = _even4(width * aspect)
    times = a.get("times")
    if times:
        try:
            times = [media.parse_time(t, fps) for t in list(times)[:MAX_FRAMES]]
        except media.MediaError as exc:
            raise ToolError(str(exc))
        times = [t for t in times if t is not None]
    count = a.get("count") if a.get("count") is not None else 4
    try:
        count = min(max(1, int(count)), MAX_FRAMES)
        timeout = 1800 if a.get("timeout") is None else int(a["timeout"])
    except (TypeError, ValueError):
        raise ToolError("count and timeout must be integers")
    if timeout <= 0:
        raise ToolError("timeout must be a positive integer")
    project = xmlgen.write_project(doc, project_out)
    try:
        info, frames = render.preview(project, out, times=times,
                                      count=count, width=width,
                                      height=height, fps=fps,
                                      timeout=timeout)
    except (render.RenderError, media.MediaError) as exc:
        raise ToolError(str(exc))
    text = (f"preview rendered: {info['out']} ({info['duration']:.2f}s, {width}px)\n"
            f"frames: {len(frames)}\n\n{edlmod.summarize(doc)}")
    return _result(text, frames if a.get("inline", True) else [])


def open_project(a):
    doc, p = _edl(a.get("edl"))
    out = _safe_output(p, doc, _sibling(p, ".kdenlive", a.get("out")), "out")
    project = xmlgen.write_project(doc, out)
    try:
        render.open_in_kdenlive(project)
    except render.RenderError as exc:
        raise ToolError(str(exc))
    return f"Kdenlive opened on {project}"


def output_check(a):
    p = _path(a.get("file"), "file")
    try:
        info = media.probe(p)
    except media.MediaError as exc:
        raise ToolError(str(exc))
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
    if os.path.exists(out) and not a.get("overwrite"):
        raise ToolError(f"EDL already exists: {out}. Pass overwrite=true to replace it, "
                        f"or give a different 'edl' path.")
    dropped = doc.pop("_import_dropped", [])
    try:
        edlmod.save(doc, out)
    except (edlmod.EdlError, media.MediaError) as exc:
        raise ToolError(f"could not save the imported EDL: {exc}")
    lines = [f"imported {src} -> {out}",
             "(best effort: per-clip effects, keyframes and transitions are not",
             "carried over; positions, sources, fades and audio gain are)"]
    if dropped:
        lines.append(f"dropped {len(dropped)} clip(s) whose media is missing:")
        for tid, at, resource in dropped[:20]:
            lines.append(f"  {tid} @ {at:.2f}s  {resource or '(no resource)'}")
        if len(dropped) > 20:
            lines.append(f"  ... and {len(dropped) - 20} more")
    lines.append("")
    lines.append(edlmod.summarize(doc))
    return "\n".join(lines)


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


MAX_PROJECT_BYTES = 64 * 1024 * 1024


def _load_kdenlive_xml(path):
    """Parse a .kdenlive file with a size cap and no DTD/entity expansion."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise ValueError(f"cannot stat project: {exc}")
    if size > MAX_PROJECT_BYTES:
        raise ValueError(f"project is too large ({size} bytes; limit {MAX_PROJECT_BYTES})")
    with open(path, "rb") as f:
        data = f.read(MAX_PROJECT_BYTES + 1)
    lower = data.lower()
    if b"<!doctype" in lower or b"<!entity" in lower:
        raise ValueError("DTD/entity declarations are not allowed in imported projects")
    return ET.fromstring(data)


def parse_kdenlive(path):
    root = _load_kdenlive_xml(path)
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
        in_ = _tc_to_seconds(child.get("in"), fps)
        out = _tc_to_seconds(child.get("out"), fps)
        dur = max(1.0 / fps, out - in_ + 1.0 / fps)
        prod = services.get(child.get("producer"))
        if prod is None:
            # keep the timeline length correct even when a producer is unresolved
            cursor += dur
            continue
        pr = _props(prod)
        service = pr.get("mlt_service", "")
        resource = pr.get("resource", "")
        clip = {"id": edlmod.new_id(), "track": tid, "start": round(cursor, 6),
                "duration": round(dur, 6), "gain": 1.0, "effects": []}
        label = (bin_names.get(_props(child).get("kdenlive:id"))
                 or pr.get("kdenlive:clipname"))
        if label:
            clip["label"] = label
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
                doc.setdefault("_import_dropped", []).append((tid, cursor, resource))
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
                "width": {"type": "integer", "description": "frame width in pixels (default 1920; profile overrides it)"},
                "height": {"type": "integer", "description": "frame height in pixels (default 1080; profile overrides it)"},
                "fps": {"type": "number", "description": "frames per second (default 25; profile overrides it)"},
                "profile": {"type": "string", "enum": sorted(edlmod.DEFAULT_PROFILES),
                            "description": "profile shortcut that sets width/height/fps"},
                "name": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False,
                              "description": "replace the EDL if it already exists (default false)"},
            },
            "required": ["path"],
        },
        "annotations": {"destructiveHint": True},
        "handler": project_create,
    },
    {
        "name": "project_show",
        "description": "Show the timeline of a project: tracks, clips, in/out points, fades, duration.",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}}, "required": ["edl"]},
        "annotations": {"readOnlyHint": True},
        "handler": project_show,
    },
    {
        "name": "project_set_profile",
        "description": "Change the project frame size / frame rate.",
        "inputSchema": {
            "type": "object",
            "properties": {"edl": {"type": "string"},
                           "width": {"type": "integer", "description": "positive even frame width"},
                           "height": {"type": "integer", "description": "positive even frame height"},
                           "fps": {"type": "number", "description": "positive frame rate"},
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
            "anyOf": [{"required": ["path"]}, {"required": ["paths"]}],
        },
        "annotations": {"readOnlyHint": True},
        "handler": media_probe,
    },
    {
        "name": "media_scan",
        "description": "List usable media in a directory with their metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {"dir": {"type": "string"}, "recursive": {"type": "boolean"},
                           "limit": {"type": "integer", "default": 1000, "minimum": 1,
                                     "description": "maximum number of files to list (default 1000)"}},
            "required": ["dir"],
        },
        "annotations": {"readOnlyHint": True},
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
        "annotations": {"destructiveHint": True},
        "handler": track_remove,
    },
    {
        "name": "clip_add",
        "description": ("Place a clip on a track. kind=media requires 'media' (in/out are source seconds), "
                        "kind=color requires 'color' and duration, kind=title requires 'title' {text, style} "
                        "and duration. Times accept seconds or '12.5s', '150f', 'HH:MM:SS.mmm'. When append "
                        "is true, start is ignored and the clip goes right after the last clip on the track."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "edl": {"type": "string"},
                "track": {"type": "string"},
                "kind": {"type": "string", "enum": ["media", "color", "title"]},
                "media": {"type": "string", "description": "required when kind=media"},
                "color": {"type": "string", "description": "required when kind=color: #rrggbb or a color name"},
                "title": {"type": "object", "description": "required when kind=title: {text, style}",
                          "properties": {
                    "text": {"type": "string"},
                    "style": {"type": "object", "properties": {
                        "font": {"type": "string"}, "size": {"type": "integer"},
                        "color": {"type": "string"}, "outline": {"type": "integer"},
                        "outline_color": {"type": "string"}, "x": {"type": "integer"},
                        "y": {"type": "integer"}, "weight": {"type": "integer"}}}}},
                "start": _time_schema("timeline position; ignored when append is true"),
                "in": _time_schema("source in point (media clips only)"),
                "out": _time_schema("source out point (media clips only)"),
                "duration": _time_schema("clip length; required for color/title clips"),
                "gain": {"type": "number", "description": "audio gain multiplier, 1.0 = original"},
                "fade_in": {"type": "number"},
                "fade_out": {"type": "number"},
                "label": {"type": "string"},
                "append": {"type": "boolean", "description": "append after the last clip of the track (start is ignored)"},
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
                "track": {"type": "string"},
                "start": _time_schema("new timeline position"),
                "duration": _time_schema("new clip length (source in/out follow for media)"),
                "in": _time_schema("new source in point (media clips)"),
                "out": _time_schema("new source out point (media clips)"),
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
        "annotations": {"destructiveHint": True},
        "handler": clip_remove,
    },
    {
        "name": "clip_split",
        "description": "Split a clip at an absolute timeline position.",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "clip_id": {"type": "string"},
                                                         "at": _time_schema("absolute timeline position to split at")},
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
        "description": "Remove effects from a clip (all, one service, or one index from effect_add).",
        "inputSchema": {"type": "object", "properties": {"edl": {"type": "string"}, "clip_id": {"type": "string"},
                                                         "service": {"type": "string"},
                                                         "effect_index": {"type": "integer", "minimum": 0,
                                                                          "description": "0-based effect index shown by effect_add"}},
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
        "description": ("Compile and validate the project: XML well-formedness, missing media, and "
                        "a full melt render to the null consumer (skip with skip_melt)."),
        "inputSchema": {"type": "object", "properties": {
                            "edl": {"type": "string"}, "out": {"type": "string"},
                            "timeout": {"type": "integer", "default": 900, "minimum": 1,
                                        "description": "melt timeout in seconds (default 900)"},
                            "skip_melt": {"type": "boolean", "default": False,
                                          "description": "only check the XML and timeline; do not run melt"}},
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
                "project_out": {"type": "string",
                                "description": "path of the intermediate .kdenlive project"},
                "crf": {"type": "integer", "minimum": 0, "maximum": 51,
                        "description": "H.264 quality (default 20; lower is better)"},
                "preset": {"type": "string", "description": "x264 preset (default medium)"},
                "width": {"type": "integer", "description": "output width; height is derived when omitted"},
                "height": {"type": "integer", "description": "output height; width is derived when omitted"},
                "start": _time_schema("timeline start position (default 0)"),
                "end": _time_schema("timeline end position (default timeline duration)"),
                "timeout": {"type": "integer", "default": 7200, "minimum": 1,
                            "description": "render timeout in seconds (default 7200)"},
                "dry_run": {"type": "boolean", "default": False,
                            "description": "return the melt command without rendering"},
            },
            "required": ["edl", "out"],
        },
        "annotations": {"destructiveHint": True},
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
                "project_out": {"type": "string",
                                "description": "path of the intermediate .kdenlive project"},
                "times": {"type": "array",
                          "items": _time_schema(),
                          "description": "timeline positions to grab (at most 24)"},
                "count": {"type": "integer", "minimum": 1, "maximum": 24,
                          "description": "frames spread over the timeline when times is omitted (default 4)"},
                "width": {"type": "integer", "minimum": 16, "maximum": 1920,
                          "description": "preview width in pixels (default 320; snapped to even)"},
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
        "annotations": {"readOnlyHint": True},
        "handler": output_check,
    },
    {
        "name": "import_kdenlive",
        "description": "Import an existing .kdenlive project into an EDL (best effort: positions, sources, fades).",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "edl": {"type": "string"},
                                                         "overwrite": {"type": "boolean", "default": False,
                                                                       "description": "replace an existing EDL (default false)"}},
                        "required": ["project"]},
        "handler": import_project,
    },
]
