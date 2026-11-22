# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""Edit Decision List: the JSON document the agent edits.

The .kdenlive file is a *build artifact* of this EDL, so the agent never has to
perform fragile in-place XML surgery. Kdenlive can still open the exported
project, and `kdenlive_import` converts a hand-edited project back.

Times are seconds (float) in the JSON; helpers accept '12.5s', '150f',
'HH:MM:SS.mmm' as well.
"""

import json
import math
import os
import re
import tempfile
import uuid

from . import media

EDL_VERSION = 1
DEFAULT_PROFILES = {
    "1080p25": {"width": 1920, "height": 1080, "fps": 25},
    "1080p30": {"width": 1920, "height": 1080, "fps": 30},
    "1080p24": {"width": 1920, "height": 1080, "fps": 24},
    "1080p2997": {"width": 1920, "height": 1080, "fps": 29.97},
    "1080p60": {"width": 1920, "height": 1080, "fps": 60},
    "720p25": {"width": 1280, "height": 720, "fps": 25},
    "720p30": {"width": 1280, "height": 720, "fps": 30},
    "vertical1080": {"width": 1080, "height": 1920, "fps": 30},
    "4k30": {"width": 3840, "height": 2160, "fps": 30},
}


class EdlError(Exception):
    pass


# ------------------------------------------------------------------- helpers

def _snap(t, fps):
    """Round a time in seconds to the nearest frame boundary."""
    f = float(fps)
    return round(float(t) * f) / f


def _snap_up(t, fps):
    """Round a time up to the next frame boundary (used when appending)."""
    f = float(fps)
    return math.ceil(float(t) * f - 1e-9) / f


def _frame(fps):
    return 1.0 / float(fps)


def _too_short(duration, fps):
    """True when a duration is below one frame (allowing 1us of rounding)."""
    return float(duration) < _frame(fps) - 1e-6


# Times are stored rounded to 6 decimals and XML timecodes to milliseconds, so
# two adjacent clips can look overlapped by a few microseconds after a roundtrip.
# Anything below this is well under a frame and is re-timed by the compiler anyway.
OVERLAP_EPS = 1e-5


def _parse_time(value, name, fps):
    try:
        t = media.parse_time(value, fps)
    except media.MediaError as exc:
        raise EdlError(f"{name}: {exc}")
    if t is None:
        raise EdlError(f"{name} must be a time value, got {value!r}")
    if not math.isfinite(float(t)):
        raise EdlError(f"{name} must be finite")
    return float(t)


def _time(value, name, fps, minimum=0.0):
    """Parse, validate and frame-snap a time field."""
    t = _parse_time(value, name, fps)
    if t < minimum:
        raise EdlError(f"{name} must be >= {minimum:g}")
    return _snap(t, fps)


def _number(value, name, minimum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EdlError(f"{name} must be a number")
    if not math.isfinite(number):
        raise EdlError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise EdlError(f"{name} must be >= {minimum:g}")
    return number


# ---------------------------------------------------------------- persistence

def path_of(path):
    p = os.path.abspath(os.path.expanduser(str(path)))
    if p.endswith(".edl.json") or p.endswith(".json"):
        return p
    return p + ".edl.json"


def base_path(path):
    """'/x/p.edl.json' or '/x/p.json' -> '/x/p' (for .kdenlive/.mp4 siblings)."""
    return re.sub(r"(\.edl)?\.json$", "", path_of(path))


def derived(path, suffix):
    return base_path(path) + suffix


def new(path, width=1920, height=1080, fps=25, name=None, profile=None, overwrite=False):
    if profile:
        key = str(profile).lower().replace("-", "").replace("_", "")
        if key not in DEFAULT_PROFILES:
            raise EdlError(f"unknown profile {profile!r}; known: {', '.join(sorted(DEFAULT_PROFILES))}")
        p = DEFAULT_PROFILES[key]
        width, height, fps = p["width"], p["height"], p["fps"]
    p = path_of(path)
    if os.path.exists(p) and not overwrite:
        raise EdlError(
            f"EDL already exists: {p}. Pass overwrite=true to replace it, "
            "or inspect it with project_show to keep editing.")
    doc = {
        "edl_version": EDL_VERSION,
        "name": name or os.path.splitext(os.path.basename(p.replace(".edl.json", "")))[0],
        "profile": {
            "width": int(width), "height": int(height), "fps": float(fps),
            "progressive": True, "sample_aspect": 1.0,
        },
        "tracks": [
            {"id": "V1", "type": "video", "name": "V1"},
            {"id": "A1", "type": "audio", "name": "A1"},
        ],
        "clips": [],
        "media": {},
    }
    save(doc, p)
    return doc


def load(path):
    p = path_of(path)
    if not os.path.exists(p):
        raise EdlError(f"EDL not found: {p}")
    try:
        with open(p, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EdlError(f"{p} is not valid JSON: {exc}")
    validate(doc)
    return doc


def save(doc, path=None):
    if not isinstance(doc, dict):
        raise EdlError("EDL must be an object")
    target = path or doc.get("path")
    if not target:
        raise EdlError("no path for EDL; pass a path")
    p = path_of(target)
    doc = dict(doc)
    doc.pop("path", None)
    validate(doc)
    payload = json.dumps(doc, indent=1, ensure_ascii=False, allow_nan=False) + "\n"
    # write via a temp file so a crash never truncates the project
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".", prefix=".edl-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def _positive(value, name, integer=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EdlError(f"{name} must be a number")
    if not math.isfinite(number) or number <= 0:
        raise EdlError(f"{name} must be a finite number > 0")
    return int(round(number)) if integer else number


def validate(doc):
    if not isinstance(doc, dict):
        raise EdlError("EDL must be an object")
    if "profile" not in doc or "tracks" not in doc:
        raise EdlError("EDL requires 'profile' and 'tracks'")
    prof = doc["profile"]
    if not isinstance(prof, dict):
        raise EdlError("profile must be an object")
    _positive(prof.get("fps"), "profile.fps")
    _positive(prof.get("width"), "profile.width", integer=True)
    _positive(prof.get("height"), "profile.height", integer=True)
    if not isinstance(doc["tracks"], list):
        raise EdlError("tracks must be a list")
    ids = set()
    kinds = {}
    for t in doc["tracks"]:
        if not isinstance(t, dict) or t.get("id") in (None, ""):
            raise EdlError("each track needs an 'id'")
        if t["id"] in ids:
            raise EdlError(f"duplicate track id {t['id']}")
        if t.get("type") not in ("video", "audio"):
            raise EdlError(f"track {t['id']}: type must be video or audio")
        ids.add(t["id"])
        kinds[t["id"]] = t.get("type")
    fps_ = float(prof.get("fps") or 25)
    clips = doc.get("clips", [])
    if not isinstance(clips, list):
        raise EdlError("clips must be a list")
    clip_ids = set()
    spans = {}
    for c in clips:
        if not isinstance(c, dict):
            raise EdlError("each clip must be an object")
        cid = c.get("id")
        if not cid:
            raise EdlError("clip is missing an id")
        if cid in clip_ids:
            raise EdlError(
                f"duplicate clip id {cid!r}; ids must be unique because the XML "
                "compiler keys producers by clip id")
        clip_ids.add(cid)
        if c.get("track") not in ids:
            raise EdlError(f"clip {cid}: unknown track {c.get('track')}")
        kind = c.get("kind")
        if kind not in ("media", "color", "title"):
            raise EdlError(f"clip {cid}: kind must be media, color or title")
        if kind in ("color", "title") and kinds.get(c.get("track")) == "audio":
            raise EdlError(f"clip {cid}: {kind} clips cannot be on an audio track")
        for key in ("start", "duration", "in", "out", "gain", "fade_in", "fade_out"):
            if key in c and c[key] is not None:
                try:
                    number = float(c[key])
                except (TypeError, ValueError):
                    raise EdlError(f"clip {cid}: {key} must be a number")
                if not math.isfinite(number):
                    raise EdlError(f"clip {cid}: {key} must be finite")
        start = 0.0 if c.get("start") is None else float(c["start"])
        if start < 0:
            raise EdlError(f"clip {cid}: start must be >= 0")
        gain = 1.0 if c.get("gain") is None else float(c["gain"])
        if gain < 0:
            raise EdlError(f"clip {cid}: gain must be >= 0")
        for key in ("fade_in", "fade_out"):
            if c.get(key) is not None and float(c[key]) < 0:
                raise EdlError(f"clip {cid}: {key} must be >= 0")
        if kind == "media":
            in_ = 0.0 if c.get("in") is None else float(c["in"])
            if in_ < 0:
                raise EdlError(f"clip {cid}: in must be >= 0")
            if c.get("out") is not None:
                if float(c["out"]) <= in_:
                    raise EdlError(f"clip {cid}: out must be greater than in")
            elif c.get("duration") is None:
                raise EdlError(f"clip {cid}: media clip needs 'out' or 'duration'")
            if not c.get("media"):
                raise EdlError(f"clip {cid}: media clip needs a 'media' path")
        dur = duration_of(doc, c)
        if _too_short(dur, fps_):
            raise EdlError(f"clip {cid}: duration must be at least one frame")
        spans.setdefault(c.get("track"), []).append((start, start + dur, cid))
    for track_id, entries in spans.items():
        entries.sort()
        prev = None
        for start, end, cid in entries:
            if prev is not None and start < prev[1] - OVERLAP_EPS:
                raise EdlError(
                    f"overlapping clips on {track_id}: {prev[2]} ends at {prev[1]:.3f}s "
                    f"but {cid} starts at {start:.3f}s; tracks are linear")
            if prev is None or end > prev[1]:
                prev = (start, end, cid)
    return True


# ------------------------------------------------------------------- helpers

def fps(doc):
    return float(doc["profile"].get("fps", 25))


def duration_of(doc, clip):
    if clip.get("duration") is not None:
        return float(clip["duration"])
    src = clip.get("out")
    if src is not None and clip.get("in") is not None:
        return float(src) - float(clip["in"])
    return float(clip.get("in", 0.0) or 0.0)


def clip_end(clip):
    return float(clip.get("start", 0.0)) + duration_of(None, clip)


def timeline_duration(doc):
    end = 0.0
    for c in doc.get("clips", []):
        end = max(end, clip_end(c))
    return end


def track(doc, track_id):
    for t in doc["tracks"]:
        if t["id"] == track_id:
            return t
    raise EdlError(f"unknown track {track_id!r}; tracks: {', '.join(t['id'] for t in doc['tracks'])}")


def clips_on(doc, track_id):
    return sorted([c for c in doc.get("clips", []) if c["track"] == track_id],
                  key=lambda c: float(c.get("start", 0.0)))


def find(doc, clip_id):
    for c in doc.get("clips", []):
        if c["id"] == clip_id:
            return c
    raise EdlError(f"no clip with id {clip_id!r}")


def new_id(prefix="c"):
    return f"{prefix}{uuid.uuid4().hex[:10]}"


# ------------------------------------------------------------------ mutations

def add_track(doc, kind, name=None, index=None):
    if kind not in ("video", "audio"):
        raise EdlError("track type must be video or audio")
    same = [t for t in doc["tracks"] if t["type"] == kind]
    prefix = "V" if kind == "video" else "A"
    if not name:
        n = len(same) + 1
        while any(t["id"] == f"{prefix}{n}" for t in doc["tracks"]):
            n += 1
        name = f"{prefix}{n}"
    tid = name
    if any(t["id"] == tid for t in doc["tracks"]):
        raise EdlError(f"track {tid} already exists")
    entry = {"id": tid, "type": kind, "name": tid}
    if index is None:
        doc["tracks"].append(entry)
    else:
        doc["tracks"].insert(int(index), entry)
    return tid


def add_clip(doc, track_id, kind="media", media_path=None, start=0.0, in_=None, out=None,
             duration=None, color=None, title=None, gain=1.0, fade_in=0.0, fade_out=0.0,
             label=None, effects=None):
    t = track(doc, track_id)
    f = fps(doc)
    if kind not in ("media", "color", "title"):
        raise EdlError(f"unknown kind {kind!r}")
    if kind in ("color", "title") and t["type"] == "audio":
        raise EdlError(f"{kind} clips cannot be placed on audio track {track_id}")
    start = 0.0 if start is None else _time(start, "start", f)
    gain = _number(1.0 if gain is None else gain, "gain", minimum=0.0)
    fade_in = 0.0 if fade_in in (None, "") else _time(fade_in, "fade_in", f)
    fade_out = 0.0 if fade_out in (None, "") else _time(fade_out, "fade_out", f)
    clip = {
        "id": new_id(),
        "track": track_id,
        "kind": kind,
        "start": round(start, 6),
        "gain": gain,
        "effects": list(effects or []),
    }
    if label:
        clip["label"] = label
    if kind == "media":
        if not media_path:
            raise EdlError("kind=media needs media_path")
        p = os.path.abspath(os.path.expanduser(str(media_path)))
        if not os.path.exists(p):
            raise EdlError(f"media not found: {p}")
        info = doc.setdefault("media", {}).get(p)
        if not info:
            info = media.probe(p)
            doc["media"][p] = info
        if not info.get("has_video") and not info.get("has_audio"):
            raise EdlError(f"unsupported media (no audio/video stream): {p}")
        if t["type"] == "audio" and not info.get("has_audio"):
            raise EdlError(f"{os.path.basename(p)} has no audio stream; cannot place on audio track {track_id}")
        clip["media"] = p
        in_s = 0.0 if in_ is None else _time(in_, "in", f)
        clip["in"] = round(in_s, 6)
        if duration is not None:
            dur = _time(duration, "duration", f)
        elif out is not None:
            out_s = _time(out, "out", f)
            if out_s <= in_s:
                raise EdlError("out must be greater than in")
            dur = out_s - in_s
        else:
            dur = _snap(float(info.get("duration") or 0.0) - in_s, f)
        if _too_short(dur, f):
            raise EdlError("clip duration must be at least one frame")
        clip["duration"] = round(float(dur), 6)
        clip["out"] = round(in_s + float(dur), 6)
    elif kind == "color":
        clip["color"] = media.normalize_color(color or "black")
        if duration is None:
            raise EdlError("kind=color needs duration")
        dur = _time(duration, "duration", f)
        if _too_short(dur, f):
            raise EdlError("clip duration must be at least one frame")
        clip["duration"] = round(float(dur), 6)
    elif kind == "title":
        if not title:
            raise EdlError("kind=title needs title={...}")
        clip["title"] = dict(title)
        if duration is None:
            duration = clip["title"].pop("duration", 5.0)
        dur = _time(duration, "duration", f)
        if _too_short(dur, f):
            raise EdlError("clip duration must be at least one frame")
        clip["duration"] = round(float(dur), 6)
        clip["title"]["duration"] = clip["duration"]
    else:  # pragma: no cover - checked above
        raise EdlError(f"unknown kind {kind!r}")
    dur = duration_of(doc, clip)
    if _too_short(dur, f):
        raise EdlError("clip duration must be at least one frame")
    # fades are non-negative and can never exceed the clip itself
    fade_in = min(fade_in, dur)
    fade_out = min(fade_out, dur)
    if fade_in > 0:
        clip["fade_in"] = round(float(fade_in), 6)
    if fade_out > 0:
        clip["fade_out"] = round(float(fade_out), 6)
    _place(doc, clip)
    doc["clips"].append(clip)
    return clip


def _place(doc, clip):
    """Reject overlaps on the same track (audio/video tracks are linear)."""
    start, end = clip["start"], clip_end(clip)
    for other in clips_on(doc, clip["track"]):
        if other is clip or other["id"] == clip.get("id"):
            continue
        o_start, o_end = float(other["start"]), clip_end(other)
        if start < o_end - OVERLAP_EPS and o_start < end - OVERLAP_EPS:
            raise EdlError(
                f"overlap on {clip['track']}: clip {other['id']} occupies "
                f"{o_start:.2f}-{o_end:.2f}s, new clip wants {start:.2f}-{end:.2f}s. "
                "Move or trim it first (clip_update), or use append=True.")


def update(doc, clip_id, **fields):
    c = find(doc, clip_id)
    f = fps(doc)
    if "track" in fields and fields["track"] is not None:
        target = track(doc, fields["track"])
        if target["type"] == "audio" and c["kind"] in ("color", "title"):
            raise EdlError(f"{c['kind']} clips cannot be moved to audio track {target['id']}")
        if target["type"] == "audio" and c["kind"] == "media":
            info = doc.get("media", {}).get(c.get("media"), {})
            if info and not info.get("has_audio"):
                raise EdlError(f"clip {c['id']} has no audio; cannot move it to {target['id']}")
        c["track"] = fields["track"]
    if fields.get("start") is not None:
        c["start"] = round(_time(fields["start"], "start", f), 6)
    if fields.get("duration") is not None:
        dur = _time(fields["duration"], "duration", f)
        if _too_short(dur, f):
            raise EdlError("duration must be at least one frame")
        c["duration"] = round(float(dur), 6)
        if c["kind"] == "title":
            c.setdefault("title", {})["duration"] = c["duration"]
        if c["kind"] == "media":
            c["out"] = round(float(c.get("in", 0.0)) + c["duration"], 6)
    if fields.get("in") is not None:
        if c["kind"] != "media":
            raise EdlError(f"clip {c['id']} is a {c['kind']} clip; 'in' only applies to media")
        c["in"] = round(_time(fields["in"], "in", f), 6)
        c["out"] = round(float(c["in"]) + duration_of(doc, c), 6)
    if fields.get("out") is not None:
        if c["kind"] != "media":
            raise EdlError(f"clip {c['id']} is a {c['kind']} clip; 'out' only applies to media")
        c["out"] = round(_time(fields["out"], "out", f), 6)
        if c["out"] <= float(c.get("in") or 0.0):
            raise EdlError("out must be greater than in")
        c["duration"] = round(float(c["out"]) - float(c.get("in") or 0.0), 6)
    if fields.get("gain") is not None:
        c["gain"] = _number(fields["gain"], "gain", minimum=0.0)
    dur_now = duration_of(doc, c)
    for key in ("fade_in", "fade_out"):
        if key in fields and fields[key] is not None:
            if fields[key] == "":
                c.pop(key, None)
                continue
            v = min(_time(fields[key], key, f), dur_now)
            if v > 0:
                c[key] = round(float(v), 6)
            else:
                c.pop(key, None)
    if "label" in fields and fields["label"] is not None:
        c["label"] = fields["label"]
    if "title" in fields and fields["title"] is not None:
        if c["kind"] != "title":
            raise EdlError(f"clip {c['id']} is a {c['kind']} clip; 'title' only applies to titles")
        if fields["title"]:
            c.setdefault("title", {}).update(fields["title"])
    _place(doc, c)
    return c


def remove(doc, clip_id):
    c = find(doc, clip_id)
    doc["clips"] = [x for x in doc["clips"] if x["id"] != clip_id]
    return c


def split(doc, clip_id, at):
    """Split one clip at an absolute timeline position."""
    c = find(doc, clip_id)
    f = fps(doc)
    at = round(_time(at, "at", f), 6)
    start, end = float(c["start"]), clip_end(c)
    if not (start < at < end):
        raise EdlError(f"split point {at:.3f}s is outside clip {clip_id} ({start:.2f}-{end:.2f}s)")
    left_dur = at - start
    right_dur = end - at
    if _too_short(left_dur, f) or _too_short(right_dur, f):
        raise EdlError("both halves must be at least one frame long")
    right = json.loads(json.dumps(c))
    right["id"] = new_id()
    right["start"] = round(at, 6)
    right["duration"] = round(right_dur, 6)
    if c["kind"] == "media":
        right["in"] = round(float(c["in"]) + left_dur, 6)
        right["out"] = round(float(right["in"]) + float(right["duration"]), 6)
    if c["kind"] == "title":
        right["title"] = dict(c.get("title") or {})
        right["title"]["duration"] = right["duration"]
    c["duration"] = round(left_dur, 6)
    if c["kind"] == "media":
        c["out"] = round(float(c["in"]) + float(c["duration"]), 6)
    right.pop("fade_in", None)
    c.pop("fade_out", None)
    doc["clips"].append(right)
    return c, right


def append(doc, track_id, kind="media", **kwargs):
    """Place a clip right after the last clip on the track (start is ignored)."""
    existing = clips_on(doc, track_id)
    kwargs.pop("start", None)
    start = max([clip_end(c) for c in existing], default=0.0)
    return add_clip(doc, track_id, kind=kind, start=_snap_up(start, fps(doc)), **kwargs)


PROP_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def add_effect(doc, clip_id, service, props=None):
    c = find(doc, clip_id)
    if not service:
        raise EdlError("effect needs a service name, e.g. brightness, volume, frei0r.cartoon")
    if not PROP_NAME_RE.match(str(service)):
        raise EdlError(f"invalid effect service name: {service!r}")
    for key in (props or {}):
        if not PROP_NAME_RE.match(str(key)):
            raise EdlError(f"invalid effect property name: {key!r}")
    eff = {"service": service, "props": dict(props or {})}
    c.setdefault("effects", []).append(eff)
    return eff


def remove_effects(doc, clip_id, service=None, index=None):
    c = find(doc, clip_id)
    effects = c.setdefault("effects", [])
    if index is not None:
        try:
            i = int(index)
        except (TypeError, ValueError):
            raise EdlError(f"effect index must be an integer, got {index!r}")
        if i < 0 or i >= len(effects):
            raise EdlError(
                f"effect index {i} out of range: clip {c['id']} has {len(effects)} effect(s) "
                f"(0-based)")
        effects.pop(i)
        return 1
    before = len(effects)
    if service:
        c["effects"] = [e for e in effects if e.get("service") != service]
    else:
        c["effects"] = []
    return before - len(c["effects"])


# ------------------------------------------------------------------- summary

def _short_text(value, limit):
    text = " ".join(str(value).split())
    if len(text) > limit:
        text = text[:limit - 3] + "..."
    return text


def _effect_summary(eff, limit=40):
    service = str(eff.get("service") or "?")
    props = eff.get("props") or {}
    text = service
    if props:
        text = f"{service}{{{', '.join(f'{k}={v}' for k, v in props.items())}}}"
    return _short_text(text, limit)


def summarize(doc):
    f = fps(doc)
    total = timeline_duration(doc)
    lines = [
        f"project: {doc.get('name', '?')}  ({doc['profile']['width']}x{doc['profile']['height']} @ {f:g} fps)",
        f"duration: {total:.2f}s ({int(round(total * f))} frames)",
        "tracks (bottom to top):",
    ]
    for t in doc["tracks"]:
        clips = clips_on(doc, t["id"])
        lines.append(f"  {t['id']} [{t['type']}] {len(clips)} clip(s)")
        for c in clips:
            if c.get("label"):
                label = c["label"]
            elif c.get("kind") == "media":
                label = os.path.basename(c.get("media", ""))
            elif c.get("kind") == "title":
                text = (c.get("title") or {}).get("text") or ""
                label = "title: " + _short_text(text, 30) if str(text).strip() else "title"
            else:
                label = c["kind"]
            extra = []
            if c.get("kind") == "media":
                extra.append(f"src {c['in']:.2f}->{c['out']:.2f}")
            if c.get("fade_in"):
                extra.append(f"fade_in {c['fade_in']:.2f}s")
            if c.get("fade_out"):
                extra.append(f"fade_out {c['fade_out']:.2f}s")
            if float(c.get("gain", 1.0)) != 1.0:
                extra.append(f"gain {c['gain']:g}")
            if c.get("effects"):
                extra.append("fx: " + ",".join(_effect_summary(e) for e in c["effects"]))
            lines.append(f"    {c['start']:>8.2f}s +{duration_of(doc, c):.2f}s  {c['id']:<8} {label}  {' '.join(extra)}".rstrip())
    return "\n".join(lines)


def as_dict(clip):
    return {k: v for k, v in clip.items()}
