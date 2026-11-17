"""Correcting minutes already in the archive (REQUIREMENTS FR-28), and marking imported minutes reviewed (FR-33).

A correction never changes the minutes in place: the current minutes.md is kept beside it as minutes.v1.md (then
minutes.v2.md…), and the new text becomes the next version, with who corrected it, when, why and whether members were
told in its front matter (Meeting.corrections). Old Business stays the text read that night: a correction doesn't touch
the tracked business items or the chow schedule. Search and member histories read only the current version.
"""
from __future__ import annotations

import difflib
import fcntl
import json
import re
import shutil
import threading
import time

from markupsafe import Markup, escape

from .club import show_date
from .console import email_status, reply_address, send_to_members
from .meeting import Item, Meeting
from .store import Archive

REASON_MAX = 300
_saving = threading.Lock()  # two corrections saved at once can't both become version 2


def may_correct(club, m: Meeting, mid: str | None, admin: bool = False) -> bool:
    """The meeting's leader or recorder, any board member (anyone holding an office: the "correct" permission), or an
    admin in admin mode. Only minutes in the archive: a draft is still the console's."""
    if m.status == "draft" or not mid and not admin:
        return False
    return admin or club.may(mid, "correct") or mid in (m.leader, m.recorder)


def version_path(archive: Archive, slug: str, v: int):
    return archive.root / slug / f"minutes.v{v}.md"


def load_version(archive: Archive, slug: str, v: int) -> Meeting | None:
    """An earlier version of these minutes (1 up to the current one less one), or None."""
    m = archive.by_slug.get(slug)
    if not m or not 1 <= v < m.version:
        return None
    p = version_path(archive, slug, v)
    return archive._canonicalize(Meeting.load(p)) if p.exists() else None


def correct(archive: Archive, slug: str, by: str, reason: str, version: int, new: Meeting, notice: bool,
            private: bool = False) -> Meeting:
    """Save `new` (attendance, guests and sections) as the next version of these minutes, keeping the current one as
    minutes.v{n}.md. version: the one the editor was opened on (if someone corrected them meanwhile, refused). Reviews
    imported minutes too: a correction is a review that fixed something."""
    reason = " ".join(reason.split())[:REASON_MAX]
    if not reason:
        raise ValueError("Say why, in a line: members see it with the minutes.")
    with _saving:
        m = archive.by_slug[slug]
        if m.status == "draft":
            raise ValueError("These minutes are still open in the console.")
        if m.version != version:
            raise ValueError(f"Someone corrected these minutes while you were editing (now version {m.version}). "
                             "Open Correct these minutes again to start from theirs.")
        if not changes(m, new, archive.club):
            raise ValueError("Nothing was changed.")
        d = archive.root / slug
        shutil.copy2(d / "minutes.md", version_path(archive, slug, m.version))
        m.in_person, m.remote, m.guests, m.visitors = new.in_person, new.remote, new.guests, new.visitors
        m.sections = new.sections
        m.version += 1
        m.corrections = [*m.corrections, {"version": m.version, "at": int(time.time()), "by": by, "reason": reason, "notice": notice}
                         | ({"private": True} if private else {})]
        if m.status == "imported" and not m.reviewed:
            m.reviewed = {"by": by, "at": int(time.time())}
        try:
            m.save(archive.root)
        finally:
            archive.reload()  # m is the archive's own: if the save failed, back to what's on disk
        return archive.by_slug[slug]


def mark_reviewed(archive: Archive, slug: str, by: str) -> Meeting:
    """Imported minutes someone read over and found right: drops "not yet reviewed"; still version 1, still imported."""
    with _saving:
        m = archive.by_slug[slug]
        if m.status != "imported":
            raise ValueError("Only imported minutes are reviewed.")
        if not m.reviewed:
            m.reviewed = {"by": by, "at": int(time.time())}
            m.save(archive.root)
            archive.reload()
        return archive.by_slug[slug]


def _short(text: str, n: int = 140) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


LISTS = ("In person", "Online", "Guests")  # compared as sets of names: their order means nothing


def _parts(m: Meeting, club) -> dict[str, list[str]]:
    guest = lambda g: " ".join(filter(None, [g["name"], g.get("callsign"), "(online)" if g.get("online") else "",
                                             f"– {g['notes']}" if g.get("notes") else ""]))
    return {"In person": sorted(club.name(x) for x in m.in_person), "Online": sorted(club.name(x) for x in m.remote),
            "Guests": sorted(guest(g) for g in m.guests), "Visitors": [str(m.visitors)] if m.visitors else [],
            **{sec: [i.shown for i in items] for sec, items in m.sections.items()}}


