"""A meeting's minutes: Markdown file with YAML front matter.

The front matter holds structured data (date, attendance, guests, status).
The body holds one `## Section` per agenda section, each item a bullet.
Community report bullets may carry an invisible `<!-- member: id -->` tag, and Upcoming Events
a `<!-- when: 2026-11-21 -->` or `<!-- when: 2026-11-21T10:00 -->` tag (for the club calendar), and optionally
an end, `<!-- until: 2026-11-22 -->` (the last day) or `<!-- until: 2026-11-21T13:00 -->`.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# The built-in sections (each has something behind it: bills, reports, business tracking, the calendar, chow), in
# their usual order. A club's agenda (club.yaml meeting: sections:) picks from these and adds its own free-text ones.
SECTIONS = [
    "Bills and Communications",
    "Financial Report",
    "Community Reports",
    "Old Business",
    "New Business",
    "Winners",
    "Upcoming Chow",
    "Upcoming Events",
]

MEMBER_TAG = re.compile(r"\s*<!--\s*member:\s*([\w-]+)\s*-->")
WHEN_TAG = re.compile(r"\s*<!--\s*when:\s*(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?)\s*-->")
UNTIL_TAG = re.compile(r"\s*<!--\s*until:\s*(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?)\s*-->")
CAL_TAG = re.compile(r"\s*<!--\s*cal:\s*(community|planning)\s*-->")
EVENT_HOURS = 2  # how long a timed event with no end is taken to last


@dataclass
class Item:
    text: str
    member: str | None = None  # roster id, community reports only
    when: str | None = None  # Upcoming Events: "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM"
    until: str | None = None  # its end: the last day, or the end time (same formats)
    cal: str | None = None  # its calendar: "community" or "planning"; None for Club events

    def span(self) -> tuple[dt.datetime, dt.datetime, bool]:
        """(start, end, all_day). All-day: end is the last day (inclusive). Timed: end is the end time; with
        no end, EVENT_HOURS later; with only an end date, that day at the start's time."""
        start, timed = dt.datetime.fromisoformat(self.when), "T" in self.when
        end = dt.datetime.fromisoformat(self.until) if self.until else None
        if not timed:
            return start, end if end else start, True
        if not end:
            return start, start + dt.timedelta(hours=EVENT_HOURS), False
        return start, end if "T" in self.until else dt.datetime.combine(end.date(), start.time()), False

    @property
    def shown(self) -> str:
        """The text as readers see it, dates first: "Sat Nov 21, 10:00 AM – 1:00 PM: VE testing",
        "Sat Jun 27 – Sun Jun 28: Field Day" (dates in the club's format)."""
        from .club import DATES  # club.py imports this module"
        if not self.when:
            return self.text
        start, end, all_day = self.span()
        day, clock = DATES["day"], "%-I:%M %p"
        if all_day:
            when = start.strftime(day) + (end.strftime(" – " + day) if end.date() != start.date() else "")
        else:
            when = start.strftime(f"{day}, {clock}")
            if self.until:
                when += end.strftime(" – " + (clock if end.date() == start.date() else f"{day}, {clock}"))
        return f"{when}: {self.text}"


@dataclass
class Meeting:
    date: dt.date
    status: str = "draft"  # draft | imported | published
    version: int = 1
    leader: str | None = None
    recorder: str | None = None  # who took the minutes in the console; shown only when not the leader
    source: str | None = None  # original file name, if imported
    note: str | None = None  # e.g. import warnings for the reviewer
    in_person: list[str] = field(default_factory=list)
    remote: list[str] = field(default_factory=list)
    guests: list[dict] = field(default_factory=list)  # {name, callsign}
    visitors: int = 0  # guests counted but not named (e.g. family, minors)
    sections: dict[str, list[Item]] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.date.isoformat()

    @property
    def attendance(self) -> int:
        return len(self.in_person) + len(self.remote)

    def section_order(self, agenda: list[str]) -> list[str]:
        """The club's agenda, then any other section these minutes have (one later dropped from the agenda still shows)."""
        return agenda + [s for s, items in self.sections.items() if s not in agenda and items]

    def items(self, section: str) -> list[Item]:
        return self.sections.get(section, [])

    # ---- serialization ----
    def to_markdown(self) -> str:
        fm = {
            "date": self.date,
            "status": self.status,
            "version": self.version,
            "leader": self.leader,
            "recorder": self.recorder,
            "source": self.source,
            "note": self.note,
            "attendance": {"in_person": self.in_person, "remote": self.remote},
            "guests": self.guests,
            "visitors": self.visitors,
        }
        fm = {k: v for k, v in fm.items() if v not in (None, [], {}, 0)}
        out = ["---", yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip(), "---", ""]
        for name in self.sections:  # in the order they were made (the club's agenda), so nothing is dropped
            out.append(f"## {name}")
            for it in self.items(name):
                tag = (f" <!-- member: {it.member} -->" if it.member else "") + (f" <!-- when: {it.when} -->" if it.when else "") + (f" <!-- until: {it.until} -->" if it.until else "") + (f" <!-- cal: {it.cal} -->" if it.cal and it.when else "")
                out.append(f"- {it.text}{tag}")
            out.append("")
        return "\n".join(out)

    @classmethod
    def from_markdown(cls, text: str) -> Meeting:
        _, fm_text, body = text.split("---", 2)
        fm = yaml.safe_load(fm_text) or {}
        att = fm.get("attendance") or {}
        m = cls(
            date=fm["date"],
            status=fm.get("status", "draft"),
            version=fm.get("version", 1),
            leader=fm.get("leader"),
            recorder=fm.get("recorder"),
            source=fm.get("source"),
            note=fm.get("note"),
            in_person=att.get("in_person") or [],
            remote=att.get("remote") or [],
            guests=fm.get("guests") or [],
            visitors=fm.get("visitors", 0),
        )
        current = None
        for line in body.splitlines():
            if line.startswith("## "):
                current = line[3:].strip()
                m.sections.setdefault(current, [])
            elif current and line.startswith("- "):
                text = line[2:]
                tag = MEMBER_TAG.search(text)
                member = tag.group(1) if tag else None
                when, until, cal = WHEN_TAG.search(text), UNTIL_TAG.search(text), CAL_TAG.search(text)
                text = CAL_TAG.sub("", UNTIL_TAG.sub("", WHEN_TAG.sub("", MEMBER_TAG.sub("", text)))).strip()
                m.sections[current].append(Item(text, member, when.group(1) if when else None,
                                                until.group(1) if until and when else None, cal.group(1) if cal and when else None))
            elif current and line.strip() and m.sections[current]:
                # continuation line of the previous bullet
                m.sections[current][-1].text += " " + line.strip()
        return m

    def save(self, root: Path) -> Path:
        d = root / self.slug
        d.mkdir(parents=True, exist_ok=True)
        p = d / "minutes.md"
        p.write_text(self.to_markdown())
        return p

    @classmethod
    def load(cls, path: Path) -> Meeting:
        return cls.from_markdown(path.read_text())
