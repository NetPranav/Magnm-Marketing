#!/usr/bin/env python3
"""Deterministic video renderer: a reviewed script becomes finished 9:16 and 16:9 MP4s with voice-over and captions.

No AI model is called to make the picture or the sound. The pieces are:
  - scenes from the script (workflows._extract_shotlist_and_srt): on-screen text, voice-over line, visual note, timing;
  - the voice-over, spoken locally by Kokoro (voiceover.py), one clip per scene; a scene lasts as long as its voice
    clip plus a short pause, or its planned time if that is longer;
  - the pictures: a real screenshot from the repo's own media when the note asks for footage (never from a confidential
    repo), otherwise a branded layout in the card palette and fonts, drawn by Playwright;
  - ffmpeg for the slow push-in or pan, cross-fades, a short transition sound, loudness, burned-in captions (libass)
    that follow the real voice timing, and the checks afterwards (ffprobe).

Files land in outputs/video/<content_id>/ and are attached to the script's content item. One render runs at a time.
"""

import hashlib
import html as html_lib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import confidential_guard
import db
import events
import paths
import product_rules
import video_settings
import voiceover

FPS = 30
XFADE_S = 0.4
END_CARD_S = 2.5
VO_LEAD_S = 0.15
SCENE_PAD_S = 0.4
MIN_SCENE_S = 1.2
WORDS_PER_SECOND = 2.6
MAX_SCENES = 14
DURATION_TOLERANCE_S = 0.5
BLANK_SPREAD = 4.0
MAX_FLUX_IMAGES = 3
SITE = "magnm-org.vercel.app"
CAPTION_CHARS = 32
CAPTION_LINES = 2

ASPECTS = {
    "9:16": {"size": (1080, 1920), "file": "video-9x16.mp4", "tag": "9x16"},
    "16:9": {"size": (1920, 1080), "file": "video-16x9.mp4", "tag": "16x9"},
}
DEFAULT_ASPECTS = ["9:16", "16:9"]

# The card palette (templates/card.py, palette v3). Text on a field is always ink.
PALETTE = {
    "stone": "#EFEFEC", "ink": "#0A0A0A", "lime": "#C8F31D", "sky": "#BCE3FF",
    "mint": "#C7F5D9", "butter": "#FFE89E", "coral": "#FF9F80", "lilac": "#DCCBFF",
}
FIELDS = ["sky", "mint", "butter", "lilac", "coral", "lime"]
TRANSITIONS = ["fade", "smoothleft", "fade", "smoothright"]
MOTIONS = ["push", "pan_right", "pull", "pan_left"]
FONT_FILES = {"anton": "anton.ttf", "serif": "serif-italic.ttf", "mono": "mono.ttf", "sans": "sans.ttf"}
SFX_REL = ("video-skills", "magnm-video", "assets", "sfx", "impact", "impactSoft_heavy_003.ogg")
SFX_LEN_S = 0.5

# How a name is spoken (the TTS reads the written spelling badly). Captions keep the real spelling.
RESPELLINGS = {"Cero": "Sero", "Magnm": "Magnum"}

_FOOTAGE_RE = re.compile(
    r"\b(screenshots?|screen\s*(?:recording|capture|grab)s?|footage|demo|terminal|gif|b-?roll|ui|interface|dashboard|app\s+window)\b",
    re.IGNORECASE,
)
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(("the", "a", "an", "of", "and", "or", "on", "in", "to", "with", "for", "is", "it", "at", "scene"))

NO_BROWSER_MSG = (
    "The video renderer's browser is not installed. Open Settings > Setup check and install the browser, then try again."
)
NO_FFMPEG_MSG = "ffmpeg was not found on this computer, and it is needed to make the video. Install ffmpeg and try again."

_RENDER_LOCK = threading.Lock()
_STATUS = {}
_STATUS_LOCK = threading.Lock()


class RenderError(Exception):
    """A render problem in plain words. `code` tells the UI what to offer (no_voice shows the Download voice button)."""

    def __init__(self, message, code="render_failed"):
        super().__init__(message)
        self.message = str(message)
        self.code = code


class RenderCancelled(RenderError):
    """The person cancelled the render."""

    def __init__(self, message="The render was cancelled."):
        super().__init__(message, "cancelled")


class RenderBusy(RenderError):
    """Another video is already rendering."""

    def __init__(self):
        super().__init__("Another video is rendering right now. Try again when it has finished.", "busy")


# --- Text helpers -------------------------------------------------------------------------------------------------

def _clean_text(text):
    """A script line without markdown marks, quotes around it or runs of spaces."""
    out = str(text or "")
    out = re.sub(r"^#{1,6}\s*", "", out.strip())
    out = out.replace("**", "").replace("__", "").replace("`", "")
    out = out.strip().strip('"').strip("\u201c\u201d").strip()
    return " ".join(out.split())


def spoken_text(text):
    """The voice-over text with names respelled for the voice."""
    out = _clean_text(text)
    for written, spoken in RESPELLINGS.items():
        out = re.sub(rf"\b{re.escape(written)}\b", spoken, out, flags=re.IGNORECASE)
    return out


def estimate_speech_seconds(text):
    """How long a line takes to read aloud, for captions when there is no voice clip."""
    words = len(str(text or "").split())
    return round(words / WORDS_PER_SECOND, 3) if words else 0.0


def _wrap_words(words, max_chars):
    lines, cur = [], ""
    for word in words:
        if cur and len(cur) + 1 + len(word) > max_chars:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


def _wrap_phrase(words, max_chars):
    """Wrap to lines of about max_chars; two extra characters are allowed to avoid an extra, nearly empty line."""
    best = _wrap_words(words, max_chars)
    for limit in (max_chars + 1, max_chars + 2):
        wider = _wrap_words(words, limit)
        if len(wider) < len(best):
            best = wider
    return best


def split_caption_lines(text, max_chars=CAPTION_CHARS, max_lines=CAPTION_LINES):
    """Split a spoken line into captions of at most `max_lines` lines of about `max_chars` characters.

    Sentences stay together when they fit; a word is never cut.
    """
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    phrases = [p for p in re.split(r"(?<=[.!?;:])\s+", clean) if p]
    cues, cur = [], []
    for phrase in phrases:
        lines = _wrap_phrase(phrase.split(), max_chars)
        if len(lines) <= max_lines and len(cur) + len(lines) <= max_lines:
            cur += lines
            continue
        if cur:
            cues.append("\n".join(cur))
            cur = []
        for i in range(0, len(lines), max_lines):
            chunk = lines[i:i + max_lines]
            if i + max_lines >= len(lines) and len(chunk) < max_lines:
                cur = chunk
            else:
                cues.append("\n".join(chunk))
    if cur:
        cues.append("\n".join(cur))
    return cues


