"""Archive access: loads meetings from disk; full-text search, stats, guest tracking."""
from __future__ import annotations

import datetime as dt
import difflib
import html
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from .club import MEETINGS, Club
from .meeting import Meeting


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


SEARCH_WORDS = 12  # words of a search that count: enough for any real search, and a cap on the work one can cause


# Common English given names and their nicknames, one group per line. Used only for the first names of people on
# the roster: with a Richard on it, "Richard", "Rich", "Rick" and "Dick" find each other; without one, nothing changes.
NICKNAMES = """richard rich rick ricky dick | robert rob bob bobby robbie bert | william bill billy will willy liam
james jim jimmy jamie | john jack johnny jon | jonathan jon jonny | joseph joe joey | michael mike mikey mick
thomas tom tommy | charles charlie chuck | edward ed eddie ted ned | theodore ted teddy theo | david dave davey
daniel dan danny | steven steve stevie | stephen steve stevie | christopher chris kit | anthony tony
andrew andy drew | kenneth ken kenny | ronald ron ronnie | donald don donnie | gerald gerry jerry | gregory greg
lawrence larry | leonard len lenny leo | raymond ray | timothy tim timmy | matthew matt | patrick pat paddy
peter pete | samuel sam sammy | benjamin ben benny | nicholas nick nicky | alexander alex al sandy | albert al bert
alan al | alfred al alf fred | frederick fred freddy | francis frank fran | franklin frank | henry hank harry
harold harry hal | walter walt wally | eugene gene | douglas doug | phillip phil | philip phil | jeffrey jeff
geoffrey geoff jeff | terrence terry | terence terry | jerome jerry | russell russ | stanley stan | norman norm
vincent vince vinny | leroy roy | louis lou | lewis lou | arthur art artie | maxwell max | zachary zach zack
jacob jake | joshua josh | nathan nate | nathaniel nate nat | herbert herb bert | gilbert gil
elizabeth liz beth betty betsy eliza | margaret maggie peggy meg | katherine kate kathy katie | catherine cathy kate katie
kathleen kathy kate | patricia pat patty trish tricia | susan sue suzy susie | deborah deb debbie | rebecca becky becca
jennifer jen jenny | jessica jess jessie | christine chris chrissy tina | christina chris tina | barbara barb
dorothy dot dottie | virginia ginny | victoria vicky tori | cynthia cindy | pamela pam | sandra sandy | theresa terry tess
judith judy | alexandra alex sandy | samantha sam | abigail abby | jacqueline jackie | nancy nan"""
NICK_GROUPS = [set(g.split()) for g in NICKNAMES.replace("\n", " | ").split("|")]
# Nicknames that are everyday words in minutes ("will", a "bill" to pay, "don't" read as "don", an antenna "jack"):
# never added for someone else's name. A member's own name always counts, and Other spellings can add any of them.
COMMON_WORDS = {"will", "bill", "don", "art", "frank", "ray", "sue", "max", "jack", "kit", "dot", "drew", "norm", "herb"}


def spellings(club: Club) -> dict[str, set[str]]:
    """Each member's first and last name with their other spellings from the roster (aliases) and, for the first
    name, its common nicknames (NICKNAMES), lowercase: word -> every spelling of that name. An alias goes with
    whichever of first or last it's closer to."""
    out: dict[str, set[str]] = {}
    sim = lambda a, b: difflib.SequenceMatcher(None, a, b).ratio()
    for m in club.members.values():
        first, last = m.first.lower(), m.last.lower()
        groups = ({first} | {n for g in NICK_GROUPS if first in g for n in g} - (COMMON_WORDS - {first}), {last})
        for a in (w.lower() for al in m.aliases for w in re.findall(r"\w+", al)):
            groups[sim(a, first) < sim(a, last)].add(a)
        for g in groups:
            for w in g:
                if w:
                    out.setdefault(w, {w}).update(g)
    return out


