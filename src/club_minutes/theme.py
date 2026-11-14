"""The club's colours (admin mode → Admin → Site → Colours): one main colour, and optionally the top bar's.

The pages use about 20 colour variables (style.css), each in a light and a dark version. Rather than ask an admin for
40 colours, the palette here is made from one: it keeps the *shape* of a hand-tuned palette (the lightness of each
step: page, card, border, text, link, button, top bar) and gives it the picked colour's hue and strength. Fed the
colour that palette was tuned from, it gives that palette back exactly. Every pair of text and background is then
checked for readability (WCAG contrast) and darkened or lightened until it passes, so no pick makes pages unreadable.
Semantic colours (danger, warnings, charts, attendance: in person green, online blue) never change, so they
mean the same whatever colour a club picks.
"""
from __future__ import annotations

import colorsys
import re

# The hand-tuned palette the others are shaped from, and the colour it was tuned from (its links' colour).
SHAPE_FROM = "#4f6f2a"
LIGHT = {"--bg": "#f5f6f0", "--surface-2": "#f3f5ee", "--surface-3": "#e9eee0", "--border": "#e1e4d8",
         "--border-strong": "#cdd2c1", "--text": "#1b1f17", "--muted": "#5f6656", "--faint": "#858c7b",
         "--accent": "#4f6f2a", "--accent-strong": "#2f3d1c", "--accent-soft": "#e6efd9", "--accent-text": "#2f5a1f",
         "--btn-bg": "#2f3d1c", "--btn-hover": "#445a28", "--nav-bg": "#2b3a19", "--nav-text": "#eef4e2",
         "--focus": "rgba(91, 122, 47, .28)", "--sheet-h": "#c3d69b"}
DARK = {"--bg": "#111410", "--surface": "#1a1e17", "--surface-2": "#20251d", "--surface-3": "#2a3125",
        "--border": "#2e3529", "--border-strong": "#414a39", "--text": "#e5eadc", "--muted": "#a2ab96", "--faint": "#7d8673",
        "--accent": "#9cc469", "--accent-strong": "#b5d98a", "--accent-soft": "#263219", "--accent-text": "#b5d98a",
        "--btn-bg": "#9cc469", "--btn-text": "#12160d", "--btn-hover": "#b2d688", "--nav-bg": "#161b12", "--nav-text": "#e2ead6",
        "--focus": "rgba(156, 196, 105, .3)", "--sheet-h": "#34442180"}
DEFAULT = "#2d5f9a"  # the app's own colour: a blue (a club picks its own on Site)

# (text, background, least contrast): each checked after recolouring; the second is moved until it passes.
# 4.5 is WCAG's for body text; 3 for large text and the parts of controls.
LIGHT_PAIRS = [("--accent-text", "--accent-soft", 4.5), ("--btn-text", "--btn-bg", 4.5), ("--nav-text", "--nav-bg", 4.5),
               ("--text", "--sheet-h", 4.5), ("--muted", "--bg", 4.5)]
LIGHT_TEXT = [("--accent-text", "#ffffff", 4.5), ("--accent", "#ffffff", 3.0)]  # link colours on a white card
DARK_PAIRS = [("--btn-text", "--btn-bg", 4.5), ("--nav-text", "--nav-bg", 4.5), ("--muted", "--bg", 4.5)]
DARK_TEXT = [("--accent-text", "--surface", 4.5), ("--accent-text", "--accent-soft", 4.5), ("--accent", "--surface", 3.0)]


def valid(hex_colour: str | None) -> str | None:
    """"#rrggbb" (lowercase) if it is one, else None."""
    h = str(hex_colour or "").strip().lower()
    return h if re.fullmatch(r"#[0-9a-f]{6}", h) else None


def _rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in (r, g, b))


def _luminance(h: str) -> float:
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in _rgb(h[:7])]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _recolour(value: str, dh: float, sat: float) -> str:
    """One palette value with the hue turned by dh and the strength scaled by sat; its lightness kept."""
    m = re.fullmatch(r"rgba\((\d+), (\d+), (\d+), ([.\d]+)\)", value)
    hexpart, alpha = (_hex(*(int(x) / 255 for x in m.groups()[:3])), m.group(4)) if m else (value[:7], value[7:])
    hue, light, s = colorsys.rgb_to_hls(*_rgb(hexpart))
    out = _hex(*colorsys.hls_to_rgb((hue + dh) % 1, light, min(1.0, s * sat)))
    if m:
        r, g, b = (int(out[i:i + 2], 16) for i in (1, 3, 5))
        return f"rgba({r}, {g}, {b}, {alpha})"
    return out + alpha