def _fmt_srt_time(seconds):
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(total_ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _fmt_ass_time(seconds):
    total_cs = max(0, int(round(float(seconds) * 100)))
    hours, rem = divmod(total_cs, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def srt_text(cues):
    """SubRip text for [(start_s, end_s, text)]."""
    blocks = [f"{i}\n{_fmt_srt_time(a)} --> {_fmt_srt_time(b)}\n{text}" for i, (a, b, text) in enumerate(cues, 1)]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def ass_text(cues, width, height):
    """ASS subtitles in one clean style. Portrait captions sit above the bottom 22%, clear of the phone's own buttons."""
    portrait = height > width
    size = 60 if portrait else 54
    margin_v = int(round(height * 0.22)) if portrait else int(round(height * 0.075))
    margin_h = 70 if portrait else 160
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Caption,Geist Medium,{size},&H00FFFFFF,&H00FFFFFF,&H000A0A0A,&H00000000,0,0,0,0,100,100,0,0,1,5,0,2,"
        f"{margin_h},{margin_h},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for start, end, text in cues:
        safe = text.replace("{", "(").replace("}", ")").replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},Caption,,0,0,0,,{safe}")
    return head + "\n".join(lines) + ("\n" if lines else "")


# --- Scenes and timing --------------------------------------------------------------------------------------------

def parse_scenes(script_body, duration_s=30):
    """Scenes from the script with the same parser the captions step uses."""
    import workflows

    scenes, _ = workflows._extract_shotlist_and_srt(script_body, duration_s=duration_s)
    if not scenes:
        raise RenderError("The script has no scenes to render.", "no_scenes")
    if len(scenes) > MAX_SCENES:
        raise RenderError(
            f"The script has {len(scenes)} scenes. Videos here are limited to {MAX_SCENES} scenes; shorten the script.",
            "too_long",
        )
    out = []
    for sc in scenes:
        out.append({
            "scene": int(sc.get("scene") or len(out) + 1),
            "start_s": float(sc.get("start_s") or 0.0),
            "end_s": float(sc.get("end_s") or 0.0),
            "visual": _clean_text(sc.get("visual")),
            "footage": _clean_text(sc.get("footage")),
            "on_screen": _clean_text(sc.get("on_screen")),
            "voiceover": _clean_text(sc.get("voiceover")),
        })
    return out


def _first_words(text, count=7):
    words = _clean_text(text).split()
    return " ".join(words[:count])


def plan_timeline(scenes, vo_seconds, end_card_s=END_CARD_S):
    """Place the scenes on a timeline. Returns (entries, total_seconds).

    A scene lasts max(planned time, its voice clip + 0.4 s). `vo_seconds[i]` is the clip length or None (no voice).
    A clean end card closes the video.
    """
    entries = []
    cursor = 0.0
    for i, sc in enumerate(scenes):
        planned = max(MIN_SCENE_S, float(sc["end_s"]) - float(sc["start_s"]))
        vo = vo_seconds[i] if i < len(vo_seconds) else None
        duration = max(planned, float(vo) + SCENE_PAD_S) if vo else planned
        text = sc.get("voiceover") or ""
        if vo:
            vo_len = float(vo)
        elif text:  # no voice clip: captions still run, over the time the line takes to read
            vo_len = min(estimate_speech_seconds(text), max(0.0, duration - VO_LEAD_S - 0.1))
        else:
            vo_len = 0.0
        entries.append({
            "index": i,
            "scene": sc.get("scene", i + 1),
            "end_card": False,
            "start_s": round(cursor, 3),
            "duration_s": round(duration, 3),
            "vo_text": text,
            "voiced": bool(vo),
            "vo_start_s": round(cursor + VO_LEAD_S, 3),
            "vo_len_s": round(vo_len, 3),
            "on_screen": sc.get("on_screen") or "",
            "visual": sc.get("visual") or "",
            "footage": sc.get("footage") or "",
        })
        cursor += duration
    entries.append({
        "index": len(scenes), "scene": len(scenes) + 1, "end_card": True, "start_s": round(cursor, 3),
        "duration_s": round(end_card_s, 3), "vo_text": "", "voiced": False, "vo_start_s": round(cursor, 3),
        "vo_len_s": 0.0, "on_screen": "", "visual": "", "footage": "",
    })
    cursor += end_card_s
    return entries, round(cursor, 3)


def retime_captions(timeline):
    """Captions that follow the real voice clips: each scene's line is split into short cues spread over its clip."""
    cues = []
    for entry in timeline:
        text = entry.get("vo_text") or ""
        if entry.get("end_card") or not text or entry.get("vo_len_s", 0) <= 0:
            continue
        parts = split_caption_lines(text)
        if not parts:
            continue
        weights = [len(p.replace("\n", " ")) + 4 for p in parts]
        total_w = float(sum(weights))
        start = float(entry["vo_start_s"])
        length = float(entry["vo_len_s"])
        cursor = start
        for part, weight in zip(parts, weights):
            end = cursor + length * weight / total_w
            cues.append((round(cursor, 3), round(end, 3), part))
            cursor = end
    return cues


# --- Rules, product and media -------------------------------------------------------------------------------------

def _check_rules(item):
    """Refuse a script that names a confidential product unless the item is confidential itself.

    Returns True when the item is confidential: then no repo footage and no cloud images are used (everything is local
    anyway). Fails closed when the product rules cannot be read.
    """
    scope = confidential_guard.scope()
    if scope.broken:
        raise RenderError(
            "The product rules could not be read, so this video was not rendered. Check Settings > Product rules and try again.",
            "confidential",
        )
    pid = str(item.get("product_id") or "").strip()
    by_source = bool(
        item.get("id") in scope.content_ids
        or scope.session(item.get("session_id"))
        or (pid and (pid.lower() in scope.product_ids or confidential_guard.product_is_confidential(pid)))
    )
    if not by_source and scope.text(item.get("title") or "", item.get("body") or "", item.get("notes") or ""):
        raise RenderError(
            "This script names a product that is confidential, so it was not rendered. Remove the name from the "
            "script or mark the item confidential, then render again.",
            "confidential",
        )
    return by_source


def _product_label(item, local_only):
    """The product name for the kicker, or None. Products that may not be shown are never named on screen."""
    if local_only:
        return None
    pid = str(item.get("product_id") or "").strip()
    if not pid:
        return None
    try:
        product = product_rules.get_product(pid, path=confidential_guard._rules_path())
    except Exception:
        return None
    if product is None:
        return pid.replace("-", " ").replace("_", " ").title()
    if product.get("confidential") or product.get("show") != "everything":
        return None
    return str(product.get("name") or pid)


def _tokens(text):
    return {t for t in _WORD_RE.findall(str(text or "").lower()) if t not in _STOP_WORDS and len(t) > 1}


def wants_footage(scene):
    """True if the scene's visual or footage note asks for real footage (a screenshot, recording, demo, terminal)."""
    return bool(_FOOTAGE_RE.search(f"{scene.get('visual') or ''} {scene.get('footage') or ''}"))


def pick_media(scene, repo, used):
    """A real screenshot or GIF from the repo's own media for a scene that asks for footage, or None.

    Never from a confidential repo, never a file that names a confidential product, never the same file twice.
    Only repos on this computer have files to use.
    """
    if not repo or not wants_footage(scene):
        return None
    if confidential_guard.is_confidential_repo(repo):
        return None
    root = repo.get("local_path")
    if repo.get("kind") != "local" or not root or not os.path.isdir(root):
        return None
    import repos

    real_root = os.path.realpath(root)
    want = _tokens(f"{scene.get('visual') or ''} {scene.get('footage') or ''} {scene.get('on_screen') or ''}")
    best, best_score = None, -1
    for entry in repos.scan_local_media(root):
        rel = entry.get("path") or ""
        if os.path.splitext(rel)[1].lower() not in _IMAGE_EXTS:
            continue
        full = os.path.realpath(os.path.join(root, rel))
        try:
            if os.path.commonpath([real_root, full]) != real_root or not os.path.isfile(full):
                continue
        except ValueError:
            continue
        if full in used or confidential_guard.mentions(rel):
            continue
        score = len(want & _tokens(os.path.splitext(os.path.basename(rel))[0].replace("-", " ").replace("_", " ")))
        if score > best_score:
            best, best_score = {"path": full, "rel": rel, "repo_id": repo.get("id"), "repo_name": repo.get("name")}, score
    return best


def _repo_for(item, repo_id):
    """The repo whose media may be used: the one chosen for the run, else the repo of the item's product."""
    import repos

    try:
        if repo_id:
            return repos.get_repo(repo_id)
        pid = str(item.get("product_id") or "").strip().lower()
        if not pid:
            return None
        for repo in repos.list_repos():
            if str(repo.get("product_id") or repo.get("id") or "").lower() == pid and repo.get("kind") == "local":
                return repo
    except Exception:
        return None
    return None


def _flux_image(prompt, content_id):
    """An optional FLUX illustration (only when the person turned it on). Returns a local path or None."""
    try:
        from tools import ToolContext
        from tools import design

        ctx = ToolContext(session_id="video-render", agent_id="designer")
        res = design.generate_image(ctx, prompt, width=1024, height=1024, content_id=content_id)
        rel = (res or {}).get("path")
        return os.path.join(paths.DATA_ROOT, rel) if rel else None
    except Exception:
        return None


# --- Scene layouts ------------------------------------------------------------------------------------------------

def _font_url(key):
    return Path(paths.resource("templates", "fonts", FONT_FILES[key])).as_uri()


def scene_html(spec, width, height):
    """The HTML of one picture: flat field colour, ink type (Anton), mono labels, no gradients or shadows."""
    esc = html_lib.escape
    field = PALETTE.get(spec.get("field"), PALETTE["sky"])
    ink = PALETTE["ink"]
    portrait = height > width
    # Wider than the strongest pan or push crops (5 % zoom), so labels never touch the edge of the frame.
    m = int(round(max(width, height) * 0.07))
    label = max(26, int(round(min(width, height) / 28)))
    fonts = "".join(
        f"@font-face{{font-family:'{name}';src:url('{_font_url(key)}');}}"
        for name, key in (("Anton", "anton"), ("Instrument Serif", "serif"), ("JetBrains Mono", "mono"))
    )
    base = (
        f"{fonts}*{{box-sizing:border-box;margin:0;padding:0}}"
        f"html,body{{width:{width}px;height:{height}px;overflow:hidden;background:{field};color:{ink}}}"
        f".label{{position:absolute;font-family:'JetBrains Mono',monospace;font-size:{label}px;letter-spacing:.08em;"
        f"text-transform:uppercase}}"
        f".box{{position:absolute;display:flex;flex-direction:column;justify-content:flex-end}}"
        f".big{{font-family:Anton,sans-serif;text-transform:uppercase;line-height:.98;letter-spacing:.005em}}"
    )
    fit_js = (
        "function fit(el,box,start){var s=start;el.style.fontSize=s+'px';"
        "while(s>40&&(el.scrollHeight>box.clientHeight||el.scrollWidth>box.clientWidth)){s-=6;el.style.fontSize=s+'px';}}"
        "document.fonts.ready.then(function(){var imgs=[].slice.call(document.images);"
        "return Promise.all(imgs.map(function(i){return i.decode?i.decode().catch(function(){}):0;}));})"
        ".then(function(){document.querySelectorAll('[data-fit]').forEach(function(el){"
        "fit(el,el.parentNode,parseInt(el.getAttribute('data-fit'),10));});document.body.setAttribute('data-ready','1');});"
    )
    body = []
    if spec.get("kind") == "end":
        product = spec.get("product") or ""
        mid = int(height * (0.50 if portrait else 0.55))
        body.append(
            f"<div class='box' style='left:{m}px;top:{int(height * 0.18)}px;width:{width - 2 * m}px;"
            f"height:{mid - int(height * 0.18)}px'><div class='big' style='white-space:nowrap' "
            f"data-fit='{int(height * 0.34)}'>MAGNM</div></div>"
        )
        if product:
            body.append(
                f"<div style=\"position:absolute;left:{m}px;top:{mid + int(m * 0.2)}px;font-family:'Instrument Serif',serif;"
                f"font-style:italic;font-size:{int(min(width, height) * 0.09)}px;line-height:1.1\">{esc(product)}</div>"
            )
        body.append(f"<div class='label' style='left:{m}px;bottom:{m}px'>{esc(spec.get('foot') or SITE)}</div>")
    else:
        body.append(f"<div class='label' style='left:{m}px;top:{m}px'>{esc(spec.get('kicker') or 'MAGNM')}</div>")
        count = int(spec.get("count") or 0)
        if count > 0:
            counter = f"{int(spec.get('index') or 1):02d} / {count:02d}"
            body.append(f"<div class='label' style='right:{m}px;top:{m}px'>{counter}</div>")
        top = m + int(label * 3.2)
        bottom = int(height * (0.66 if portrait else 0.78))
        text_left, text_w, text_top = m, width - 2 * m, top
        image = spec.get("image")
        if image:
            url = Path(image).as_uri()
            if portrait:
                ih = int((bottom - top) * 0.44)
                body.append(
                    f"<div style='position:absolute;left:{m}px;top:{top}px;width:{width - 2 * m}px;height:{ih}px;"
                    f"display:flex;align-items:center;justify-content:center;overflow:hidden'>"
                    f"<img src='{url}' style='max-width:100%;max-height:100%;border:3px solid {ink}'></div>"
                )
                text_top = top + ih + int(height * 0.03)
            else:
                col = int((width - 3 * m) * 0.5)
                body.append(
                    f"<div style='position:absolute;left:{width - m - col}px;top:{top}px;width:{col}px;"
                    f"height:{bottom - top}px;display:flex;align-items:center;justify-content:center;overflow:hidden'>"
                    f"<img src='{url}' style='max-width:100%;max-height:100%;border:3px solid {ink}'></div>"
                )
                text_w = col
        start = int(height * (0.17 if portrait else 0.30))
        body.append(
            f"<div class='box' style='left:{text_left}px;top:{text_top}px;width:{text_w}px;height:{bottom - text_top}px'>"
            f"<div class='big' data-fit='{start}'>{esc(spec.get('text') or '')}</div></div>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>scene</title>"
        f"<style>{base}</style></head><body>{''.join(body)}<script>{fit_js}</script></body></html>"
    )


# --- Toolchain (the real TTS, browser, ffmpeg and ffprobe; tests swap this for a fake) ----------------------------

class RealToolchain:
    """Runs the real tools. Every method is small so tests can replace the whole object."""

    def have_ffmpeg(self):
        return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

    def synth(self, text, wav_path, voice, speed):
        return voiceover.synthesize(text, wav_path, voice=voice, speed=speed)

    def screens(self, specs, width, height, cancel=None, on_scene=None):
        """Draw each spec's picture as PNG with Playwright (file pages only; every network request is refused)."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RenderError(NO_BROWSER_MSG, "no_browser") from exc
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:
                raise RenderError(NO_BROWSER_MSG, "no_browser") from exc
            try:
                context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
                context.route(
                    "**/*",
                    lambda route: route.continue_() if route.request.url.startswith(("file:", "data:", "about:")) else route.abort(),
                )
                page = context.new_page()
                for k, spec in enumerate(specs, 1):
                    if cancel is not None and cancel():
                        raise RenderCancelled()
                    if on_scene is not None:
                        on_scene(k)
                    html_path = os.path.splitext(spec["png"])[0] + ".html"
                    with open(html_path, "w", encoding="utf-8") as fh:
                        fh.write(scene_html(spec, width, height))
                    page.goto(Path(html_path).as_uri())
                    page.wait_for_selector("body[data-ready='1']", timeout=20000)
                    page.screenshot(path=spec["png"], type="png")
            finally:
                browser.close()

    def ffmpeg(self, args, cwd=None, cancel=None, on_progress=None):
        """Run ffmpeg; returns its stderr text. A failure raises RenderError, cancel stops the process."""
        err_file = tempfile.TemporaryFile()
        proc = subprocess.Popen(
            args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err_file, text=True, bufsize=1,
        )
        lines = []

        def read():
            for line in proc.stdout:
                lines.append(line)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        seen = 0
        try:
            while proc.poll() is None:
                if cancel is not None and cancel():
                    proc.terminate()
                    try:
                        proc.wait(5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise RenderCancelled()
                if on_progress is not None:
                    while seen < len(lines):
                        line = lines[seen]
                        seen += 1
                        if line.startswith(("out_time_us=", "out_time_ms=")):
                            try:
                                on_progress(int(line.split("=", 1)[1]) / 1_000_000.0)
                            except ValueError:
                                pass
                time.sleep(0.1)
            reader.join(2)
            err_file.seek(0)
            err_text = err_file.read().decode("utf-8", "replace")
            if proc.returncode != 0:
                tail = " ".join(err_text.strip().splitlines()[-3:])[:300]
                raise RenderError(f"ffmpeg could not make the video: {tail}", "ffmpeg_failed")
            return err_text
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            reader.join(2)
            proc.stdout.close()
            err_file.close()

    def probe(self, path):
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise RenderError("ffprobe could not read the finished video.", "check_failed")
        data = json.loads(proc.stdout or "{}")
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
        fmt_duration = float((data.get("format") or {}).get("duration") or 0.0)

        def stream_duration(stream):
            try:
                return float(stream.get("duration")) if stream and stream.get("duration") else fmt_duration
            except (TypeError, ValueError):
                return fmt_duration

        return {
            "duration_s": fmt_duration,
            "video_duration_s": stream_duration(video),
            "audio_duration_s": stream_duration(audio) if audio else None,
            "width": int(video.get("width") or 0) if video else 0,
            "height": int(video.get("height") or 0) if video else 0,
            "has_video": video is not None,
            "has_audio": audio is not None,
            "video_codec": video.get("codec_name") if video else None,
            "audio_codec": audio.get("codec_name") if audio else None,
        }

    def frame_spread(self, path, at_s):
        """The spread (standard deviation) of grey levels in one sampled frame; a blank picture has almost none."""
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, at_s):.2f}", "-i", path, "-frames:v", "1",
             "-vf", "scale=64:64", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60,
        )
        data = proc.stdout or b""
        if proc.returncode != 0 or len(data) < 64:
            return 0.0
        return float(statistics.pstdev(data))


TOOLCHAIN = RealToolchain()


# --- Status -------------------------------------------------------------------------------------------------------

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _set_status(content_id, **fields):
    with _STATUS_LOCK:
        cur = _STATUS.setdefault(content_id, {"state": "idle"})
        cur.update(fields)
        snapshot = dict(cur)
    snapshot["content_id"] = content_id
    try:
        events.publish("video_render", snapshot)
    except Exception:
        pass


def _video_entries(content_id):
    item = db.get_content_item(content_id) or {}
    prefix = _rel_dir(content_id) + "/"
    return [
        f for f in item.get("files") or []
        if isinstance(f, dict) and str(f.get("path") or "").replace("\\", "/").startswith(prefix)
    ]


def render_status(content_id):
    """The state of the latest render for an item: idle, running, done, failed or cancelled, with progress and files."""
    with _STATUS_LOCK:
        cur = dict(_STATUS.get(content_id) or {"state": "idle"})
    cur["content_id"] = content_id
    if cur.get("state") in ("idle", "done"):
        cur["files"] = _video_entries(content_id)
    return cur


def browser_available():
    """True if Playwright can be imported (the browser itself is checked when a render draws its first picture)."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def note_queued(content_id, job_id):
    """Remember that a render job was queued, so the UI shows 'waiting' and can cancel it."""
    _set_status(content_id, state="queued", percent=0, message="Waiting for its turn", error=None, error_code=None,
                job_id=job_id)


def note_cancelled(content_id):
    """A queued render was cancelled before it started."""
    _set_status(content_id, state="cancelled", message="The render was cancelled.", error=None, error_code="cancelled")


def active_renders():
    """Content ids that are queued or rendering right now."""
    with _STATUS_LOCK:
        return sorted(cid for cid, st in _STATUS.items() if st.get("state") in ("queued", "running"))


def preflight(content_id, voice=True, aspects=None):
    """Plain, immediate refusals before a render is queued: unknown script, no ffmpeg, no voice installed."""
    item = _item_or_error(content_id)
    _clean_aspects(aspects)
    if not TOOLCHAIN.have_ffmpeg():
        raise RenderError(NO_FFMPEG_MSG, "no_ffmpeg")
    if voice:
        try:
            voiceover.require_voice()
        except voiceover.VoiceError as exc:
            raise RenderError(exc.message, exc.code) from exc
    return item


def _rel_dir(content_id):
    return os.path.relpath(paths.data("outputs", "video", content_id), paths.DATA_ROOT).replace("\\", "/")


# --- The render ---------------------------------------------------------------------------------------------------

def _clean_aspects(aspects):
    if aspects is None:
        return list(DEFAULT_ASPECTS)
    if isinstance(aspects, str):
        aspects = [aspects]
    out = []
    for a in aspects:
        a = str(a).strip().replace("x", ":")
        if a not in ASPECTS:
            raise RenderError(f"The aspect '{str(a)[:20]}' is not supported. Use 9:16 or 16:9.", "bad_aspect")
        if a not in out:
            out.append(a)
    if not out:
        raise RenderError("Choose at least one aspect: 9:16 or 16:9.", "bad_aspect")
    return out


_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,120}")


