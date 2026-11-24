"""What published minutes keep from the day they were submitted. The minutes show names, the agenda's order, the club's
name and links and its date formats, and all of those can change later; so each version carries a snapshot in its front
matter (Meeting.shown) that the page and the PDF read, and a draft has none and follows today's settings. Shape:

    names: {member id: {name, short, first, last, shown}}   everyone the minutes mention, @ mentions too (shown: what "names:" chose)
    sections: [section names in order]    chow: whether Upcoming Chow was on
    heading, label: the club's heading line and its label    links: [{label, url}] shown in the minutes
    dates: {document, list, day}: strftime formats
    logo: the logo kept (branding.keep_logo; "": the plain one)   backing: what went behind it   colours: {main, bar}
    layout: 1 for minutes made before 1.2 (set by the upgrade): their reports are drawn as 1.1 drew them;
        none for 1.2's (a line between community reports); 2 from 1.3 on: a line between the items of every section (View.lines)
"""
from __future__ import annotations

import datetime as dt
import re

from markupsafe import Markup

from . import branding, theme
from .club import DATES, show_date, unname
from .richtext import mentions

FORMATS = ("document", "list", "day")


def take(club, m) -> dict:
    """The snapshot of `m` as the club stands now."""
    ids = {*m.in_person, *m.remote, m.leader, m.recorder, (m.reviewed or {}).get("by"),
           *(c.get("by") for c in m.corrections), *(it.member for items in m.sections.values() for it in items),
           *(i for items in m.sections.values() for it in items for i in mentions(it.text) if club.known_name(i))} - {None, ""}
    return {"names": names(club, ids), "sections": m.section_order(club.sections()), "chow": club.has("chow"),
            "heading": heading(club),
            "label": club.label,
            "links": [{"label": ln.get("label", ""), "url": ln.get("url", "")} for ln in club.links if ln.get("minutes") is not False],
            "dates": {k: DATES[k] for k in FORMATS},
            "logo": branding.keep_logo(), "backing": branding.backing(), "colours": branding.colours(), "layout": 2}


def heading(club) -> str:
    return " · ".join(x for x in (club.info.get("callsign") if club.ham else None, club.info.get("name")) if x) or club.label


def names(club, ids) -> dict:
    out = {}
    for i in sorted(ids):
        mem = club.members.get(club.canonical(i))
        out[i] = {"name": club.name(i), "short": club.short(i), "first": mem.first if mem else "",
                  "last": mem.last if mem else i, "shown": club.shown_name(i)}
    return out


class View:
    """What _minutes.html asks for: the snapshot's answer when the minutes have one, else today's (drafts, the preview)."""

    def __init__(self, m, club):
        self.m, self.club, self.s = m, club, m.shown or {}

    def _n(self, mid):
        return (self.s.get("names") or {}).get(mid)

    def name(self, mid):
        return n["name"] if (n := self._n(mid)) else self.club.name(mid)

    def known_name(self, mid):  # for a mention in text (richtext.py)
        live = self.club.known_name(mid)  # never_record is checked now: a snapshot from before it never shows the name
        return n["shown"] if (n := self._n(mid)) and live != "" else live

    def member_url(self, mid):
        return self.club.member_url(mid)

    def logo(self, pdf):
        """The logo kept with these minutes (a file for the PDF), or None: today's (drafts, minutes from before 1.2's
        upgrade kept none)."""
        kid = self.s.get("logo")
        if kid == "":
            plain = branding.HERE / "static" / branding.FILES["logo"]
            return plain.as_uri() if pdf else "/static/logo.png"
        if not kid or not re.fullmatch(r"[0-9a-f]{16}", kid) or not (p := branding.kept_logo(kid)).exists():
            return None
        return p.as_uri() if pdf else f"/brand/kept/{kid}.png"

    @property
    def backing(self):
        b = self.s.get("backing")
        return b if b in ("circle", "square", "none") else None

    @property
    def colours_css(self):
        """The minutes' own colours, for them alone, or "" to follow the site's."""
        c = self.s.get("colours") or {}
        main = theme.valid(c.get("main"))
        return Markup(theme.css(main, theme.valid(c.get("bar")), " .minutes")) if main else ""  # our own CSS, from checked #rrggbb only

    @property
    def old(self):
        """Minutes from before 1.2: each report as typed (web addresses linked, no formatting), its short name after it."""
        return self.s.get("layout") == 1

    @property
    def lines(self):
        """A faint line between the items of every section (Jim, 2026-11-22): minutes made before it keep their look."""
        return not self.m.shown or self.s.get("layout") == 2

    def short(self, mid):
        return n["short"] if (n := self._n(mid)) else self.club.short(mid)

    def shown_name(self, mid):
        return n["shown"] if (n := self._n(mid)) else self.club.shown_name(mid)

    def by_last(self, ids):
        def key(i):
            if n := self._n(i):
                return n["last"], n["first"]
            mem = self.club.members.get(i)
            return (mem.last, mem.first) if mem else (i, "")
        return sorted(ids, key=key)

    def unnamed(self, text, mid):
        n = self._n(mid) if mid else None
        return unname(text, {n["name"], n["short"], n["first"]}) if n else self.club.unnamed(text, mid)

    def shown(self, item):
        return item.show((self.s.get("dates") or {}).get("day"))

    @property
    def sections(self):
        return self.s["sections"] if "sections" in self.s else self.m.section_order(self.club.sections())

    @property
    def chow(self):
        return self.s["chow"] if "chow" in self.s else self.club.has("chow")

    @property
    def heading(self):
        return self.s["heading"] if "heading" in self.s else heading(self.club)

    @property
    def label(self):
        return self.s.get("label") or self.club.label

    @property
    def links(self):
        return self.s["links"] if "links" in self.s else [ln for ln in self.club.links if ln.get("minutes") is not False]

    def longdate(self, d):
        f = (self.s.get("dates") or {}).get("document")
        return d.strftime(f) if f else show_date(d, "document")

    def ts(self, t):
        f = (self.s.get("dates") or {}).get("list") or DATES["list"]
        return dt.datetime.fromtimestamp(t).strftime(f) if t else "never"
