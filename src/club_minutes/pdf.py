"""The minutes as a PDF, for the web app and the daily job alike. Importing web would start the whole app, so this has
its own small template environment: only the filters and globals minutes_doc.html uses."""
import datetime as dt
import re
from pathlib import Path

import jinja2
from markupsafe import Markup, escape

from . import branding, corrections, pictures, richtext, theme
from .club import DATES, show_date

HERE = Path(__file__).parent
URL_RE = re.compile(r"(https?://[^\s<]+[^\s<.,)])")


def linkify(text: str) -> Markup:
    return Markup(URL_RE.sub(r'<a href="\1">\1</a>', str(escape(text))))


def render_pdf(archive, m, rp_id: str | None) -> tuple[str, bytes]:
    """(file name, PDF bytes) for submitted minutes: 15-oct-26-minutes.pdf, or -v2.pdf for a corrected version.
    rp_id: the site's address (club.yaml auth: rp_id), for the app's own links in the minutes."""
    from weasyprint import HTML
    club = archive.club
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "templates"), autoescape=True)
    env.filters.update(
        linkify=linkify, rich=lambda t, pdf=False: richtext.render(t, pdf), plain=richtext.plain, longdate=lambda d: show_date(d, "document"),
        ts=lambda t: dt.datetime.fromtimestamp(t).strftime(DATES["list"]) if t else "never",
        abs_url=lambda u: f"https://{rp_id}{u}" if u.startswith("/") and rp_id else u,
        by_last_name=lambda ids: sorted(ids, key=lambda i: (club.members[i].last, club.members[i].first) if i in club.members else (i, "")))
    env.globals.update(
        club=club, pdf=True, module=club.has, app_name=lambda: branding.name(club),
        theme_light=lambda: "; ".join(f"{k}: {v}" for k, v in theme.palette(**branding.colours())[0].items()),
        brand=lambda kind="logo", pdf=False: branding.path(kind).as_uri() if pdf else f"/brand/{branding.FILES[kind]}?v={branding.stamp()}",
        logo_class=lambda: f"logo bg-{branding.backing()}", picture_src=pictures.src)
    html = env.get_template("minutes_doc.html").render(m=m, current=archive.by_slug.get(m.slug, m))
    return corrections.pdf_name(m), HTML(string=html, base_url=str(HERE)).write_pdf()