def _item_or_error(content_id):
    cid = str(content_id or "")
    item = db.get_content_item(cid) if _ID_RE.fullmatch(cid) else None
    if item is None:
        raise RenderError("That script was not found.", "not_found")
    return item


def _clip_dir():
    d = os.path.join(voiceover.cache_dir(), "clips")
    os.makedirs(d, exist_ok=True)
    return d


def _prune_clips(max_age_days=14):
    cutoff = time.time() - max_age_days * 86400
    try:
        for name in os.listdir(_clip_dir()):
            full = os.path.join(_clip_dir(), name)
            if name.endswith(".wav") and os.path.getmtime(full) < cutoff:
                os.remove(full)
    except OSError:
        pass


def _voice_clips(scenes, voice, speed, check, step=None):
    """One voice clip per scene that has a voice-over line (cached by voice, speed and text). [{wav, duration_s}|None]."""
    try:
        voiceover.require_voice()
    except voiceover.VoiceError as exc:
        raise RenderError(exc.message, exc.code) from exc
    clips = []
    spoken_scenes = [i for i, sc in enumerate(scenes) if sc["voiceover"]]
    for i, sc in enumerate(scenes):
        check()
        if not sc["voiceover"]:
            clips.append(None)
            continue
        text = spoken_text(sc["voiceover"])
        key = hashlib.sha1(f"{voice}|{speed}|{text}".encode("utf-8")).hexdigest()[:20]
        wav = os.path.join(_clip_dir(), key + ".wav")
        if step:
            step(spoken_scenes.index(i) + 1, len(spoken_scenes))
        if os.path.isfile(wav):
            try:
                clips.append({"wav": wav, "duration_s": voiceover.wav_duration(wav)})
                continue
            except Exception:
                pass
        try:
            info = TOOLCHAIN.synth(text, wav, voice, speed)
        except voiceover.VoiceError as exc:
            raise RenderError(exc.message, exc.code) from exc
        clips.append({"wav": wav, "duration_s": float(info["duration_s"])})
    return clips


