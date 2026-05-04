"""Generate PNG icons and screenshots for the SunSide PWA.

Run once after pulling: ``python scripts/generate_pwa_assets.py``

Produces in ``assets/``:
- icon-192.png, icon-512.png (any-purpose)
- icon-maskable-192.png, icon-maskable-512.png (maskable, 80%-safe-zone)
- apple-touch-icon-180.png (iOS home screen)
- screenshot-narrow.png (1080x1920, mobile)
- screenshot-wide.png (1920x1080, desktop)

Requires Pillow: ``pip install pillow``
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

NIGHT = (11, 29, 58)        # #0b1d3a
SUN   = (245, 179, 1)       # #f5b301
ACCENT = (242, 183, 5)      # #f2b705
LIGHT_BG = (244, 246, 248)  # #f4f6f8
DARK_BG = (11, 15, 23)      # #0b0f17
TEXT_LIGHT = (21, 25, 35)   # #151923
TEXT_DARK = (230, 233, 240) # #e6e9f0
MUTED_LIGHT = (100, 112, 129)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _draw_sun(draw: ImageDraw.ImageDraw, cx: int, cy: int, core_r: int, ray_len: int, ray_w: int) -> None:
    draw.ellipse((cx - core_r, cy - core_r, cx + core_r, cy + core_r), fill=SUN)
    inner = core_r + ray_w
    outer = inner + ray_len
    for i in range(8):
        angle = math.radians(i * 45)
        x1 = cx + inner * math.cos(angle)
        y1 = cy + inner * math.sin(angle)
        x2 = cx + outer * math.cos(angle)
        y2 = cy + outer * math.sin(angle)
        draw.line((x1, y1, x2, y2), fill=SUN, width=ray_w)


def _rounded_rect(size: int, radius_ratio: float, fill) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * radius_ratio)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=fill)
    return img


def make_icon(size: int, *, maskable: bool) -> Image.Image:
    if maskable:
        # maskable: full-bleed background, content in inner 80% safe zone
        img = Image.new("RGBA", (size, size), NIGHT + (255,))
        scale = size / 512
        d = ImageDraw.Draw(img)
        cx = cy = size // 2
        _draw_sun(d, cx, cy, core_r=int(128 * scale), ray_len=int(40 * scale), ray_w=int(26 * scale))
        return img

    # any-purpose: rounded square + classic sun
    img = _rounded_rect(size, 96 / 512, NIGHT + (255,))
    d = ImageDraw.Draw(img)
    scale = size / 512
    cx = cy = size // 2
    _draw_sun(d, cx, cy, core_r=int(96 * scale), ray_len=int(56 * scale), ray_w=int(22 * scale))
    return img


def make_apple_touch(size: int = 180) -> Image.Image:
    # iOS masks to its own rounded square, so fill bleed and use any-purpose look
    img = Image.new("RGBA", (size, size), NIGHT + (255,))
    d = ImageDraw.Draw(img)
    scale = size / 512
    cx = cy = size // 2
    _draw_sun(d, cx, cy, core_r=int(110 * scale), ray_len=int(50 * scale), ray_w=int(24 * scale))
    return img


def _shadow_card(img: Image.Image, box, radius: int, fill, border=None) -> None:
    d = ImageDraw.Draw(img)
    if border:
        d.rounded_rectangle(box, radius=radius, fill=fill, outline=border, width=2)
    else:
        d.rounded_rectangle(box, radius=radius, fill=fill)


def make_screenshot_narrow() -> Image.Image:
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), LIGHT_BG)
    d = ImageDraw.Draw(img)

    title_font = _load_font(64)
    h2_font = _load_font(40)
    body_font = _load_font(30)
    big_font = _load_font(180)
    pct_font = _load_font(56)
    small_font = _load_font(24)

    # Header
    d.text((48, 96), "SunSide", fill=TEXT_LIGHT, font=title_font)
    d.text((48, 176), "Sitzseite fuer Sonne und Schatten", fill=MUTED_LIGHT, font=body_font)

    # Recommendation card (hero)
    card = (48, 260, W - 48, 720)
    _shadow_card(img, card, 16, (255, 255, 255), border=(215, 222, 232))
    d.rectangle((card[0], card[1], card[2], card[1] + 8), fill=ACCENT)
    d.text((96, card[1] + 60), "EMPFEHLUNG", fill=MUTED_LIGHT, font=small_font)
    d.text((96, card[1] + 100), "LINKS", fill=TEXT_LIGHT, font=big_font)
    d.text((96, card[1] + 320), "78 % im Schatten", fill=TEXT_LIGHT, font=pct_font)

    # Metrics row
    metric_w = (W - 48 * 2 - 24 * 2) // 3
    metrics = [
        ("INTERVALL", "1.2 km"),
        ("SEGMENTE", "187"),
        ("SONNENHOEHE", "32 deg"),
    ]
    for i, (lbl, val) in enumerate(metrics):
        x0 = 48 + i * (metric_w + 24)
        box = (x0, 760, x0 + metric_w, 920)
        _shadow_card(img, box, 12, (255, 255, 255), border=(215, 222, 232))
        d.text((x0 + 24, 790), lbl, fill=MUTED_LIGHT, font=small_font)
        d.text((x0 + 24, 830), val, fill=TEXT_LIGHT, font=h2_font)

    # Map placeholder
    map_box = (48, 960, W - 48, 1700)
    _shadow_card(img, map_box, 16, (228, 234, 240), border=(215, 222, 232))
    # Fake route polyline
    pts = [(120, 1620), (260, 1500), (430, 1410), (610, 1280), (780, 1180), (940, 1040)]
    for a, b in zip(pts, pts[1:]):
        d.line([a, b], fill=(37, 99, 235), width=10)
    for x, y in pts:
        d.ellipse((x - 10, y - 10, x + 10, y + 10), fill=(232, 93, 4))
    d.text((96, 1000), "Route", fill=TEXT_LIGHT, font=h2_font)

    # Footer
    d.text((48, 1780), "Offline-faehig - keine Tracker - kein Login", fill=MUTED_LIGHT, font=body_font)
    return img


def make_screenshot_wide() -> Image.Image:
    W, H = 1920, 1080
    img = Image.new("RGB", (W, H), LIGHT_BG)
    d = ImageDraw.Draw(img)

    title_font = _load_font(48)
    h2_font = _load_font(34)
    body_font = _load_font(24)
    big_font = _load_font(140)
    pct_font = _load_font(44)
    small_font = _load_font(20)

    # Left control panel
    panel = (48, 48, 580, H - 48)
    _shadow_card(img, panel, 12, (255, 255, 255), border=(215, 222, 232))
    d.text((80, 80), "SunSide", fill=TEXT_LIGHT, font=title_font)
    d.text((80, 144), "Sitzseite fuer Sonne und Schatten", fill=MUTED_LIGHT, font=body_font)
    sections = [
        ("Reise", "Zug - OSM-Gleisroute"),
        ("Start", "Berlin Hbf"),
        ("Ziel", "Muenchen Hbf"),
        ("Abfahrt", "08:42"),
        ("Praeferenz", "Schatten"),
    ]
    y = 220
    for label, value in sections:
        d.text((80, y), label.upper(), fill=MUTED_LIGHT, font=small_font)
        d.text((80, y + 30), value, fill=TEXT_LIGHT, font=h2_font)
        y += 100
    # Primary button
    d.rounded_rectangle((80, H - 160, 548, H - 96), radius=10, fill=ACCENT)
    d.text((250, H - 148), "Berechnen", fill=(20, 18, 10), font=h2_font)

    # Right workbench
    # Recommendation hero
    rec = (620, 48, W - 48, 360)
    _shadow_card(img, rec, 12, (255, 255, 255), border=(215, 222, 232))
    d.rectangle((rec[0], rec[1], rec[2], rec[1] + 6), fill=ACCENT)
    d.text((660, 90), "EMPFEHLUNG", fill=MUTED_LIGHT, font=small_font)
    d.text((660, 130), "LINKS", fill=TEXT_LIGHT, font=big_font)
    d.text((660, 290), "78 % im Schatten", fill=TEXT_LIGHT, font=pct_font)

    # Metrics
    metric_w = (W - 620 - 48 - 16 * 5) // 6
    metrics = [
        ("INTERVALL", "1.2 km"),
        ("SEGMENTE", "187"),
        ("BEWOELKUNG", "23 %"),
        ("SONNENHOEHE", "32 deg"),
        ("TUNNEL", "4 %"),
        ("GELAENDE", "11 %"),
    ]
    for i, (lbl, val) in enumerate(metrics):
        x0 = 620 + i * (metric_w + 16)
        box = (x0, 392, x0 + metric_w, 510)
        _shadow_card(img, box, 10, (255, 255, 255), border=(215, 222, 232))
        d.text((x0 + 18, 414), lbl, fill=MUTED_LIGHT, font=small_font)
        d.text((x0 + 18, 444), val, fill=TEXT_LIGHT, font=h2_font)

    # Map
    map_box = (620, 540, W - 48, H - 48)
    _shadow_card(img, map_box, 12, (228, 234, 240), border=(215, 222, 232))
    d.text((660, 564), "Route", fill=TEXT_LIGHT, font=h2_font)
    pts = [(700, 980), (820, 920), (980, 860), (1180, 800), (1380, 760), (1580, 700), (1780, 660)]
    for a, b in zip(pts, pts[1:]):
        d.line([a, b], fill=(37, 99, 235), width=8)
    for x, y in pts:
        d.ellipse((x - 8, y - 8, x + 8, y + 8), fill=(232, 93, 4))

    return img


def main() -> None:
    outputs = {
        "icon-192.png": make_icon(192, maskable=False),
        "icon-512.png": make_icon(512, maskable=False),
        "icon-maskable-192.png": make_icon(192, maskable=True),
        "icon-maskable-512.png": make_icon(512, maskable=True),
        "apple-touch-icon-180.png": make_apple_touch(180),
        "screenshot-narrow.png": make_screenshot_narrow(),
        "screenshot-wide.png": make_screenshot_wide(),
    }
    for name, img in outputs.items():
        path = ASSETS / name
        img.save(path, optimize=True)
        print(f"wrote {path.relative_to(ROOT)}  ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
