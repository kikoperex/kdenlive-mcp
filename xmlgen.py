# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Ángel Pérez
"""Compile an EDL into a Kdenlive (MLT XML) project.

Layout produced:

    <mlt producer="main_bin">
      <profile/>
      <chain id="chain0">            <- one per unique media file (the bin)
      <producer id="producerX">      <- one per timeline clip (filters live here)
      <playlist id="main_bin">       <- document properties + bin entries
      <producer id="black_track"/>
      <playlist id="playlist0..n">   <- one per track (V1..Vn then An..A1)
      <tractor id="{uuid}">          <- the sequence: tracks + internal transitions
    </mlt>

Facts this file relies on (verified against Kdenlive 26.08 / MLT 7.42):
  * the document uuid lives in main_bin as `kdenlive:docproperties.uuid`, and the
    sequence tractor's `kdenlive:sequenceproperties.documentuuid` must match it,
    otherwise Kdenlive treats it as a foreign sequence;
  * video tracks are numbered V1..Vn in tractor order, audio tracks get their
    number reversed (the last audio track in the tractor is A1), so tracks are
    emitted V1..Vn, An..A1;
  * later tractor tracks composite over earlier ones, hence black_track first;
  * video tracks are `hide="audio"`, audio tracks `hide="video"`;
  * video compositing uses an `always_active` qtblend transition, audio mixing
    uses an `always_active` mix transition, both with `internal_added` so
    Kdenlive does not show them as user-added compositions.
"""

import math
import os
import uuid as uuidlib
import xml.sax.saxutils as sx

import edl as edlmod
import media

DOC_VERSION = "1.1"
DOC_PATCH = "1"
KDENLIVE_VERSION = "26.08.3"
INTERNAL_ADDED = "237"


def _db(gain):
    gain = float(gain)
    return 20.0 * math.log10(gain) if gain > 0 else -100.0


_CTRL = dict.fromkeys(list(range(0x00, 0x09)) + [0x0b, 0x0c] + list(range(0x0e, 0x20)))


def esc(value):
    """Escape text for XML and drop characters XML forbids."""
    text = str(value).translate(_CTRL)
    return sx.escape(text, {'"': "&quot;"})


_NAME_RE = None


def esc_name(name):
    import re
    global _NAME_RE
    if _NAME_RE is None:
        _NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
    if not _NAME_RE.match(str(name)):
        raise ValueError(f"invalid XML property name: {name!r}")
    return str(name)


def _prop(name, value, indent="  "):
    name = esc_name(name)
    if value is None:
        return f'{indent}<property name="{name}"/>\n'
    return f'{indent}<property name="{name}">{esc(value)}</property>\n'


def profile_element(profile):
    w, h = int(profile["width"]), int(profile["height"])
    fps = float(profile.get("fps", 25))
    num, den = _frame_rate(fps)
    from math import gcd
    g = gcd(w, h)
    dar_n, dar_den = w // g, h // g
    progressive = 1 if profile.get("progressive", True) else 0
    desc = f"{w}x{h} {fps:g} fps"
    return (
        f' <profile description="{esc(desc)}" width="{w}" height="{h}" '
        f'progressive="{progressive}" sample_aspect_num="1" sample_aspect_den="1" '
        f'display_aspect_num="{dar_n}" display_aspect_den="{dar_den}" '
        f'frame_rate_num="{num}" frame_rate_den="{den}" colorspace="709"/>\n'
    )


def _frame_rate(fps):
    known = {(24, 1): 24, (25, 1): 25, (30, 1): 30, (60, 1): 60, (50, 1): 50}
    for (n, d), v in known.items():
        if abs(v - fps) < 1e-9:
            return n, d
    for n, d in ((24000, 1001), (30000, 1001), (60000, 1001), (15000, 1001)):
        if abs(n / d - fps) < 1e-6:
            return n, d
    from fractions import Fraction
    fr = Fraction(fps).limit_denominator(1001)
    return fr.numerator, fr.denominator