class Archive:
    def __init__(self, root: Path = MEETINGS, club: Club | None = None):
        self.root = root
        self.club = club or Club()
        self.reload()

    def reload(self) -> None:
        paths = sorted(self.root.glob("*/minutes.md"), reverse=True)
        self.meetings: list[Meeting] = [self._canonicalize(Meeting.load(p)) for p in paths]
        self.by_slug = {m.slug: m for m in self.meetings}
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute("create virtual table docs using fts5(slug unindexed, section unindexed, member unindexed, text, tokenize='porter')")
        rows = [
            (m.slug, sec, it.member or "", it.text)
            for m in self.meetings for sec, items in m.sections.items() for it in items
        ]
        rows += [(m.slug, "Guests", "", f"{g['name']} {g.get('callsign', '')} {g.get('notes', '')}")
                 for m in self.meetings for g in m.guests]
        self.db.executemany("insert into docs values (?,?,?,?)", rows)
        self.db.execute("create virtual table vocab using fts5vocab(docs, 'row')")

    def _canonicalize(self, m: Meeting) -> Meeting:
        """Map ids stored under a member's old callsign to their current one."""
        c = self.club.canonical
        m.in_person = [c(x) for x in m.in_person]
        m.remote = [c(x) for x in m.remote]
        for items in m.sections.values():
            for it in items:
                if it.member:
                    it.member = c(it.member)
        return m

    def search(self, q: str, limit: int = 100) -> list[dict]:
        """Every word (or another spelling of a member's name from the roster); if that finds nothing, close
        spellings of the words in the minutes (a typo, or a spelling nobody has added to the roster)."""
        terms = [t.lower() for t in re.findall(r"\w+", q)][:SEARCH_WORDS]
        if not terms:
            return []
        names = spellings(self.club)
        found = self._match([(t, names.get(t, set())) for t in terms], limit)
        if not found:
            vocab = [w for (w,) in self.db.execute("select term from vocab")]
            found = self._match([(t, names.get(t, set()) | set(difflib.get_close_matches(t, vocab, n=3, cutoff=0.8)))
                                 for t in terms], limit)
        return found

    def _match(self, alts: list[tuple[str, set[str]]], limit: int) -> list[dict]:
        """Rows with, for each (typed word, other spellings), the typed word as the start of a word, or one of the
        other spellings as a whole word ("Al" for Alan must not find "all")."""
        fts = " AND ".join("(" + " OR ".join([f'"{t}"*'] + [f'"{w}"' for w in sorted(a - {t})]) + ")" for t, a in alts)
        cur = self.db.execute(
            "select slug, section, member, snippet(docs, 3, char(1), char(2), ' … ', 24) "
            "from docs where docs match ? order by slug desc limit ?", (fts, limit))
        # the text is escaped (it's typed by members); only the highlight marks are HTML
        mark = lambda t: html.escape(t).replace("\x01", "<mark>").replace("\x02", "</mark>")
        return [{"slug": s, "section": sec, "member": mem, "snippet": mark(snip)} for s, sec, mem, snip in cur]

    def member_history(self, mid: str) -> dict:
        present = [(m, "in person" if mid in m.in_person else "online")
                   for m in self.meetings if mid in m.in_person or mid in m.remote]
        reports = [(m, it) for m in self.meetings for it in m.items("Community Reports") if it.member == mid]
        return {"present": present, "reports": reports}

    def stats(self, meetings: list[Meeting] | None = None) -> dict:
        """Attendance totals over `meetings` (default: all), newest first."""
        meetings = self.meetings if meetings is None else meetings
        rows = [{"meeting": m, "in_person": len(m.in_person), "remote": len(m.remote),
                 "guests": len(m.guests) + m.visitors, "total": m.attendance + len(m.guests) + m.visitors}
                for m in meetings]
        per_member = defaultdict(lambda: {"in_person": 0, "remote": 0, "last": None})
        for m in meetings:
            for mid in m.in_person:
                per_member[mid]["in_person"] += 1
            for mid in m.remote:
                per_member[mid]["remote"] += 1
            for mid in m.in_person + m.remote:
                if per_member[mid]["last"] is None:
                    per_member[mid]["last"] = m.date  # meetings are newest first
        n = len(meetings) or 1
        members = sorted(
            ({"id": k, "name": self.club.name(k), **v, "total": v["in_person"] + v["remote"],
              "rate": (v["in_person"] + v["remote"]) / n} for k, v in per_member.items()),
            key=lambda r: -r["total"])
        avg = sum(r["total"] for r in rows) / n
        return {"rows": rows, "members": members, "avg": avg, "count": len(meetings)}

    def guests(self) -> list[dict]:
        """Group guest visits by callsign, else by normalized name. Flag guests who later became members."""
        groups: dict[str, dict] = {}
        callsign_of = {_norm(g["name"]): g["callsign"]
                       for m in self.meetings for g in m.guests if g.get("callsign")}
        for m in sorted(self.meetings, key=lambda m: m.date):
            for g in m.guests:
                nk = _norm(g["name"])
                key = g.get("callsign") or callsign_of.get(nk) or nk
                grp = groups.setdefault(key, {"name": g["name"], "callsign": None, "visits": []})
                grp["callsign"] = grp["callsign"] or g.get("callsign")
                grp["visits"].append((m.date, g.get("notes", "")))
        for grp in groups.values():
            grp["member"] = self._member_for_guest(grp)
            if grp["member"]:  # the roster's member-since date, else their first meeting as a member
                since = self.club.members[grp["member"]].member_since
                try:
                    grp["joined"] = dt.date.fromisoformat(since) if since else None
                except ValueError:
                    grp["joined"] = None
                grp["joined"] = grp["joined"] or min((m.date for m in self.meetings
                                                      if grp["member"] in m.in_person + m.remote), default=None)
        return sorted(groups.values(), key=lambda g: (-len(g["visits"]), g["name"]))

    def _member_for_guest(self, grp: dict) -> str | None:
        for m in self.club.members.values():
            if grp["callsign"] and m.callsign == grp["callsign"]:
                return m.id
            lasts = [m.last] + m.aliases
            if any(_norm(f"{m.first}{l}") == _norm(grp["name"]) for l in lasts):
                return m.id
        return None