def diff(old: Meeting, new: Meeting, club) -> list[tuple[str, list[tuple[str, str | None, str | None]]]]:
    """Each part a correction changed, with its rows side by side: ("same", a, a), ("changed", a, b), ("removed", a,
    None), ("added", None, b). Attendance and guests are names: one there and not the other is removed and added."""
    a_parts, b_parts, out = _parts(old, club), _parts(new, club), []
    for part in dict.fromkeys([*a_parts, *b_parts]):
        a, b = a_parts.get(part, []), b_parts.get(part, [])
        if a == b:
            continue
        rows = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                rows += [("same", x, x) for x in a[i1:i2]]
            elif op == "replace" and i2 - i1 == j2 - j1 and part not in LISTS:
                rows += [("changed", x, y) for x, y in zip(a[i1:i2], b[j1:j2], strict=True)]
            else:
                rows += [("removed", x, None) for x in a[i1:i2]] + [("added", None, y) for y in b[j1:j2]]
        out.append((part, rows))
    return out


def words(a: str, b: str) -> tuple[Markup, Markup]:
    """A changed line both ways, the words that differ marked: <del> in the old, <ins> in the new (escaped text)."""
    ta, tb = re.findall(r"\w+|\s+|[^\w\s]", a), re.findall(r"\w+|\s+|[^\w\s]", b)
    old, new = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        x, y = escape("".join(ta[i1:i2])), escape("".join(tb[j1:j2]))
        old.append(x if op == "equal" else Markup(f"<del>{x}</del>") if x else "")
        new.append(y if op == "equal" else Markup(f"<ins>{y}</ins>") if y else "")
    return Markup("").join(old), Markup("").join(new)


def _was(a: str, b: str) -> str:
    """The words of `a` that `b` replaced, when one short stretch changed ("Oct 20"); else "" (just "reworded")."""
    ta, tb = re.findall(r"\w+|\s+|[^\w\s]", a), re.findall(r"\w+|\s+|[^\w\s]", b)
    parts = [(i1, i2) for op, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes() if op != "equal"]
    gone = " ".join("".join(ta[parts[0][0]:parts[0][1]]).split()) if len(parts) == 1 else ""  # one stretch only, else "reworded"
    return gone if len(gone) <= 40 else ""


def changes(old: Meeting, new: Meeting, club, private: bool = False) -> list[str]:
    """What a correction changed, a line each, for the notice email. Never the text of a removed line (a correction may
    be made to take something out); a changed line shows its new text and the few words it replaced, or with `private`
    only its new text. Attendance shows names."""
    out = []
    for part, rows in diff(old, new, club):
        if part in ("In person", "Online", "Visitors"):
            out += [f"{part}: " + (f"{a} → {b}" if k == "changed" else f"removed {a}" if k == "removed" else f"added {b}")
                    for k, a, b in rows if k != "same"]
            continue
        for k, a, b in rows:
            if k == "changed":
                was = "" if private else _was(a, b)
                out.append(f'{part}: "{_short(b)}"' + (f' (was "{was}")' if was else " (reworded)"))
            elif k == "added":
                out.append(f'{part}: added "{_short(b)}"')
        if n := sum(k == "removed" for k, *_ in rows):
            out.append(f"{part}: {n} line{'s' if n > 1 else ''} removed")
    return out


def hidden_version(m: Meeting, v: int) -> bool:
    """Version v was replaced by a correction ticked "This removes private details": only those who may correct see it."""
    return any(c.get("private") and c.get("version") == v + 1 for c in m.corrections)


def pdf_name(m: Meeting) -> str:
    """15-oct-26-minutes.pdf for version 1, then 15-oct-26-minutes-v2.pdf and so on."""
    return f"{m.date.day}-{m.date:%b}-{m.date:%y}-minutes{f'-v{m.version}' if m.version > 1 else ''}.pdf".lower()


def retry_pending(archive: Archive, make_pdf, slug: str | None = None, after: int = 600) -> int:
    """Send again what console.unsent lists (every meeting, or one): the minutes email, or the current version's
    corrected-minutes email; a notice for a version since replaced is dropped. make_pdf(meeting) → (name, bytes).
    Each still goes once (emailed*.json). Returns how many were tried."""
    from .console import email_minutes, unsent
    n = 0
    for s in [slug] if slug else [p.name for p in archive.root.iterdir() if p.is_dir()]:
        m = archive.by_slug.get(s)
        if not m:
            continue
        with open(archive.root / s / ".send.lock", "w") as lock:
            try:  # one retry of a meeting at a time (two clicks, or a click and the daily job): the other skips it
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            for name in unsent(archive, s, after):
                info = json.loads((archive.root / s / f"{name}.pending").read_text())
                if name == "emailed.json" and m.status == "draft":  # never submitted: nothing to email
                    (archive.root / s / f"{name}.pending").unlink(missing_ok=True)
                    continue
                if name == "emailed.json":
                    email_minutes(archive, s, lambda m=m: make_pdf(m), info["url"])
                elif name == f"emailed.v{m.version}.json":
                    email_notice(archive, s, info["what"], lambda m=m: make_pdf(m), info["url"])
                else:  # replaced by a later correction, which has its own email
                    (archive.root / s / f"{name}.pending").unlink(missing_ok=True)
                    continue
                n += 1
    return n