def voice_over(content_id, duration_s=None, progress=None, cancel=None):
    """Speak every scene of a script now (the workflow's voice_over step). Clips are cached for the render step."""
    item = _item_or_error(content_id)
    settings = video_settings.get_settings()
    scenes = parse_scenes(item.get("body") or "", duration_s or 30)
    if not any(sc["voiceover"] for sc in scenes):
        return {"clips": 0, "seconds": 0.0, "voice": settings["voice"]}

    def check():
        if cancel is not None and cancel():
            raise RenderCancelled()

    def step(k, n):
        if progress:
            progress(int(100 * (k - 1) / max(1, n)), f"Voice-over: scene {k} of {n}")

    _prune_clips()
    clips = _voice_clips(scenes, settings["voice"], settings["speed"], check, step)
    spoken = [c for c in clips if c]
    if progress:
        progress(100, "Voice-over ready")
    return {"clips": len(spoken), "seconds": round(sum(c["duration_s"] for c in spoken), 2), "voice": settings["voice"]}


def check_outputs(files, expected_s, voice):
    """ffprobe checks on the finished files; a failed check fails the render plainly. Returns {"ok": True, ...}."""
    report = {}
    for aspect, path in files.items():
        want_w, want_h = ASPECTS[aspect]["size"]
        name = ASPECTS[aspect]["file"]
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            raise RenderError(f"The {aspect} video file was not written.", "check_failed")
        info = TOOLCHAIN.probe(path)
        if not info.get("has_video") or (info.get("width"), info.get("height")) != (want_w, want_h):
            raise RenderError(f"The {aspect} video is not {want_w}x{want_h} ({name}).", "check_failed")
        # Each stream is measured on its own: the container's length is the longer of the two and would hide a short picture.
        lengths = [info.get("video_duration_s") or info.get("duration_s")]
        if info.get("has_audio") and info.get("audio_duration_s"):
            lengths.append(info["audio_duration_s"])
        for length in lengths:
            if abs(float(length or 0.0) - float(expected_s)) > DURATION_TOLERANCE_S:
                raise RenderError(
                    f"The {aspect} video's length ({float(length or 0.0):.1f} s) does not match the audio "
                    f"({float(expected_s):.1f} s).",
                    "check_failed",
                )
        if voice and not info.get("has_audio"):
            raise RenderError(f"The {aspect} video has no audio stream, but the voice-over was on.", "check_failed")
        for frac in (0.1, 0.5, 0.95):
            at = max(0.0, min(float(expected_s) * frac, float(expected_s) - 0.2))
            if TOOLCHAIN.frame_spread(path, at) < BLANK_SPREAD:
                raise RenderError(f"The {aspect} video has a blank picture at {at:.1f} s.", "check_failed")
        report[aspect] = {"width": info["width"], "height": info["height"], "duration_s": round(float(info["duration_s"]), 2),
                          "audio": bool(info.get("has_audio")), "video_codec": info.get("video_codec"),
                          "audio_codec": info.get("audio_codec")}
    return {"ok": True, "files": report}


