"""The minutes as a PDF, for the web app and the daily job alike. Importing web would start the whole app, so this has
its own small template environment: only the filters and globals minutes_doc.html uses."""
import datetime as dt
import os
import re
import threading
from pathlib import Path

import jinja2
from markupsafe import Markup, escape

from . import branding, corrections, pictures, richtext, snapshot, theme
from .club import DATA, DATES, show_date

HERE = Path(__file__).parent
URL_RE = re.compile(r"(https?://[^\s<]+[^\s<.,)])")


def linkify(text: str) -> Markup:
    return Markup(URL_RE.sub(r'<a href="\1">\1</a>', str(escape(text))))


def _env(club, rp_id: str | None) -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "templates"), autoescape=True)
    env.filters.update(
        linkify=linkify, rich=richtext.rich_filter, plain=richtext.plain, longdate=lambda d: show_date(d, "document"),
        ts=lambda t: dt.datetime.fromtimestamp(t).strftime(DATES["list"]) if t else "never",
        abs_url=lambda u: f"https://{rp_id}{u}" if u.startswith("/") and rp_id else u,
        by_last_name=lambda ids: sorted(ids, key=lambda i: (club.members[i].last, club.members[i].first) if i in club.members else (i, "")))
    env.globals.update(
        club=club, pdf=True, module=club.has, minutes_view=snapshot.View, app_name=lambda: branding.name(club),
        theme_light=lambda: "; ".join(f"{k}: {v}" for k, v in theme.palette(**branding.colours())[0].items()),
        brand=lambda kind="logo", pdf=False: branding.path(kind).as_uri() if pdf else f"/brand/{branding.FILES[kind]}?v={branding.stamp()}",
        logo_class=lambda: f"logo bg-{branding.backing()}", picture_src=pictures.src)
    return env


# "Page X of Y" in the bottom margin (so it can't change how many pages there are): only on a PDF of more than one page
PAGE_NUMBERS = '@page { @bottom-center { content: "Page " counter(page) " of " counter(pages); font: 9pt sans-serif; color: #666; } }'


def numbers_from() -> str:
    """The first day this app numbered PDF pages (pdf-pages-from, written the first time it's asked): minutes of
    meetings before it are never numbered, so minutes from before the update look as they always did."""
    p = DATA / "pdf-pages-from"
    try:
        return p.read_text().strip()
    except FileNotFoundError:
        p.parent.mkdir(parents=True, exist_ok=True)
        (tmp := p.with_suffix(".tmp")).write_text(today := dt.date.today().isoformat())
        tmp.replace(p)
        return today


def _write(html: str, numbers: bool = True) -> bytes:
    from weasyprint import CSS, HTML
    doc = HTML(string=html, base_url=str(HERE)).render()
    if numbers and len(doc.pages) > 1:
        doc = HTML(string=html, base_url=str(HERE)).render(stylesheets=[CSS(string=PAGE_NUMBERS)])
    return doc.write_pdf()


def render_pdf(archive, m, rp_id: str | None) -> tuple[str, bytes]:
    """(file name, PDF bytes) for submitted minutes: 15-oct-26-minutes.pdf, or -v2.pdf for a corrected version.
    rp_id: the site's address (club.yaml auth: rp_id), for the app's own links in the minutes."""
    html = _env(archive.club, rp_id).get_template("minutes_doc.html").render(m=m, current=archive.by_slug.get(m.slug, m))
    return corrections.pdf_name(m), _write(html, str(m.date) >= numbers_from())


def application(club, rec: dict, rp_id: str | None = None) -> bytes:
    """A membership application as a PDF: the club's name and logo, the answers, the signature and the sponsor's
    confirmation, and a block for the club's own part (applications.py)."""
    from . import applications
    def when(t):
        return dt.datetime.fromtimestamp(t).strftime(DATES["list"] + ", %-I:%M %p") if t else ""
    html = _env(club, rp_id).get_template("application_doc.html").render(
        a=rec, sponsor=club.name((rec.get("sponsor") or {}).get("id")), when=when, fees=applications.fees(rec))
    return _write(html)


def kept(archive, m, rp_id: str | None) -> tuple[str, bytes]:
    """render_pdf, kept: the first time a published version's PDF is made it is saved beside its minutes
    (minutes.v2.pdf for version 2), and that file is what's served from then on, so the PDF stays as it was that night.
    Each writer uses its own temporary file, so two processes making it at once is fine: one rename wins. Never
    served by path: callers have already checked who may see this version. Minutes without a snapshot aren't kept."""
    p = archive.root / m.slug / f"minutes.v{m.version}.pdf"
    if m.status != "draft" and m.shown:
        try:
            return corrections.pdf_name(m), p.read_bytes()
        except FileNotFoundError:
            pass
    name, data = render_pdf(archive, m, rp_id)
    if m.status != "draft" and m.shown:
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(p)
    return name, data