def _clip_type(info):
    has_v, has_a = info.get("has_video"), info.get("has_audio")
    if info.get("is_image"):
        return 5  # ClipType::Image
    if has_v and has_a:
        return 3  # AV
    if has_v:
        return 2  # Video
    return 1      # Audio


class Builder:
    def __init__(self, doc):
        self.doc = doc
        self.fps = edlmod.fps(doc)
        self.out = []
        self.ids = 0
        self.doc_uuid = "{" + str(uuidlib.uuid4()) + "}"
        self.bin_media = {}     # media path -> (chain id, bin id)
        self.bin_ids = {}       # media path -> kdenlive bin id
        self.bin_extra = []     # generated (color/title) bin items
        self.clip_bin = {}      # clip id -> kdenlive bin id
        self.next_bin_id = 1

    # ---------------------------------------------------------------- helpers
    def tid(self, prefix="producer"):
        self.ids += 1
        return f"{prefix}{self.ids}"

    def tc(self, seconds):
        return media.tc(seconds, self.fps)

    def bin_id(self, media_path):
        if media_path not in self.bin_ids:
            self.bin_ids[media_path] = self.new_bin_id()
        return self.bin_ids[media_path]

    def new_bin_id(self):
        bid = self.next_bin_id
        self.next_bin_id += 1
        return bid

    # ------------------------------------------------------------------ build
    def build(self):
        doc = self.doc
        o = self.out
        o.append("<?xml version='1.0' encoding='utf-8'?>\n")
        root_dir = os.path.dirname(os.path.abspath(doc.get("_path", ".")))
        # `producer` is what melt renders by default, so point it at the sequence
        # tractor (Kdenlive itself never reads this attribute, it looks main_bin up).
        o.append(f'<mlt LC_NUMERIC="C" producer="{self.doc_uuid}" version="7.42.0" root="{esc(root_dir)}">\n')
        o.append(profile_element(doc["profile"]))

        # bin sources: one chain per unique media file
        for path in self._used_media():
            self._bin_chain(path)

        # timeline clip producers
        clip_producers = {}
        for clip in doc.get("clips", []):
            clip_producers[clip["id"]] = self._clip_producer(clip)

        self._main_bin()
        self._black_track()

        tracks = self._ordered_tracks()
        playlist_ids = {}
        for t in tracks:
            playlist_ids[t["id"]] = self._track_playlist(t, clip_producers)
        self._sequence(tracks, playlist_ids)
        o.append("</mlt>\n")
        return "".join(o)

    def _ordered_tracks(self):
        videos = [t for t in self.doc["tracks"] if t["type"] == "video"]
        audios = [t for t in self.doc["tracks"] if t["type"] == "audio"]
        return videos + list(reversed(audios))

    def _used_media(self):
        seen, order = set(), []
        for c in self.doc.get("clips", []):
            if c.get("kind") == "media":
                p = c["media"]
                if p not in seen:
                    seen.add(p)
                    order.append(p)
        return order

    def _media_info(self, path):
        info = self.doc.get("media", {}).get(path)
        if not info:
            info = media.probe(path)
            self.doc.setdefault("media", {})[path] = info
        return info

    def _bin_chain(self, path):
        info = self._media_info(path)
        chain_id = self.tid("chain")
        bid = self.bin_id(path)
        dur = float(info.get("duration") or 10.0)
        service = "qimage" if info.get("is_image") else "avformat-novalidate"
        self.out.append(f' <chain id="{chain_id}" out="{self.tc(dur)}">\n')
        self.out.append(_prop("length", self.tc(dur)))
        self.out.append(_prop("eof", "pause"))
        self.out.append(_prop("resource", path))
        self.out.append(_prop("mlt_service", service))
        if not info.get("is_image"):
            self.out.append(_prop("seekable", "1"))
            self.out.append(_prop("audio_index", "1" if info.get("has_audio") else "-1"))
            self.out.append(_prop("video_index", "0" if info.get("has_video") else "-1"))
        self._bin_props(path, info, bid)
        self.out.append(" </chain>\n")
        self.bin_media[path] = (chain_id, bid)
        return chain_id

    def _bin_props(self, path, info, bid, indent="  "):
        self.out.append(_prop("kdenlive:clipname", os.path.basename(path), indent))
        self.out.append(_prop("kdenlive:folderid", "-1", indent))
        self.out.append(_prop("kdenlive:id", str(bid), indent))
        self.out.append(_prop("kdenlive:clip_type", str(_clip_type(info)), indent))
        if os.path.exists(path):
            self.out.append(_prop("kdenlive:file_hash", media.file_hash(path), indent))
        if info.get("duration"):
            self.out.append(_prop("kdenlive:duration", self.tc(float(info["duration"])), indent))
        self.out.append(_prop("xml_retain", "1", indent))

    def _clip_producer(self, clip):
        kind = clip["kind"]
        if kind == "media":
            self.clip_bin[clip["id"]] = self.bin_id(clip["media"])
            return self._media_clip_producer(clip)
        # color/title clips are generated sources: they get their own bin entry so
        # Kdenlive can map the timeline clip back to a bin item
        bid = self.new_bin_id()
        self.clip_bin[clip["id"]] = bid
        producer = self._color_producer(clip, bid) if kind == "color" else self._title_producer(clip, bid)
        self.bin_extra.append({"id": bid, "producer": producer,
                               "duration": float(clip["duration"])})
        return producer

    def _media_clip_producer(self, clip):
        path = clip["media"]
        info = self._media_info(path)
        bid = self.bin_id(path)
        pid = self.tid("producer")
        out_tc = self.tc(float(clip["in"]) + float(clip["duration"]))
        self.out.append(f' <producer id="{pid}" in="{self.tc(0)}" out="{out_tc}">\n')
        self.out.append(_prop("length", out_tc))
        self.out.append(_prop("eof", "pause"))
        self.out.append(_prop("resource", path))
        self.out.append(_prop("mlt_service", "qimage" if info.get("is_image") else "avformat-novalidate"))
        if info.get("is_image"):
            self.out.append(_prop("ttl", "1"))
        else:
            self.out.append(_prop("seekable", "1"))
            self.out.append(_prop("audio_index", "1" if info.get("has_audio") else "-1"))
            self.out.append(_prop("video_index", "0" if info.get("has_video") else "-1"))
        self._bin_props(path, info, bid)
        if info.get("is_image"):
            # images have no intrinsic duration: hold them
            self.out.append(_prop("kdenlive:duration", out_tc))
        self._clip_filters(clip, info)
        self.out.append(" </producer>\n")
        return pid

    def _color_producer(self, clip, bid):
        pid = self.tid("producer")
        dur = float(clip["duration"])
        self.out.append(f' <producer id="{pid}" in="{self.tc(0)}" out="{self.tc(dur)}">\n')
        self.out.append(_prop("length", self.tc(dur)))
        self.out.append(_prop("eof", "pause"))
        self.out.append(_prop("resource", clip["color"]))
        self.out.append(_prop("mlt_service", "color"))
        self.out.append(_prop("mlt_image_format", "rgba"))
        self.out.append(_prop("set.test_audio", "0"))
        self.out.append(_prop("kdenlive:clipname", "Color Clip"))
        self.out.append(_prop("kdenlive:folderid", "-1"))
        self.out.append(_prop("kdenlive:id", str(bid)))
        self.out.append(_prop("kdenlive:clip_type", "2"))
        self.out.append(_prop("kdenlive:duration", self.tc(dur)))
        self._clip_filters(clip, {"has_video": True, "has_audio": False})
        self.out.append(" </producer>\n")
        return pid

    def _title_producer(self, clip, bid):
        pid = self.tid("producer")
        t = clip.get("title") or {}
        dur = float(clip["duration"])
        frames = media.frames(dur, self.fps)
        style = t.get("style") or {}
        text = t.get("text", "")
        font = style.get("font", "DejaVu Sans")
        size = int(style.get("size", max(24, int(self.doc["profile"]["height"] / 14))))
        fg = _rgba(style.get("color", "white"))
        outline = int(style.get("outline", 0))
        ocolor = _rgba(style.get("outline_color", "black"))
        x = style.get("x")
        y = style.get("y")
        w = int(self.doc["profile"]["width"])
        h = int(self.doc["profile"]["height"])
        if x is None:
            x = int(w * 0.08)
        if y is None:
            y = int(h * 0.78)
        weight = int(style.get("weight", 700))
        xml_data = (
            '<?xml version="1.0"?>\n<kdenlivetitle width="%d" height="%d" out="%d" LC_NUMERIC="C">\n'
            ' <item type="QGraphicsTextItem" z-index="0">\n'
            '  <position x="%d" y="%d">\n   <transform>1,0,0,0,1,0,0,0,1</transform>\n  </position>\n'
            '  <content font="%s" font-size="%d" font-weight="%d" font-color="%s" '
            'font-outline="%d" font-outline-color="%s" font-precision="1" '
            'letter-spacing="0" line-spacing="0">%s</content>\n'
            '  <start>0</start>\n  <end>%d</end>\n </item>\n</kdenlivetitle>\n'
        ) % (w, h, frames - 1, int(x), int(y), esc(font), size, weight, fg,
             outline, ocolor, sx.escape(text), frames - 1)
        self.out.append(f' <producer id="{pid}" in="{self.tc(0)}" out="{self.tc(dur)}">\n')
        self.out.append(_prop("length", self.tc(dur)))
        self.out.append(_prop("eof", "pause"))
        # An empty resource makes the kdenlivetitle module read `xmldata` instead
        # of trying to load a .kdenlivetitle file.
        self.out.append(_prop("resource", ""))
        self.out.append(_prop("mlt_service", "kdenlivetitle"))
        self.out.append(_prop("mlt_image_format", "rgba"))
        self.out.append(_prop("kdenlive:clipname", text[:40] or "Title"))
        self.out.append(_prop("kdenlive:folderid", "-1"))
        self.out.append(_prop("kdenlive:id", str(bid)))
        self.out.append(_prop("kdenlive:clip_type", "6"))
        self.out.append(_prop("kdenlive:duration", self.tc(dur)))
        self.out.append(f'  <property name="xmldata">{esc(xml_data)}</property>\n')
        self._clip_filters(clip, {"has_video": True, "has_audio": False})
        self.out.append(" </producer>\n")
        return pid

    def _clip_filters(self, clip, info):
        fps = self.fps
        dur = float(clip["duration"])
        fade_in = float(clip.get("fade_in") or 0)
        fade_out = float(clip.get("fade_out") or 0)
        # MLT's brightness `level` is a 0..1 multiplier (1 = untouched frame).
        if fade_in and (info.get("has_video") or clip["kind"] != "media"):
            self._brightness_fade([(0, 0), (max(1, media.frames(fade_in, fps)), 1)])
        if fade_out and (info.get("has_video") or clip["kind"] != "media"):
            start = max(0.0, dur - fade_out)
            self._brightness_fade([(media.frames(start, fps), 1),
                                   (max(1, media.frames(dur, fps) - 1), 0)])
        if info.get("has_audio") and clip["kind"] == "media":
            gain = float(clip.get("gain", 1.0))
            if fade_in or fade_out or abs(gain - 1.0) > 1e-6:
                self._volume_filter(clip, dur, fade_in, fade_out, gain)
        for eff in clip.get("effects") or []:
            self.out.append('  <filter>\n')
            for k, v in (eff.get("props") or {}).items():
                self.out.append(_prop(k, v, "   "))
            self.out.append(_prop("mlt_service", eff["service"], "   "))
            self.out.append("  </filter>\n")

    def _brightness_fade(self, keyframes):
        kf = ";".join(f"{f}={v}" for f, v in keyframes)
        self.out.append("  <filter>\n")
        self.out.append(_prop("level", kf, "   "))
        self.out.append(_prop("alpha", "1", "   "))
        self.out.append(_prop("mlt_service", "brightness", "   "))
        self.out.append("  </filter>\n")

    def _volume_filter(self, clip, dur, fade_in, fade_out, gain):
        """Kdenlive's Volume effect stores `level` in dB (0 dB = unity gain)."""
        fps = self.fps
        db = _db(gain)
        last = max(1, media.frames(dur, fps) - 1)
        if not fade_in and not fade_out:
            self.out.append("  <filter>\n")
            self.out.append(_prop("level", f"{db:.2f}", "   "))
            self.out.append(_prop("mlt_service", "volume", "   "))
            self.out.append("  </filter>\n")
            return
        points = [(0, -100.0 if fade_in else db)]
        if fade_in:
            points.append((media.frames(fade_in, fps), db))
        if fade_out:
            points.append((media.frames(max(0.0, dur - fade_out), fps), db))
            points.append((last, -100.0))
        elif points[-1][0] != last:
            points.append((last, points[-1][1]))
        kf = ";".join(f"{f}={v:.2f}" for f, v in points)
        self.out.append("  <filter>\n")
        self.out.append(_prop("level", kf, "   "))
        self.out.append(_prop("mlt_service", "volume", "   "))
        self.out.append("  </filter>\n")

    def _main_bin(self):
        doc = self.doc
        o = self.out
        o.append(' <playlist id="main_bin">\n')
        o.append(_prop("kdenlive:docproperties.uuid", self.doc_uuid))
        o.append(_prop("kdenlive:docproperties.version", DOC_VERSION))
        o.append(_prop("kdenlive:docproperties.patchversion", DOC_PATCH))
        o.append(_prop("kdenlive:docproperties.kdenliveversion", KDENLIVE_VERSION))
        o.append(_prop("kdenlive:docproperties.documentid", str(int(os.path.getmtime(doc["_path"]) * 1000)) if os.path.exists(doc.get("_path", "")) else "1"))
        o.append(_prop("kdenlive:docproperties.profile", self._profile_name()))
        o.append(_prop("kdenlive:docproperties.audioChannels", "2"))
        o.append(_prop("kdenlive:docproperties.activeTrack", "1"))
        o.append(_prop("kdenlive:docproperties.audioTarget", _first_audio_index(self)))
        o.append(_prop("kdenlive:docproperties.videoTarget", "1"))
        o.append(_prop("kdenlive:docproperties.position", "0"))
        o.append(_prop("kdenlive:docproperties.zoom", "8"))
        o.append(_prop("kdenlive:docproperties.verticalzoom", "1"))
        o.append(_prop("kdenlive:docproperties.scrollPos", "0"))
        o.append(_prop("kdenlive:docproperties.zonein", "0"))
        o.append(_prop("kdenlive:docproperties.enableproxy", "0"))
        o.append(_prop("kdenlive:docproperties.generateproxy", "0"))
        o.append(_prop("kdenlive:docproperties.generateimageproxy", "0"))
        o.append(_prop("kdenlive:docproperties.enableTimelineZone", "0"))
        o.append(_prop("kdenlive:docproperties.disablepreview", "0"))
        o.append(_prop("kdenlive:docproperties.expandedFolders", ""))
        o.append(_prop("kdenlive:expandedFolders", ""))
        o.append(_prop("kdenlive:documentnotes", ""))
        o.append(_prop("xml_retain", "1"))
        for path, (chain_id, bid) in self.bin_media.items():
            dur = float(self._media_info(path).get("duration") or 10.0)
            o.append(f'  <entry producer="{chain_id}" in="{self.tc(0)}" out="{self.tc(dur)}">\n')
            o.append(_prop("kdenlive:id", str(bid), "   "))
            o.append("  </entry>\n")
        for item in self.bin_extra:
            dur = item["duration"]
            o.append(f'  <entry producer="{item["producer"]}" in="{self.tc(0)}" out="{self.tc(dur)}">\n')
            o.append(_prop("kdenlive:id", str(item["id"]), "   "))
            o.append("  </entry>\n")
        o.append(" </playlist>\n")

    def _profile_name(self):
        w = int(self.doc["profile"]["width"])
        h = int(self.doc["profile"]["height"])
        fps = float(self.doc["profile"]["fps"])
        return f"{w}x{h} {fps:g}fps"

    def _black_track(self):
        # MLT derives a tractor's length from its longest track, so black_track is
        # pinned to the timeline content; Kdenlive extends it while editing.
        total = max(1.0, edlmod.timeline_duration(self.doc))
        self.out.append(f' <producer id="black_track" in="{self.tc(0)}" out="{self.tc(total)}">\n')
        self.out.append(_prop("length", "2147483647"))
        self.out.append(_prop("eof", "continue"))
        self.out.append(_prop("resource", "black"))
        self.out.append(_prop("aspect_ratio", "1"))
        self.out.append(_prop("mlt_service", "color"))
        self.out.append(_prop("kdenlive:playlistid", "black_track"))
        self.out.append(_prop("mlt_image_format", "rgba"))
        self.out.append(_prop("set.test_audio", "0"))
        self.out.append(" </producer>\n")

    def _track_playlist(self, track, clip_producers):
        pid = self.tid("playlist")
        clips = [c for c in edlmod.clips_on(self.doc, track["id"]) if c["id"] in clip_producers]
        self.out.append(f' <playlist id="{pid}">\n')
        if track["type"] == "audio":
            self.out.append(_prop("kdenlive:audio_track", "1"))
        self.out.append(_prop("kdenlive:track_name", track.get("name") or track["id"]))
        self.out.append(_prop("kdenlive:trackheight", "67"))
        self.out.append(_prop("kdenlive:timeline_active", "1"))
        self.out.append(_prop("kdenlive:collapsed", "0"))
        self.out.append(_prop("kdenlive:thumbs_format", ""))
        self.out.append(_prop("kdenlive:audio_rec", ""))
        cursor = 0.0
        for c in clips:
            start = float(c["start"])
            if start > cursor + 1e-6:
                self.out.append(f'  <blank length="{self.tc(start - cursor)}"/>\n')
            dur = float(c["duration"])
            producer = clip_producers[c["id"]]
            src_in = float(c.get("in", 0.0)) if c["kind"] == "media" else 0.0
            self.out.append(
                f'  <entry producer="{producer}" in="{self.tc(src_in)}" '
                f'out="{self.tc(src_in + dur - 1.0 / self.fps)}">\n')
            bid = self.clip_bin.get(c["id"])
            if bid is not None:
                self.out.append(_prop("kdenlive:id", str(bid), "   "))
            self.out.append("  </entry>\n")
            cursor = start + dur
        self.out.append(" </playlist>\n")
        return pid

    def _sequence(self, tracks, playlist_ids):
        doc = self.doc
        total = edlmod.timeline_duration(doc)
        o = self.out
        o.append(f' <tractor id="{self.doc_uuid}" in="{self.tc(0)}" out="{self.tc(max(total, 1.0) - 1.0 / self.fps)}">\n')
        o.append(_prop("kdenlive:uuid", self.doc_uuid))
        o.append(_prop("kdenlive:clipname", "Sequence 1"))
        o.append(_prop("kdenlive:producer_type", "17"))
        o.append(_prop("kdenlive:id", str(self.next_bin_id)))
        o.append(_prop("kdenlive:folderid", "-1"))
        o.append(_prop("kdenlive:clip_type", "0"))
        o.append(_prop("kdenlive:duration", self.tc(total)))
        o.append(_prop("kdenlive:maxduration", str(media.frames(total, self.fps))))
        o.append(_prop("kdenlive:sequenceproperties.hasAudio", "1" if any(t["type"] == "audio" for t in tracks) else "0"))
        o.append(_prop("kdenlive:sequenceproperties.hasVideo", "1" if any(t["type"] == "video" for t in tracks) else "0"))
        o.append(_prop("kdenlive:sequenceproperties.documentuuid", self.doc_uuid))
        o.append(_prop("kdenlive:sequenceproperties.tracksCount", str(len(tracks))))
        o.append(_prop("kdenlive:sequenceproperties.tracks", str(len(tracks))))
        o.append(_prop("kdenlive:sequenceproperties.activeTrack", "1"))
        o.append(_prop("kdenlive:sequenceproperties.audioTarget", _first_audio_index(self)))
        o.append(_prop("kdenlive:sequenceproperties.videoTarget", "1"))
        o.append(_prop("kdenlive:sequenceproperties.zoom", "8"))
        o.append(_prop("kdenlive:sequenceproperties.verticalzoom", "1"))
        o.append(_prop("kdenlive:sequenceproperties.scrollPos", "0"))
        o.append(_prop("kdenlive:sequenceproperties.position", "0"))
        o.append(_prop("kdenlive:sequenceproperties.zonein", "0"))
        o.append(_prop("kdenlive:sequenceproperties.zoneout", str(media.frames(total, self.fps))))
        o.append(_prop("kdenlive:sequenceproperties.disablepreview", "0"))
        o.append(_prop("kdenlive:sequenceproperties.groups", "[\n]"))
        o.append(_prop("kdenlive:sequenceproperties.guides", "[\n]"))
        o.append(_prop("kdenlive:markers", "[\n]"))
        o.append(_prop("kdenlive:monitorPosition", "0"))
        o.append('  <track producer="black_track"/>\n')
        index = {}
        for i, t in enumerate(tracks, start=1):
            index[t["id"]] = i
            hide = "audio" if t["type"] == "video" else "video"
            o.append(f'  <track hide="{hide}" producer="{playlist_ids[t["id"]]}"/>\n')
        ntrans = 0
        for t in tracks:
            i = index[t["id"]]
            if t["type"] == "video":
                o.append(f'  <transition id="transition{ntrans}">\n')
                o.append(_prop("a_track", "0", "   "))
                o.append(_prop("b_track", str(i), "   "))
                o.append(_prop("compositing", "0", "   "))
                o.append(_prop("distort", "0", "   "))
                o.append(_prop("rotate_center", "0", "   "))
                o.append(_prop("mlt_service", "qtblend", "   "))
                o.append(_prop("always_active", "1", "   "))
                o.append(_prop("internal_added", INTERNAL_ADDED, "   "))
                o.append("  </transition>\n")
            else:
                o.append(f'  <transition id="transition{ntrans}">\n')
                o.append(_prop("a_track", "0", "   "))
                o.append(_prop("b_track", str(i), "   "))
                o.append(_prop("mlt_service", "mix", "   "))
                o.append(_prop("kdenlive_id", "mix", "   "))
                o.append(_prop("always_active", "1", "   "))
                o.append(_prop("accepts_blanks", "1", "   "))
                o.append(_prop("sum", "1", "   "))
                o.append(_prop("internal_added", INTERNAL_ADDED, "   "))
                o.append("  </transition>\n")
            ntrans += 1
        o.append(" </tractor>\n")


def _first_audio_index(builder):
    """Tractor index of the first audio track (Kdenlive's audioTarget)."""
    tracks = builder._ordered_tracks()
    for i, t in enumerate(tracks, start=1):
        if t["type"] == "audio":
            return str(i)
    return "1"


def _rgba(color):
    c = media.normalize_color(color)      # 0xrrggbbgg
    hexpart = c[2:]
    r, g, b = int(hexpart[0:2], 16), int(hexpart[2:4], 16), int(hexpart[4:6], 16)
    return f"{r},{g},{b},255"


def compile_edl(doc, path=None):
    doc = dict(doc)
    doc["_path"] = os.path.abspath(path) if path else doc.get("_path", "project.kdenlive")
    return Builder(doc).build()


def write_project(doc, out_path):
    out_path = os.path.abspath(os.path.expanduser(out_path))
    xml = compile_edl(doc, out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    return out_path