def _motion_filter(i, frames, width, height):
    kind = MOTIONS[i % len(MOTIONS)]
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if kind == "push":
        z, x, y = f"1+0.05*on/{frames}", center_x, center_y
    elif kind == "pull":
        z, x, y = f"1.05-0.05*on/{frames}", center_x, center_y
    elif kind == "pan_right":
        z, x, y = "1.05", f"(iw-iw/zoom)*on/{frames}", center_y
    else:
        z, x, y = "1.05", f"(iw-iw/zoom)*(1-on/{frames})", center_y
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={FPS}"


def _video_args(timeline, aspect, audio_name, total_s, captions_name, out_name):
    width, height = ASPECTS[aspect]["size"]
    tag = ASPECTS[aspect]["tag"]
    n = len(timeline)
    args = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]
    for i in range(n):
        args += ["-i", f"scene-{tag}-{i}.png"]
    args += ["-i", audio_name]
    parts = []
    for i, entry in enumerate(timeline):
        # Picture i fades in during the last XFADE_S of the one before it, so every picture but the first starts
        # XFADE_S early. Then picture i is fully on screen at its start second and the video is exactly total_s long.
        frames = max(2, int(round((entry["duration_s"] + (XFADE_S if i > 0 else 0.0)) * FPS)))
        parts.append(
            f"[{i}:v]scale={2 * width}:{2 * height}:flags=lanczos,{_motion_filter(i, frames, width, height)},setsar=1,format=yuv420p[v{i}]"
        )
    prev = "v0"
    for i in range(1, n):
        offset = round(timeline[i]["start_s"] - XFADE_S, 3)
        parts.append(f"[{prev}][v{i}]xfade=transition={TRANSITIONS[(i - 1) % len(TRANSITIONS)]}:duration={XFADE_S}:offset={offset}[x{i}]")
        prev = f"x{i}"
    if captions_name:
        parts.append(f"[{prev}]subtitles=filename={captions_name}:fontsdir=fonts,format=yuv420p[vout]")
    else:
        parts.append(f"[{prev}]format=yuv420p[vout]")
    args += [
        "-filter_complex", ";".join(parts), "-map", "[vout]", "-map", f"{n}:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "8M", "-bufsize", "16M",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-t", f"{total_s:.3f}", "-movflags", "+faststart", out_name,
    ]
    return args