def _nudge(colour: str, darker: bool) -> str:
    hue, light, s = colorsys.rgb_to_hls(*_rgb(colour[:7]))
    return _hex(*colorsys.hls_to_rgb(hue, max(0.0, min(1.0, light + (-0.02 if darker else 0.02))), s)) + colour[7:]


def _fix(p: dict, text: str, background: str, least: float, notes: set, move: str) -> None:
    """Move p[move] (the background, or the text when it's on a fixed colour) lighter or darker, whichever reaches
    the contrast in fewer steps (a pale bar behind white text has to go darker, however pale it starts)."""
    def steps(darker: bool):
        q = dict(p)
        for n in range(60):
            if contrast(q.get(text, text)[:7], q.get(background, background)[:7]) >= least:
                return n, q[move]
            q[move] = _nudge(q[move], darker)
        return 99, None
    n, colour = min(steps(True), steps(False), key=lambda x: x[0])
    if n and colour:
        p[move] = colour
        notes.add("Some shades were made lighter or darker so text stays readable.")


def palette(main: str = DEFAULT, bar: str | None = None) -> tuple[dict, dict, list[str]]:
    """(light, dark, notes): the CSS variables for a main colour (and the top bar's, if one is picked)."""
    main = valid(main) or DEFAULT
    h0, _, s0 = colorsys.rgb_to_hls(*_rgb(SHAPE_FROM))
    h1, _, s1 = colorsys.rgb_to_hls(*_rgb(main))
    dh, sat = h1 - h0, min(1.6, s1 / s0)  # a much stronger colour than the shape's would tint the page too much
    light = {k: _recolour(v, dh, sat) for k, v in LIGHT.items()}
    dark = {k: _recolour(v, dh, sat) for k, v in DARK.items()}
    light["--btn-text"] = "#ffffff"
    notes: set[str] = set()
    if valid(bar):
        light["--nav-bg"] = valid(bar)  # the exact colour picked, unless white text on it isn't readable (below)
        # dark mode: the same colour dimmed (its hue and most of its strength), so a purple bar is a dark purple there
        hb, lb, sb = colorsys.rgb_to_hls(*_rgb(valid(bar)))
        dark["--nav-bg"] = _hex(*colorsys.hls_to_rgb(hb, min(lb, 0.22), sb * 0.85))
    for p, pairs, texts in ((light, LIGHT_PAIRS, LIGHT_TEXT), (dark, DARK_PAIRS, DARK_TEXT)):
        for text, background, least in pairs:
            _fix(p, text, background, least, notes, move=background)
        for text, background, least in texts:
            _fix(p, text, background, least, notes, move=text)
    return light, dark, sorted(notes)


def css(main: str = DEFAULT, bar: str | None = None) -> str:
    """The stylesheet that sets the palette (served at /brand/theme.css, after style.css)."""
    light, dark = palette(main, bar)[:2]
    block = lambda p: "; ".join(f"{k}: {v}" for k, v in p.items())
    return (f":root {{ {block(light)}; }}\n:root[data-theme=\"dark\"] {{ {block(dark)}; }}\n"
            f"@media (prefers-color-scheme: dark) {{ :root:not([data-theme=\"light\"]) {{ {block(dark)}; }} }}\n")


def from_logo(path) -> list[str]:
    """Up to four colours from the logo to offer as one-click picks: its most used ones that are a colour (not
    white, black, grey or see-through), most used first."""
    from PIL import Image
    try:
        img = Image.open(path).convert("RGBA")
    except OSError:
        return []
    img.thumbnail((96, 96))
    opaque = Image.new("RGB", img.size, (255, 255, 255))
    opaque.paste(img, mask=img.split()[3])
    out = []
    for count, rgb in sorted(opaque.quantize(12).convert("RGB").getcolors(96 * 96) or [], reverse=True):
        _hue, light, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
        h = _hex(*(c / 255 for c in rgb))
        if 0.1 < light < 0.9 and s > 0.2 and all(contrast(h, o) > 1.4 for o in out):
            out.append(h)
    return out[:4]