def email_notice(archive: Archive, slug: str, what_changed: list[str], make_pdf, url: str) -> dict:
    """The "corrected minutes" email for the current version, to the same members, by the same choice of link or PDF,
    as the minutes themselves. Once per version (emailed.v{n}.json)."""
    m = archive.by_slug[slug]
    name = f"emailed.v{m.version}.json"
    if (done := email_status(archive, slug, name)) and not done.get("error"):
        return done
    club, c = archive.club, m.corrected
    who = club.members.get(c["by"])
    date = show_date(m.date, "document")
    changed = "\n".join(f"- {x}" for x in what_changed[:30]) + (f"\n- and {len(what_changed) - 30} more" if len(what_changed) > 30 else "")
    body = (f"The minutes of {date} have been corrected (version {m.version}) by {who.name if who else 'an officer'}:\n"
            f"{c['reason']}\n\nWhat changed:\n{changed}\n\nThe corrected minutes: {url} (the PDF is attached)\n\n"
            f"To get a link instead of the PDF, or stop these emails: {url.split('/meetings/')[0]}/profile/email\n\n73,\n{club.label}")
    return send_to_members(archive, slug, f"{club.label} meeting minutes, {date} (corrected)", body, " (the PDF is attached)",
                           make_pdf, reply_address(who), name)


# ---------------- the editor's form ----------------
ROWS_MAX = 200  # guests, or one section's items: far more than a meeting has, and a cap on what one request makes


def _posted(form, pattern: str) -> list[int]:
    """The row numbers the form sent for a list (rows can be added and removed in the browser), in order."""
    return sorted({int(x.group(1)) for k in form if (x := re.fullmatch(pattern, k))})[:ROWS_MAX]


def from_form(archive: Archive, m: Meeting, form) -> Meeting:
    """The minutes as the editor's form sends them back. Attendance is roster ids (a radio per member: absent, in
    person, online); guests a box each; each section's items a text box each, numbered from 0. An item keeps the tags
    it had (who reported, an event's date and calendar): they come from the saved minutes by the item's number, never
    from the browser; added items are numbered after the saved ones. Sections are these minutes' and the agenda's."""
    club = archive.club
    new = Meeting(date=m.date, status=m.status)
    rows = [mid for mid, _, _ in attendance_rows(archive, m)]
    for mid in [*m.in_person, *m.remote, *rows]:  # the recorded order first, so an unchanged list stays the same
        where = form.get(f"att-{mid}")
        if where == "in_person" and mid not in new.in_person:
            new.in_person.append(mid)
        elif where == "remote" and mid not in new.remote:
            new.remote.append(mid)
    for i in _posted(form, r"g(\d+)-name"):
        name = _text(form.get(f"g{i}-name"))[:120]
        if not name or club.is_redacted(name) or form.get(f"g{i}-rm"):
            continue
        g = {"name": name}
        for k in ("callsign", "notes"):
            if v := _text(form.get(f"g{i}-{k}"))[:200]:
                g[k] = v.upper() if k == "callsign" else v
        if form.get(f"g{i}-online"):
            g["online"] = True
        new.guests.append(g)
    try:
        new.visitors = max(0, min(999, int(form.get("visitors") or 0)))
    except ValueError:
        new.visitors = m.visitors
    for n, sec in enumerate(m.section_order(club.sections())):
        old = m.items(sec)
        items = []
        for j in _posted(form, rf"s{n}-(\d+)"):
            if not (text := _text(form.get(f"s{n}-{j}"))) or form.get(f"s{n}-{j}-rm"):
                continue
            if j < len(old):
                o = old[j]
                items.append(Item(text, o.member, o.when, o.until, o.cal))
            else:
                items.append(Item(text, club.speaker_of(text, new.in_person + new.remote) if sec == "Community Reports" else None))
        if items or sec in m.sections:
            new.sections[sec] = items
    return new


def _text(v) -> str:
    """A field as typed (two spaces after a full stop stay two), less line breaks: an item is one line in minutes.md."""
    return re.sub(r"\s*[\r\n]+\s*", " ", str(v or "")).strip()


def attendance_rows(archive: Archive, m: Meeting) -> list[tuple[str, str, str]]:
    """(id, name, where) for the editor, by last name: everyone recorded, and the rest of the roster who aren't Inactive
    or Silent Keys.
    Imported minutes can name someone the roster doesn't know (an id from an unmatched surname): kept, as recorded."""
    club = archive.club
    there = {**{x: "in_person" for x in m.in_person}, **{x: "remote" for x in m.remote}}
    ids = [*dict.fromkeys([*there, *(x for x, v in club.members.items() if v.status.lower() not in ("inactive", "silent key"))])]
    def key(x):
        v = club.members.get(x)
        return (v.last.lower(), v.first.lower()) if v else (x.lower(), "")
    return [(x, club.name(x), there.get(x, "")) for x in sorted(ids, key=key)]