def _sfx_path():
    path = paths.resource(*SFX_REL)
    return path if os.path.isfile(path) else None


def _audio_args(timeline, total_s, voice_name, sfx, voice_on, out_name, measured=None, measure_only=False):
    """ffmpeg arguments that mix the voice track with a short transition sound and normalise the loudness."""
    level = "loudnorm=I=-16:TP=-1.5:LRA=11"
    transitions = [e for e in timeline[1:]]
    args = ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "info" if measure_only else "error", "-i", voice_name]
    parts = [f"[0:a]aresample=48000,apad=whole_dur={total_s:.3f}[vo]"]
    mix_inputs = ["[vo]"]
    if sfx and transitions:
        args += ["-i", sfx]
        count = len(transitions)
        gain = 0.35 if voice_on else 0.5
        base = f"[1:a]aresample=48000,atrim=end={SFX_LEN_S},asetpts=PTS-STARTPTS,areverse,lowpass=f=7000,volume={gain}"
        if count == 1:
            parts.append(f"{base}[s0]")
        else:
            parts.append(f"{base},asplit={count}" + "".join(f"[s{j}]" for j in range(count)))
        for j, entry in enumerate(transitions):
            delay = max(0, int(round((entry["start_s"] - SFX_LEN_S) * 1000)))
            parts.append(f"[s{j}]adelay={delay}|{delay}[d{j}]")
            mix_inputs.append(f"[d{j}]")
    parts.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:duration=first:normalize=0[mix]")
    if not voice_on:
        tail = "[mix]aresample=48000[aout]"
    elif measure_only:
        tail = f"[mix]{level}:print_format=json[aout]"
    elif measured:
        tail = (
            f"[mix]{level}:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true,aresample=48000[aout]"
        )
    else:
        tail = f"[mix]{level},aresample=48000[aout]"
    parts.append(tail)
    args += ["-filter_complex", ";".join(parts), "-map", "[aout]", "-t", f"{total_s:.3f}"]
    if measure_only:
        args += ["-f", "null", "-"]
    else:
        args += ["-c:a", "pcm_s16le", "-ar", "48000", out_name]
    return args


