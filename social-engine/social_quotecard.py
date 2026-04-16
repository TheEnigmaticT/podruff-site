"""Quote-card generator for social posts.

Given (client, video frame, pull-quote, attribution), produces:
- A landscape (1200x630) and square (1080x1080) PNG
- Light and dark variants based on client brand
- Uploads to R2 if requested
- Creates Notion image-post row if requested

Design:
- Pure PIL compositing. No generative APIs.
- Client brand (palette, fonts, logos, wash) pulled from social_config.json.
- Aspect ratios chosen to satisfy Instagram's 0.75–1.91 range (1200x630 = 1.905).

Usage:
    from social_quotecard import render_card, create_post, load_brand, extract_candidate_frames

    brand = load_brand("crestway")
    path = render_card(brand, frame_path, quote, attribution, variant="light", aspect="landscape",
                       out_path=Path("card.png"))
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

try:
    import cv2
    import numpy as np
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

# ── Constants ────────────────────────────────────────────────────────
FFPROBE = "/opt/homebrew/bin/ffprobe"
FFMPEG = "/opt/homebrew/bin/ffmpeg"

# Aspect presets. 1200x630 chosen so aspect (1.905) stays inside
# Instagram's 0.75–1.91 range — 628 trips Zernio's IG validator.
ASPECTS = {
    "landscape": (1200, 630),
    "square": (1080, 1080),
}

_DEFAULT_CONFIG_PATH = Path("/Users/ct-mac-mini/dev/podruff-site/social-scheduler/social_config.json")
_DEFAULT_FONTS_CACHE = Path.home() / ".cache/social_quotecard/fonts"


# ── Brand ────────────────────────────────────────────────────────────

@dataclass
class Variant:
    """Color scheme for one variant (light or dark)."""
    bg: tuple           # RGB
    quote: tuple        # quote text color
    rule: tuple         # accent rule color
    attrib: tuple       # attribution color
    logo: Path          # path to the logo PNG suitable for this bg


@dataclass
class Brand:
    name: str
    fonts: dict          # {"serif_italic": <Path|str>, "sans": <Path|str>}
    light: Variant
    dark: Variant
    wash_color: tuple    # RGB for the per-photo color wash
    wash_alpha: float    # 0.0–1.0

    def variant(self, which: Literal["light", "dark"]) -> Variant:
        return self.light if which == "light" else self.dark


# ── Config loader ────────────────────────────────────────────────────

def _hex_to_rgb(s: str) -> tuple:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def load_brand(client: str, config_path: Path = _DEFAULT_CONFIG_PATH) -> Brand:
    """Read per-client brand block from social_config.json.

    Expected shape:
        clients.<name>.brand = {
            "fonts": { "serif_italic": "<path>", "sans": "<path>" },
            "light":  { "bg": "#...", "quote": "#...", "rule": "#...", "attrib": "#...", "logo": "<path>" },
            "dark":   { "bg": "#...", "quote": "#...", "rule": "#...", "attrib": "#...", "logo": "<path>" },
            "wash":   { "color": "#...", "alpha": 0.12 }
        }
    """
    cfg = json.loads(Path(config_path).read_text())
    block = cfg["clients"][client].get("brand")
    if not block:
        raise ValueError(f"No 'brand' block configured for client {client!r} in {config_path}")

    def _variant(v):
        return Variant(
            bg=_hex_to_rgb(v["bg"]),
            quote=_hex_to_rgb(v["quote"]),
            rule=_hex_to_rgb(v["rule"]),
            attrib=_hex_to_rgb(v["attrib"]),
            logo=_expand(v["logo"]),
        )

    fonts = {k: _expand(v) for k, v in block["fonts"].items()}
    wash = block.get("wash", {"color": "#888888", "alpha": 0.0})

    return Brand(
        name=client,
        fonts=fonts,
        light=_variant(block["light"]),
        dark=_variant(block["dark"]),
        wash_color=_hex_to_rgb(wash["color"]),
        wash_alpha=float(wash["alpha"]),
    )


# ── Font auto-download ───────────────────────────────────────────────

_GOOGLE_FONTS = {
    # Spelled as file basename → raw-TTF URL on google/fonts
    "Fraunces-Italic.ttf":
        "https://github.com/google/fonts/raw/main/ofl/fraunces/"
        "Fraunces-Italic%5BSOFT%2CWONK%2Copsz%2Cwght%5D.ttf",
    "Fraunces.ttf":
        "https://github.com/google/fonts/raw/main/ofl/fraunces/"
        "Fraunces%5BSOFT%2CWONK%2Copsz%2Cwght%5D.ttf",
    "SpaceGrotesk.ttf":
        "https://github.com/google/fonts/raw/main/ofl/spacegrotesk/"
        "SpaceGrotesk%5Bwght%5D.ttf",
    "Inter.ttf":
        "https://github.com/google/fonts/raw/main/ofl/inter/"
        "Inter%5Bopsz%2Cwght%5D.ttf",
    "Inter-Italic.ttf":
        "https://github.com/google/fonts/raw/main/ofl/inter/"
        "Inter-Italic%5Bopsz%2Cwght%5D.ttf",
}


def ensure_font(name_or_path) -> Path:
    """Resolve a font reference. If `name_or_path` points at an existing file, use it.
    Otherwise if it's a known Google Font basename, download to cache."""
    p = Path(os.path.expanduser(str(name_or_path)))
    if p.exists():
        return p
    key = p.name
    if key in _GOOGLE_FONTS:
        _DEFAULT_FONTS_CACHE.mkdir(parents=True, exist_ok=True)
        target = _DEFAULT_FONTS_CACHE / key
        if not target.exists():
            import urllib.request
            urllib.request.urlretrieve(_GOOGLE_FONTS[key], target)
        return target
    raise FileNotFoundError(f"Font not found and not a known preset: {name_or_path}")


