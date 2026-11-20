"""Formatting in community reports, business items and comments (Jim, 2026-11-15/20): bold, italic, lists and web
addresses, stored as Markdown marks (the toolbar adds them), never HTML. A link always shows its full address, so a
printed copy keeps it: a word made a link by hand ([text](https://…)) shows as "text (https://…)". An address gets a
QR code only when marked, "https://… [QR]" (the toolbar's QR button): the address shows once, in the text, and its QR in
a row after the text (on screen, a link too). Text from before reads the same, bare web addresses included (no QR).
"""
from __future__ import annotations

import html
import re

import jinja2
import markdown
import segno
from markdown.extensions import Extension
from markupsafe import Markup

URL = re.compile(r"(?<![\[(<\"'\w/])(https?://[^\s<>\"')\]]+[^\s<>\"')\].,;:!?])([ \t]*\[QR\])?", re.IGNORECASE)
LINK = re.compile(r'<a href="([^"]*)"(?: title="([^"]*)")?>(.*?)</a>', re.DOTALL)
# A mention (the picker types it): "@[Jane D](n0call)", the name as it was typed and the member's id (a lowercase callsign
# or name with hyphens). Anything else after "@[" stays text; the id is checked here, before it goes in a link.
MENTION = re.compile(r"(?<!\w)@\[([^\[\]\n]{1,60})\]\(([a-z0-9-]{1,40})\)")
SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)


def qr_svg(url: str) -> Markup:
    """The address as a QR code, inline SVG (scales in the PDF; no image file, nothing the page's policy blocks)."""
    svg = segno.make(url, error="m").svg_inline(scale=2, border=2, omitsize=True, svgclass="qr", title=f"QR code: {url}")
    return Markup(svg.replace("<svg ", '<svg role="img" ', 1))


class _Only(Extension):
    """Only what the toolbar makes (paragraphs, lists, bold, italic, links): an indented old line or one starting with
    "#" stays text, never a code block or a heading."""
    def extendMarkdown(self, md):
        for name in ("code", "hashheader", "setextheader", "hr", "quote", "indent", "fenced_code_block", "html_block", "reference"):
            if name in md.parser.blockprocessors:
                md.parser.blockprocessors.deregister(name)
        for name in ("backtick", "image_link", "image_reference", "reference", "short_reference", "autolink", "automail", "html", "entity"):
            if name in md.inlinePatterns:
                md.inlinePatterns.deregister(name)


LIST_LINE = re.compile(r"^(?:[-*+]|\d+\.)\s")
EMPTY_ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*$")


def _lists_apart(text: str) -> str:
    """A blank line where typed text meets a list (Markdown starts a list only after one; members type it right under
    a line), so "Built a mast\n- one\n- two\nDone" is a line, a list and a line. A list item with nothing in it
    (Enter after the last one, "4.") is dropped: it would show as a stray "4." under the list."""
    out = []
    for line in text.split("\n"):
        if EMPTY_ITEM.match(line):
            continue
        if out and out[-1].strip() and bool(LIST_LINE.match(line)) != bool(LIST_LINE.match(out[-1])):
            out.append("")
        out.append(line)
    return "\n".join(out)


def mentions(text: str) -> set[str]:
    """The member ids mentioned in the text."""
    return {m.group(2) for m in MENTION.finditer(text or "")}


def render(text: str, pdf: bool = False, names=None) -> Markup:
    """The text with its formatting, as safe HTML. HTML typed in is shown as text; links only to http(s) or mailto.
    `names` (a Club or a snapshot View: .known_name(id)) gives a mention its member's shown name, as a pill like a
    report's; an id that is no member's shows the typed name as plain text."""
    pills = []

    def pill(m: re.Match) -> str:  # a private-use token Markdown leaves alone, swapped for the pill after it
        name = names.known_name(m.group(2)) if names else None
        pills.append(html.escape(m.group(1)) if name is None else (f'<span class="who">{html.escape(name)}</span>' if pdf else
                     f'<a class="who" href="/members/{m.group(2)}">{html.escape(name)}</a>'))
        return f"\ue000{len(pills) - 1}\ue001"
    text = MENTION.sub(pill, (text or "").replace("\ue000", "").replace("\ue001", ""))
    text = URL.sub(lambda m: f"[{m.group(1)}]({m.group(1)}{' \"qr\"' if m.group(2) else ''})", text or "")  # "[QR]" after one: a code
    out = markdown.markdown(html.escape(_lists_apart(text), quote=False), extensions=["sane_lists", "nl2br", _Only()], output_format="html")

    codes = []  # the item's QR codes, in a row after its text: inline, they break the sentence apart

    def link(m: re.Match) -> str:
        href, label = html.unescape(m.group(1)), m.group(3)
        if not SAFE_HREF.match(href):
            return label  # javascript: and the like: just the words
        h = html.escape(href)
        try:  # a QR only for an address a code can hold (a very long one stays a plain link)
            svg = qr_svg(href) if m.group(2) == "qr" and href.lower().startswith("http") and len(href) <= 1000 and len(codes) < 6 else None
        except Exception:  # noqa: BLE001 - segno's DataOverflowError and the like: never take the page down
            svg = None
        if svg:
            codes.append(f'<span class="qrcode">{svg}</span>' if pdf else  # the address shows once, in the text
                         f'<a class="qrcode" href="{h}" rel="noopener nofollow" target="_blank" aria-label="QR code for {h}">{svg}</a>')
        a = h if pdf else f'<a href="{h}" rel="noopener nofollow" target="_blank">{h}</a>'  # always the address itself
        return a if html.unescape(label) == href else f"{label} ({a})"
    out = LINK.sub(link, out)
    out = re.sub("\ue000(\\d+)\ue001", lambda m: pills[int(m.group(1))], out)
    if out.startswith("<p>") and out.endswith("</p>") and out.count("<p>") == 1:
        out = out[3:-4]  # one plain line stays inline (a list item, a heading)
    if codes:
        out += f'<span class="qrs">{"".join(codes)}</span>'
    return Markup(out)


def plain(text: str) -> str:
    """The text without its marks, for emails, titles and search: "[the form](https://…)" → "the form (https://…)"."""
    t = re.sub(r"(https?://\S+)[ \t]*\[QR\]", r"\1", MENTION.sub(r"\1", text or ""), flags=re.IGNORECASE)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?: \"qr\")?\)", lambda m: m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", t)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    return re.sub(r"(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*])", r"\2", t)


@jinja2.pass_context
def rich_filter(ctx, text, pdf=False):
    """The templates' |rich: mentions take their names from the minutes' View (`v`) when the page has one, else the club."""
    v = ctx.get("v")
    return render(text, pdf, v if hasattr(v, "known_name") else ctx.get("club"))


def title(text: str) -> str:
    """An item's first line, plain: its title on cards, Home, search and the dialog's heading (the rest is its body)."""
    return plain((text or "").split("\n", 1)[0]).strip()