def _parse_loudnorm(stderr_text):
    """The measurement block loudnorm prints, or None."""
    match = None
    for match in re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr_text or "", re.DOTALL):
        pass
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
        wanted = {k: float(data[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError, TypeError):
        return None
    if not all(math.isfinite(v) for v in wanted.values()):
        return None
    return wanted


def _readme(item, result, settings, timeline, media_used, flux_used, local_only):
    lines = [
        f"# Video for {item.get('title') or item['id']}",
        "",
        f"Content item: {item['id']}",
        f"Rendered: {_now_iso()}",
        f"Length: {result['total_s']:.1f} s, {len(timeline) - 1} scenes and an end card",
        f"Sizes: {', '.join(result['aspects'])} (1080x1920 and 1920x1080 H.264 + AAC, faststart)",
        f"Captions: {'burned in and ' if result['captions_burned'] else ''}written to captions.srt",
        "",
        "## How it was made",
        "Made on this computer with no AI model for the picture or the sound:",
        "- Scenes, on-screen text and voice-over lines come from the reviewed script.",
        "- Pictures: flat brand layouts drawn with Playwright (Chromium) in the card palette"
        + (", with real repo screenshots where the script asked for footage." if media_used else "."),
        "- Motion, cross-fades, mixing, captions (libass) and checks: ffmpeg and ffprobe.",
        "- Voice-over: Kokoro text-to-speech (kokoro-onnx), run locally.",
        "- Music: none.",
        "",
        "## Voice",
    ]
    if result["voice"]["used"]:
        lines.append(f"- Voice-over: {result['voice']['voice']}, speed {result['voice']['speed']}, spoken by Kokoro.")
        lines.append("- Names are respelled for the voice only; captions use the real spelling.")
    else:
        lines.append("- No voice-over (rendered without a voice). Scene lengths follow the script's planned timings.")
    lines += ["", "## Assets and licences"]
    lines.append("- Fonts: Anton, Instrument Serif, JetBrains Mono, Geist (SIL OFL 1.1).")
    lines.append("- Transition sound: Kenney impactSoft_heavy_003 (CC0), played reversed.")
    lines.append("- Voice model: Kokoro v1.0 (Apache-2.0); kokoro-onnx (MIT).")
    for m in media_used:
        lines.append(f"- Screenshot: {m['rel']} from the repo {m.get('repo_name') or m.get('repo_id')} (your own media).")
    if flux_used:
        lines.append(f"- {flux_used} illustration(s) generated with FLUX (turned on in Settings > Video).")
    if local_only:
        lines.append("")
        lines.append("This item is confidential: everything was made locally, with no repo footage and no cloud images.")
    return "\n".join(lines) + "\n"


def _publish_files(work_out, out_dir, names):
    """Move the finished files into the output folder, replacing the previous render."""
    os.makedirs(out_dir, exist_ok=True)
    for stale in ("video-9x16.mp4", "video-16x9.mp4", "poster.jpg", "preview-480p.mp4", "captions.srt",
                  "voiceover.wav", "voiceover.txt", "README.md"):
        try:
            os.remove(os.path.join(out_dir, stale))
        except OSError:
            pass
    for name in names:
        os.replace(os.path.join(work_out, name), os.path.join(out_dir, name))


def _attach(content_id, aspects, out_dir, has_poster, total_s):
    """Attach the video, poster and captions to the content item, replacing earlier ones of this folder."""
    item = _item_or_error(content_id)
    prefix = _rel_dir(content_id) + "/"
    # Earlier files of this folder go, and so do planned captions: the ones timed to the voice match the video.
    kept = [
        f for f in item.get("files") or []
        if not (isinstance(f, dict) and (str(f.get("path") or "").replace("\\", "/").startswith(prefix)
                                         or f.get("kind") == "captions"))
    ]
    added = []
    for aspect in aspects:
        rel = os.path.relpath(os.path.join(out_dir, ASPECTS[aspect]["file"]), paths.DATA_ROOT).replace("\\", "/")
        added.append({"kind": "video", "path": rel, "aspect": aspect, "duration_s": round(total_s, 2)})
    if has_poster:
        added.append({"kind": "poster", "path": prefix + "poster.jpg"})
    added.append({"kind": "captions", "path": prefix + "captions.srt", "retimed": True})
    db.set_content_files(content_id, kept + added)
    return added


def render_video(content_id, aspects=None, voice=True, *, duration_s=None, repo_id=None, captions=None, flux=None,
                 progress=None, cancel=None, wait=True):
    """Render a script into finished MP4s. Raises RenderError (plain message and a code) when it cannot.

    voice=False renders without a voice-over. progress(percent, message) and cancel() are optional. One render runs
    at a time: with wait=False a second one raises RenderBusy at once instead of waiting.
    """
    if not _RENDER_LOCK.acquire(blocking=bool(wait)):
        raise RenderBusy()
    started = time.monotonic()
    try:
        return _render_locked(
            content_id, _clean_aspects(aspects), bool(voice), duration_s, repo_id, captions, flux, progress, cancel, started,
        )
    except RenderCancelled:
        _set_status(content_id, state="cancelled", message="The render was cancelled.", error=None, error_code="cancelled")
        raise
    except RenderError as exc:
        _set_status(content_id, state="failed", message=exc.message, error=exc.message, error_code=exc.code)
        raise
    except Exception as exc:
        err = RenderError(f"The video could not be rendered: {str(exc)[:200]}", "render_failed")
        _set_status(content_id, state="failed", message=err.message, error=err.message, error_code=err.code)
        raise err from exc
    finally:
        _RENDER_LOCK.release()


def _render_locked(content_id, aspects, voice_on, duration_s, repo_id, captions, flux, progress, cancel, started):
    item = _item_or_error(content_id)
    settings = video_settings.get_settings()
    burn = settings["captions"] if captions is None else bool(captions)
    use_flux = settings["flux"] if flux is None else bool(flux)

    last = {"pct": 0}

    def report(pct, message, **extra):
        pct = max(last["pct"], min(100, int(pct)))
        if pct == last["pct"] and message == last.get("msg"):
            return
        last["pct"], last["msg"] = pct, message
        _set_status(content_id, state="running", percent=pct, message=message, error=None, error_code=None, **extra)
        if progress:
            progress(pct, message)

    def check():
        if cancel is not None and cancel():
            raise RenderCancelled()

    _set_status(content_id, state="running", percent=0, message="Starting", error=None, error_code=None,
                started_at=_now_iso(), finished_at=None)
    report(1, "Checking the script")
    if not TOOLCHAIN.have_ffmpeg():
        raise RenderError(NO_FFMPEG_MSG, "no_ffmpeg")
    local_only = _check_rules(item)
    check()
    scenes = parse_scenes(item.get("body") or "", duration_s or 30)
    if voice_on and not any(sc["voiceover"] for sc in scenes):
        voice_on = False  # nothing to speak

    work_root = paths.data("cache", "video")
    os.makedirs(work_root, exist_ok=True)
    work = tempfile.mkdtemp(prefix="render-", dir=work_root)
    try:
        return _render_in(
            item, scenes, aspects, voice_on, settings, burn, use_flux, local_only, repo_id, work, report, check,
            started, cancel,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_in(item, scenes, aspects, voice_on, settings, burn, use_flux, local_only, repo_id, work, report, check,
               started, cancel):
    content_id = item["id"]
    voice_name, speed = settings["voice"], settings["speed"]

    # 1. Voice-over: one clip per scene (cached), then the timeline.
    clips = [None] * len(scenes)
    if voice_on:
        def step(k, n):
            report(2 + 13 * (k - 1) / max(1, n), f"Voice-over: scene {k} of {n}")

        clips = _voice_clips(scenes, voice_name, speed, check, step)
    check()
    timeline, total_s = plan_timeline(scenes, [c["duration_s"] if c else None for c in clips])
    n_pics = len(timeline)
    report(16, "Planning the scenes")

    # 2. Pictures: real repo media where asked (never when confidential), else brand layouts.
    product = _product_label(item, local_only)
    kicker = (product or "MAGNM").upper()
    repo = None if local_only else _repo_for(item, repo_id)
    offset = sum(ord(c) for c in content_id) % len(FIELDS)
    used, media_used, flux_count = set(), [], 0
    specs = []
    for entry in timeline:
        i = entry["index"]
        field = FIELDS[(i + offset) % len(FIELDS)]
        if entry["end_card"]:
            specs.append({"kind": "end", "field": FIELDS[(i + offset) % len(FIELDS)], "text": "", "kicker": kicker,
                          "product": product or "", "foot": SITE, "image": None, "index": i + 1, "count": n_pics - 1})
            continue
        text = entry["on_screen"] or _first_words(entry["vo_text"]) or _first_words(entry["visual"], 5)
        image = None
        if not local_only:
            sc = scenes[i]
            picked = pick_media(sc, repo, used) if repo else None
            if picked:
                used.add(picked["path"])
                media_used.append(picked)
                image = picked["path"]
            elif use_flux and flux_count < MAX_FLUX_IMAGES and sc["visual"] and not confidential_guard.mentions(sc["visual"]):
                path = _flux_image(f"Flat minimal illustration, no text, no logos: {sc['visual'][:200]}", content_id)
                if path and os.path.isfile(path):
                    image = path
                    flux_count += 1
        specs.append({"kind": "scene", "field": field, "text": text, "kicker": kicker, "foot": SITE, "image": image,
                      "index": i + 1, "count": n_pics - 1, "product": product or ""})
    check()

    # 3. Audio (shared by both sizes): voice track, transition sounds, loudness.
    report(18, "Mixing the audio")
    track = os.path.join(work, "voice.wav")
    voiceover.write_timeline_track(
        track, [(e["vo_start_s"], c["wav"]) for e, c in zip(timeline, clips) if c], total_s,
    )
    check()
    sfx = _sfx_path()
    measured = None
    if voice_on:
        out = TOOLCHAIN.ffmpeg(
            _audio_args(timeline, total_s, "voice.wav", sfx, True, "audio.wav", measure_only=True), cwd=work, cancel=cancel,
        )
        measured = _parse_loudnorm(out)
    check()
    TOOLCHAIN.ffmpeg(
        _audio_args(timeline, total_s, "voice.wav", sfx, voice_on, "audio.wav", measured=measured), cwd=work, cancel=cancel,
    )
    check()

    # 4. Captions that follow the real voice clips.
    cues = retime_captions(timeline)
    out_dir_work = os.path.join(work, "out")
    os.makedirs(out_dir_work)
    with open(os.path.join(out_dir_work, "captions.srt"), "w", encoding="utf-8") as fh:
        fh.write(srt_text(cues))
    font_dir = os.path.join(work, "fonts")
    os.makedirs(font_dir)
    shutil.copy(paths.resource("templates", "fonts", FONT_FILES["sans"]), font_dir)

    # 5. Pictures and encoding, one size at a time.
    produced = {}
    per_aspect = 60.0 / len(aspects)
    for ai, aspect in enumerate(aspects):
        width, height = ASPECTS[aspect]["size"]
        tag = ASPECTS[aspect]["tag"]
        base = 20 + ai * per_aspect
        a_specs = []
        for spec in specs:
            a_specs.append(dict(spec, png=os.path.join(work, f"scene-{tag}-{len(a_specs)}.png")))

        def on_scene(k, base=base, total=len(a_specs)):
            check()
            label = f"Scene {k} of {total - 1}" if k < total else "End card"
            report(base + (per_aspect * 0.25) * (k / total), f"{label} ({aspect})")

        TOOLCHAIN.screens(a_specs, width, height, cancel=cancel, on_scene=on_scene)
        check()
        ass_name = None
        if burn and cues:
            ass_name = f"captions-{tag}.ass"
            with open(os.path.join(work, ass_name), "w", encoding="utf-8") as fh:
                fh.write(ass_text(cues, width, height))
        out_name = ASPECTS[aspect]["file"]
        encode_base = base + per_aspect * 0.3

        def on_prog(seconds, encode_base=encode_base):
            report(encode_base + per_aspect * 0.7 * min(1.0, seconds / max(total_s, 0.1)), f"Encoding {aspect} video")

        report(encode_base, f"Encoding {aspect} video")
        TOOLCHAIN.ffmpeg(
            _video_args(timeline, aspect, "audio.wav", total_s, ass_name, os.path.join("out", out_name)),
            cwd=work, cancel=cancel, on_progress=on_prog,
        )
        produced[aspect] = os.path.join(out_dir_work, out_name)
        check()

    # 6. Poster and the small preview (from the first size).
    report(82, "Making the poster and preview")
    first = aspects[0]
    first_path = produced[first]
    poster_at = min(1.2, max(0.2, total_s * 0.1))
    TOOLCHAIN.ffmpeg(
        ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{poster_at:.2f}", "-i", first_path,
         "-frames:v", "1", "-q:v", "3", os.path.join("out", "poster.jpg")], cwd=work, cancel=cancel,
    )
    scale = "scale=480:-2" if first == "9:16" else "scale=-2:480"
    TOOLCHAIN.ffmpeg(
        ["ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error", "-i", first_path, "-vf", scale,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "29", "-maxrate", "900k", "-bufsize", "1800k",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-movflags", "+faststart",
         os.path.join("out", "preview-480p.mp4")], cwd=work, cancel=cancel,
    )
    check()

    # 7. Checks before anything is kept.
    report(90, "Checking the files")
    checks = check_outputs(produced, total_s, voice_on)
    if voice_on:
        shutil.copy(os.path.join(work, "voice.wav"), os.path.join(out_dir_work, "voiceover.wav"))
    with open(os.path.join(out_dir_work, "voiceover.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(f"Scene {e['scene']}: {e['vo_text']}" for e in timeline if e["vo_text"]) + "\n")
    result = {
        "content_id": content_id,
        "aspects": aspects,
        "total_s": total_s,
        "timeline": timeline,
        "voice": {"used": voice_on, "voice": voice_name, "speed": speed,
                  "clips": len([c for c in clips if c])},
        "captions_burned": bool(burn and cues),
        "local_only": local_only,
        "checks": checks,
        "assets": {"media": [m["rel"] for m in media_used], "flux": flux_count},
    }
    with open(os.path.join(out_dir_work, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(_readme(item, result, settings, timeline, media_used, flux_count, local_only))

    # 8. Keep the files and attach them.
    report(95, "Saving the video")
    out_dir = paths.data("outputs", "video", content_id)
    names = [ASPECTS[a]["file"] for a in aspects] + ["poster.jpg", "preview-480p.mp4", "captions.srt", "voiceover.txt", "README.md"]
    if voice_on:
        names.append("voiceover.wav")
    check()
    _publish_files(out_dir_work, out_dir, [n for n in names if os.path.exists(os.path.join(out_dir_work, n))])
    attached = _attach(content_id, aspects, out_dir, os.path.exists(os.path.join(out_dir, "poster.jpg")), total_s)
    result["files"] = attached
    result["render_seconds"] = round(time.monotonic() - started, 1)
    report(100, "The video is ready")
    _set_status(content_id, state="done", percent=100, message="The video is ready", error=None, error_code=None,
                finished_at=_now_iso(), files=attached)
    return result