# ── Frame extraction ─────────────────────────────────────────────────

def _duration_seconds(video_path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video_path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def extract_candidate_frames(video_path: Path, out_dir: Path, count: int = 3,
                             subtitle_trim: float = 0.80) -> list[Path]:
    """Extract `count` evenly-spaced frames from `video_path`.

    Returns paths in order of timestamp (earliest first).
    The caller reviews and picks the best-composed frame. `subtitle_trim`
    is a hint for downstream (not applied here — we keep full frame).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = _duration_seconds(video_path)
    paths = []
    for i in range(count):
        # Spread across middle 70% of the video to avoid title/outro frames.
        frac = 0.15 + (0.70 * i / max(count - 1, 1))
        t = dur * frac
        dst = out_dir / f"{video_path.stem}_t{int(frac*100):02d}.png"
        subprocess.run(
            [FFMPEG, "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
             "-vframes", "1", "-q:v", "2", str(dst)],
            check=True, capture_output=True,
        )
        paths.append(dst)
    return paths


# ── Face detection + auto-crop ──────────────────────────────────────

def _load_cascades():
    front = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    return front, profile


def detect_face_bbox(pil_image: Image.Image) -> Optional[tuple]:
    """Return largest face bounding box (x, y, w, h) in pixels, or None.
    Tries frontal then profile cascades."""
    if not _CV2_OK:
        return None
    arr = np.array(pil_image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    front, profile = _load_cascades()
    faces = front.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    if len(faces) == 0:
        faces = profile.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    if len(faces) == 0:
        return None
    # largest face
    x, y, w, h = max(faces, key=lambda b: b[2] * b[3])
    return (int(x), int(y), int(w), int(h))


def _frame_sharpness(pil_image: Image.Image) -> float:
    """Laplacian variance in the face region (or full image if no face) — higher = sharper."""
    if not _CV2_OK:
        return 0.0
    arr = np.array(pil_image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    bbox = detect_face_bbox(pil_image)
    if bbox is not None:
        x, y, w, h = bbox
        gray = gray[y:y + h, x:x + w]
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def score_frame(frame_path: Path, subtitle_trim: float = 0.65) -> float:
    """Score a frame for 'good portrait': face present + sharp + reasonable size.
    Higher is better. Returns -1 if no face found."""
    im = Image.open(frame_path).convert("RGB")
    W, H = im.size
    im = im.crop((0, 0, W, int(H * subtitle_trim)))
    bbox = detect_face_bbox(im)
    if bbox is None:
        return -1.0
    x, y, w, h = bbox
    sharpness = _frame_sharpness(im)
    # Reward moderate face size (not tiny, not filling)
    area_frac = (w * h) / (im.size[0] * im.size[1])
    size_bonus = 1.0 - abs(area_frac - 0.06) * 8  # peak around 6% of frame area
    return sharpness * max(0.1, size_bonus)


def pick_best_frame(frame_paths: list[Path], subtitle_trim: float = 0.65) -> Path:
    """Score all candidates, return the best one."""
    scored = [(score_frame(p, subtitle_trim), p) for p in frame_paths]
    scored.sort(reverse=True)
    return scored[0][1]


def auto_crop_params(frame_path: Path, subtitle_trim: float, target_w: int, target_h: int,
                      *, headroom_ratio: float = 0.7,
                      body_ratio: float = 2.2,
                      max_face_fraction: float = 0.50,
                      max_upscale: float = 1.5) -> dict:
    """From a frame + subtitle trim, compute x_bias / face_y_top / face_y_bottom
    so that the face is well-placed, not too large, and the output doesn't get
    upscaled into potato resolution.

    - headroom_ratio: fraction of face height to include as headroom above face
    - body_ratio: fraction of face height to include below face (shoulders+body)
    - max_face_fraction: cap — face height on output shouldn't exceed this fraction of target_h
    - max_upscale: cap the scale factor applied when fitting source to target (prevents blur)

    Falls back to conservative defaults if no face is detected.
    """
    im = Image.open(frame_path).convert("RGB")
    W, H = im.size
    trimmed = im.crop((0, 0, W, int(H * subtitle_trim)))
    W2, H2 = trimmed.size
    bbox = detect_face_bbox(trimmed)
    if bbox is None:
        return {"x_bias": 0.5, "face_y_top": 0.10, "face_y_bottom": 0.90}

    fx, fy, fw, fh = bbox
    fcx = fx + fw / 2

    # Source crop height must be ≥ fh / max_face_fraction (so face on output ≤ max_face_fraction)
    # AND ≥ target_h / max_upscale (so we don't upscale into potato mush)
    min_crop_h_face = fh / max_face_fraction
    min_crop_h_scale = target_h / max_upscale
    min_crop_h = max(min_crop_h_face, min_crop_h_scale)

    # Start with headroom + body proportion
    top_px = fy - headroom_ratio * fh
    bottom_px = fy + fh + body_ratio * fh
    crop_h = bottom_px - top_px

    if crop_h < min_crop_h:
        extra = (min_crop_h - crop_h) / 2
        top_px -= extra
        bottom_px += extra

    # Clamp to trimmed frame bounds, shifting to fit if needed
    top_px = int(top_px)
    bottom_px = int(bottom_px)
    if top_px < 0:
        bottom_px += -top_px
        top_px = 0
    if bottom_px > H2:
        top_px -= bottom_px - H2
        bottom_px = H2
        top_px = max(0, top_px)

    face_y_top = top_px / H2
    face_y_bottom = bottom_px / H2
    x_bias = max(0.0, min(1.0, fcx / W2))

    return {"x_bias": x_bias, "face_y_top": face_y_top, "face_y_bottom": face_y_bottom}


# ── Speaker prep (subtitle crop, face-region, aspect-fit, wash) ─────

def prep_speaker(frame_path: Path, tw: int, th: int, *,
                 subtitle_trim: float = 0.80,
                 face_y_top: float = 0.28,
                 face_y_bottom: float = 0.90,
                 x_bias: float = 0.5,
                 darken: float = 0.96,
                 saturation: float = 0.88,
                 wash_color: Optional[tuple] = None,
                 wash_alpha: float = 0.0) -> Image.Image:
    """Produce the speaker panel image at (tw, th).

    x_bias controls horizontal crop position when source width is cropped
    (0.0 = hard left, 0.5 = center, 1.0 = hard right). Useful when the speaker
    is not centered in the source frame.
    """
    im = Image.open(frame_path).convert("RGB")
    W, H = im.size
    im = im.crop((0, 0, W, int(H * subtitle_trim)))
    W, H = im.size
    im = im.crop((0, int(H * face_y_top), W, int(H * face_y_bottom)))
    W, H = im.size

    target_ratio = tw / th
    src_ratio = W / H
    if src_ratio < target_ratio:
        new_h = int(W / target_ratio)
        im = im.crop((0, 0, W, new_h))  # keep top (head stays up)
    else:
        new_w = int(H * target_ratio)
        left = int((W - new_w) * max(0.0, min(1.0, x_bias)))
        im = im.crop((left, 0, left + new_w, H))
    im = im.resize((tw, th), Image.LANCZOS)
    im = ImageEnhance.Brightness(im).enhance(darken)
    im = ImageEnhance.Color(im).enhance(saturation)
    if wash_color and wash_alpha > 0:
        tint = Image.new("RGB", im.size, wash_color)
        im = Image.blend(im, tint, wash_alpha)
    return im


# ── Text layout ──────────────────────────────────────────────────────

def _tokenize_with_highlight(text: str, highlight: Optional[str]):
    """Split text into (token, is_highlighted) pairs.

    `highlight` is a substring to emphasise in the accent color. Case-insensitive
    match on first occurrence. Pure-punctuation tokens get merged into the
    previous token (keeps the previous token's highlight state) so you don't
    get dangling " ." or " ," after a highlighted phrase."""
    if not highlight:
        tokens = [(w, False) for w in text.split()]
    else:
        lo = text.lower().find(highlight.lower())
        if lo < 0:
            tokens = [(w, False) for w in text.split()]
        else:
            tokens = []
            for w in text[:lo].split():
                tokens.append((w, False))
            for w in text[lo:lo+len(highlight)].split():
                tokens.append((w, True))
            for w in text[lo+len(highlight):].split():
                tokens.append((w, False))
    # Merge pure-punctuation tokens into the previous token (keep prev highlight).
    merged = []
    _punct = set(".,!?;:\u2014\u2013\"'")  # incl em/en dash
    for w, hl in tokens:
        if merged and all(c in _punct for c in w):
            pw, phl = merged[-1]
            merged[-1] = (pw + w, phl)
        else:
            merged.append((w, hl))
    return merged


def _wrap_tokens(draw, tokens, font, max_w):
    """Wrap [(word, is_hl)] tokens into lines of [(word, is_hl)]."""
    space_w = draw.textbbox((0, 0), " ", font=font)[2]
    lines = []
    cur = []
    cur_w = 0
    for word, hl in tokens:
        ww = draw.textbbox((0, 0), word, font=font)[2]
        needed = ww if not cur else cur_w + space_w + ww
        if needed > max_w and cur:
            lines.append(cur); cur = [(word, hl)]; cur_w = ww
        else:
            cur.append((word, hl)); cur_w = needed
    if cur:
        lines.append(cur)
    return lines


def _wrap_lines(draw, text, font, max_w):
    """Plain wrap — used only when there's no highlight."""
    lines = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            lines.append(""); continue
        cur = words[0]
        for w in words[1:]:
            probe = cur + " " + w
            if draw.textbbox((0, 0), probe, font=font)[2] <= max_w:
                cur = probe
            else:
                lines.append(cur); cur = w
        lines.append(cur)
    return lines


def _fit_font_tokens(draw, tokens, font_path, box_w, box_h, *,
                     max_size=52, min_size=26, line_spacing=1.2):
    """Fit a tokenized quote into the box. Returns (font, lines-of-tokens, line_h)."""
    for size in range(max_size, min_size - 1, -2):
        f = ImageFont.truetype(str(font_path), size)
        lines = _wrap_tokens(draw, tokens, f, box_w)
        a, d = f.getmetrics()
        lh = int((a + d) * line_spacing)
        if lh * len(lines) <= box_h:
            return f, lines, lh
    f = ImageFont.truetype(str(font_path), min_size)
    lines = _wrap_tokens(draw, tokens, f, box_w)
    a, d = f.getmetrics()
    return f, lines, int((a + d) * line_spacing)


def _draw_token_lines(draw, lines, font, default_color, accent_color, box, line_h):
    """Draw wrapped token lines. Highlighted tokens get accent_color."""
    bx1, by1, bx2, by2 = box
    th = line_h * len(lines)
    y = by1 + (by2 - by1 - th) // 2
    space_w = draw.textbbox((0, 0), " ", font=font)[2]
    for line in lines:
        x = bx1
        for i, (word, hl) in enumerate(line):
            color = accent_color if hl else default_color
            draw.text((x, y), word, fill=color, font=font)
            ww = draw.textbbox((0, 0), word, font=font)[2]
            x += ww + (space_w if i < len(line) - 1 else 0)
        y += line_h


def _fit_font(draw, text, font_path, box_w, box_h, *,
              max_size=52, min_size=26, line_spacing=1.2):
    for size in range(max_size, min_size - 1, -2):
        f = ImageFont.truetype(str(font_path), size)
        lines = _wrap_lines(draw, text, f, box_w)
        a, d = f.getmetrics()
        lh = int((a + d) * line_spacing)
        if lh * len(lines) <= box_h:
            return f, lines, lh
    f = ImageFont.truetype(str(font_path), min_size)
    lines = _wrap_lines(draw, text, f, box_w)
    a, d = f.getmetrics()
    return f, lines, int((a + d) * line_spacing)


def _draw_lines(draw, lines, font, color, box, line_h):
    bx1, by1, bx2, by2 = box
    th = line_h * len(lines)
    y = by1 + (by2 - by1 - th) // 2
    for line in lines:
        draw.text((bx1, y), line, fill=color, font=font)
        y += line_h


def _paste_logo(canvas, logo_path, pos, size_w):
    logo = Image.open(logo_path).convert("RGBA")
    lw, lh = logo.size
    logo = logo.resize((size_w, int(size_w * lh / lw)), Image.LANCZOS)
    canvas.paste(logo, pos, logo)


# ── Card renderer ────────────────────────────────────────────────────

def render_card(brand: Brand, frame_path: Path, quote: str, attribution: str, *,
                variant: Literal["light", "dark"] = "light",
                aspect: Literal["landscape", "square"] = "landscape",
                out_path: Path,
                logo_width: Optional[int] = None,
                highlight: Optional[str] = None,
                subtitle_trim: float = 0.80,
                face_y_top: float = 0.28,
                face_y_bottom: float = 0.90,
                x_bias: float = 0.5,
                ) -> Path:
    """Render a single card PNG. Returns out_path.

    Tunables:
      highlight — substring of `quote` to render in the accent color (first match, case-insensitive)
      subtitle_trim — fraction of source frame to KEEP from the top (trims burned-in subtitles)
      face_y_top / face_y_bottom — vertical band within the trimmed frame that contains head+shoulders
      x_bias — horizontal crop position (0.0 left .. 0.5 center .. 1.0 right)
    """
    W, H = ASPECTS[aspect]
    v = brand.variant(variant)
    canvas = Image.new("RGB", (W, H), v.bg)

    serif_italic = ensure_font(brand.fonts["serif_italic"])
    sans = ensure_font(brand.fonts["sans"])

    tokens = _tokenize_with_highlight(quote, highlight)
    speaker_kwargs = dict(subtitle_trim=subtitle_trim,
                          face_y_top=face_y_top, face_y_bottom=face_y_bottom,
                          x_bias=x_bias,
                          wash_color=brand.wash_color, wash_alpha=brand.wash_alpha)

    if aspect == "landscape":
        panel_w = 540
        speaker = prep_speaker(frame_path, panel_w, H, **speaker_kwargs)
        canvas.paste(speaker, (W - panel_w, 0))
        draw = ImageDraw.Draw(canvas)
        quote_box = (60, 100, 600, 410)
        qf, qlines, qlh = _fit_font_tokens(draw, tokens, serif_italic, 540, 310,
                                            max_size=44, min_size=26)
        _draw_token_lines(draw, qlines, qf, v.quote, v.rule, quote_box, qlh)
        draw.line([(60, 440), (160, 440)], fill=v.rule, width=2)
        af = ImageFont.truetype(str(sans), 20)
        a, d = af.getmetrics()
        alh = int((a + d) * 1.15)
        _draw_lines(draw, [attribution], af, v.attrib, (60, 456, 600, 492), alh)
        _paste_logo(canvas, v.logo, (60, H - 110), logo_width or 140)
    else:  # square
        panel_h = 540
        speaker = prep_speaker(frame_path, W, panel_h, **speaker_kwargs)
        canvas.paste(speaker, (0, 0))
        draw = ImageDraw.Draw(canvas)
        text_top = panel_h
        quote_box = (80, text_top + 50, W - 80, text_top + 300)
        qf, qlines, qlh = _fit_font_tokens(draw, tokens, serif_italic, W - 160, 250,
                                            max_size=52, min_size=30)
        _draw_token_lines(draw, qlines, qf, v.quote, v.rule, quote_box, qlh)
        draw.line([(80, text_top + 325), (220, text_top + 325)], fill=v.rule, width=2)
        af = ImageFont.truetype(str(sans), 24)
        a, d = af.getmetrics()
        alh = int((a + d) * 1.15)
        _draw_lines(draw, [attribution], af, v.attrib,
                    (80, text_top + 340, W - 80, text_top + 380), alh)
        _paste_logo(canvas, v.logo, (80, H - 120), logo_width or 160)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG", quality=95)
    return out_path


def render_pair(brand: Brand, frame_path: Path, quote: str, attribution: str, *,
                variant: Literal["light", "dark"],
                out_dir: Path,
                stem: str,
                logo_width: Optional[int] = None,
                highlight: Optional[str] = None,
                subtitle_trim: float = 0.80,
                face_y_top: float = 0.28,
                face_y_bottom: float = 0.90,
                x_bias: float = 0.5,
                ) -> dict[str, Path]:
    """Render landscape + square for a given variant. Returns {aspect: path}."""
    return {
        aspect: render_card(
            brand, frame_path, quote, attribution,
            variant=variant, aspect=aspect,
            out_path=Path(out_dir) / f"{stem}_{aspect}.png",
            logo_width=logo_width, highlight=highlight,
            subtitle_trim=subtitle_trim,
            face_y_top=face_y_top, face_y_bottom=face_y_bottom,
            x_bias=x_bias,
        )
        for aspect in ("landscape", "square")
    }


# ── CLI ──────────────────────────────────────────────────────────────

def _main():
    import argparse
    p = argparse.ArgumentParser(prog="social_quotecard",
        description="Generate quote cards from a client + video + pull-quote.")
    sub = p.add_subparsers(dest="cmd", required=True)

    # extract-frames
    ef = sub.add_parser("extract-frames", help="Extract candidate frames from a video")
    ef.add_argument("video", type=Path)
    ef.add_argument("--out", type=Path, required=True)
    ef.add_argument("--count", type=int, default=3)

    # pick-best-frame
    pb = sub.add_parser("pick-best-frame",
                        help="Extract N frames, score by face sharpness+size, print best path")
    pb.add_argument("video", type=Path)
    pb.add_argument("--out", type=Path, required=True)
    pb.add_argument("--count", type=int, default=5)
    pb.add_argument("--subtitle-trim", type=float, default=0.65)

    # render
    rn = sub.add_parser("render", help="Render a quote card pair (landscape + square)")
    rn.add_argument("--client", required=True, help="client name as in social_config.json")
    rn.add_argument("--frame", type=Path, required=True)
    rn.add_argument("--quote", required=True)
    rn.add_argument("--attrib", required=True)
    rn.add_argument("--variant", choices=["light", "dark"], default="light")
    rn.add_argument("--out-dir", type=Path, required=True)
    rn.add_argument("--stem", required=True)
    rn.add_argument("--logo-width", type=int, default=None)
    rn.add_argument("--highlight", default=None,
                    help="substring of --quote to render in the accent color")
    rn.add_argument("--subtitle-trim", type=float, default=0.80,
                    help="fraction of source frame height to keep from top (trims burned subs)")
    rn.add_argument("--face-y-top", type=float, default=0.28,
                    help="top of face-band within trimmed frame (0-1)")
    rn.add_argument("--face-y-bottom", type=float, default=0.90,
                    help="bottom of face-band within trimmed frame (0-1)")
    rn.add_argument("--x-bias", type=float, default=0.5,
                    help="horizontal crop position (0=left, 0.5=center, 1=right)")

    args = p.parse_args()

    if args.cmd == "extract-frames":
        paths = extract_candidate_frames(args.video, args.out, count=args.count)
        for pth in paths:
            print(pth)
    elif args.cmd == "pick-best-frame":
        paths = extract_candidate_frames(args.video, args.out, count=args.count)
        best = pick_best_frame(paths, subtitle_trim=args.subtitle_trim)
        print(best)
    elif args.cmd == "render":
        brand = load_brand(args.client)
        out = render_pair(brand, args.frame, args.quote, args.attrib,
                          variant=args.variant, out_dir=args.out_dir, stem=args.stem,
                          logo_width=args.logo_width, highlight=args.highlight,
                          subtitle_trim=args.subtitle_trim,
                          face_y_top=args.face_y_top, face_y_bottom=args.face_y_bottom,
                          x_bias=args.x_bias)
        for aspect, pth in out.items():
            print(f"{aspect}: {pth}")


if __name__ == "__main__":
    _main()
