import argparse
import json
import os
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(prog="w8lbz-minutes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="import historical minutes (PDFs + *CORRECTED*.txt) into the archive")
    imp.add_argument("src", type=Path)
    sub.add_parser("roster", help="rebuild the roster cache (roster.json) from the roster (roster-sheet.json)")
    lk = sub.add_parser("login-link", help="print a one-time sign-in link for a member (admin recovery)")
    lk.add_argument("who", help="callsign, or the member's email")
    lk.add_argument("--base-url", help="defaults to https://<auth.rp_id>")
    rm = sub.add_parser("delete-meeting", help="delete one meeting's minutes, e.g. after a test (restart the service afterwards)")
    rm.add_argument("date", help="YYYY-MM-DD")
    ci = sub.add_parser("chow-import", help="one-time import of the Secretary's chow .xlsx (prints it; --write saves)")
    ci.add_argument("xlsx", type=Path)
    ci.add_argument("--order", required=True, help="rotation order: surnames, comma-separated")
    ci.add_argument("--next", help="who the rotation continues with (surname)")
    ci.add_argument("--year", type=int, default=2026, help="year for dates written like 5-Mar")
    ci.add_argument("--write", action="store_true", help="save it (refuses if a chow schedule already exists)")
    rem = sub.add_parser("reminders", help="daily: business item and chow reminders, and the meeting-day email")
    rem.add_argument("--dry-run", action="store_true", help="print what would be sent; send and record nothing")
    sub.add_parser("auto-update", help="nightly: install a newer release that needs no steps by hand, after a backup (if turned on)")
    sub.add_parser("ai-ask-about", help="ask the AI for 'ask about' prompts for each member's community report (also runs after each Submit)")
    bk = sub.add_parser("backup", help="an encrypted backup of the data folder, checked, kept and sent off the server")
    bk.add_argument("--if-due", action="store_true", help="only if the schedule on the Backups page says one is due (the hourly timer)")
    rs = sub.add_parser("restore", help="unpack a backup into an empty folder (asks for the passphrase)")
    rs.add_argument("file", type=Path)
    rs.add_argument("dest", type=Path)
    srv = sub.add_parser("serve", help="run the web UI")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.add_argument("--reload", action="store_true")
    a = ap.parse_args()

    if a.cmd == "import":
        from .importer import import_archive
        ms = import_archive(a.src)
        print(f"imported {len(ms)} meetings")
    elif a.cmd == "backup":
        from . import backup
        from .club import Club
        if a.if_due and not backup.due():
            return
        club = Club()
        st = backup.run(club, f"https://{club.auth.get('rp_id', 'localhost')}")
        print(json.dumps(st))
        if not st.get("ok"):
            raise SystemExit(1)
    elif a.cmd == "restore":
        import getpass
        from . import backup
        n = backup.restore(a.file.read_bytes(), os.environ.get("W8LBZ_BACKUP_PASSPHRASE") or getpass.getpass("Passphrase: "), a.dest)
        print(f"Restored {n} files into {a.dest}. Stop the app, move them into the data folder (chown minutes), start it.")
    elif a.cmd == "roster":
        from .roster import sync
        ms = sync()
        print(f"synced {len(ms)} members, {sum(m['officer'] for m in ms)} officers")
    elif a.cmd == "login-link":
        from .auth import Auth
        from .club import Club
        auth = Auth(Club())
        mid = a.who.lower() if a.who.lower() in auth.club.members else auth.member_by_email(a.who)
        if not mid or not auth.is_active(mid):
            raise SystemExit(f"No active member matches {a.who!r}")
        print(f"Sign-in link for {auth.club.name(mid)} (one use, 15 minutes):")
        print(auth.create_link(mid, base_url=a.base_url))
    elif a.cmd == "delete-meeting":
        import datetime as dt
        import shutil
        from .club import MEETINGS
        d = MEETINGS / dt.date.fromisoformat(a.date).isoformat()
        if not (d / "minutes.md").exists():
            raise SystemExit(f"No minutes for {a.date}")
        shutil.rmtree(d)
        from . import business
        s = business.load()
        if n := business.forget_meeting(s, d.name):
            print(f"Removed {n} business item(s) added at that meeting.")
        business.save(s)
        print(f"Deleted {d}. Restart the web service so it drops it from memory.")
    elif a.cmd == "reminders":
        import datetime as dt
        from . import auth, business, prefs
        from .club import Club
        club = Club()
        url = f"https://{club.auth.get('rp_id')}"
        today = dt.date.today()
        with business.locked():
            s = business.load()
            due = business.due_reminders(s, today, int(club.info.get("remind_days_before", 3)))
            for mid, items in due.items():
                m = club.members.get(mid)
                if not m or not m.email or m.status.lower() in ("inactive", "silent key") or prefs.wants(mid, "business") != "now":
                    continue
                subject, body = business.reminder_email(club, mid, items, today, url)
                if a.dry_run:
                    print(f"--- to {m.name} <{m.email}>: {subject}\n{body}\n")
                    continue
                try:
                    auth.send_email(m.email, subject, body, only_to=club.email_only_to())
                except Exception as e:  # one bad address shouldn't stop the others
                    print(f"couldn't email {m.name}: {e}")
                    continue
                business.mark_reminded(items)
                print(f"reminded {m.name} about {len(items)} item(s)")
            if not a.dry_run:
                business.save(s)
        # members who just reached the arrears line, to the Treasurer and the Board (when the Treasurer turned alerts on)
        from . import treasurer
        with treasurer.locked():
            ts = treasurer.load()
            alert = treasurer.arrears_alert(club, ts, today)
            if alert and a.dry_run:
                print(f"--- to {', '.join(alert[0])}: {alert[1]}\n{alert[2]}\n")
            elif alert:
                try:
                    auth.send_email(alert[0], alert[1], alert[2], only_to=club.email_only_to())
                    treasurer.save(ts)  # only once it went: a failed send tries again tomorrow
                    print(alert[1])
                except Exception as e:
                    print(f"couldn't send the dues alert: {e}")
        # chow reminders and the meeting-day email, each sent once even if this runs twice
        from .store import Archive
        from .club import MEETINGS
        extra = prefs.chow_reminders(club, today, url) + prefs.digest(club, Archive(MEETINGS, club), today, url)
        new, sent = prefs.unsent([k for _, k, _, _ in extra]), []
        for mid, key, subject, body in extra:
            if key not in new:
                continue
            m = club.members[mid]
            if a.dry_run:
                print(f"--- to {m.name} <{m.email}>: {subject}\n{body}\n")
                continue
            try:
                auth.send_email(m.email, subject, body, only_to=club.email_only_to())
            except Exception as e:  # one bad address shouldn't stop the others
                print(f"couldn't email {m.name}: {e}")
                continue
            sent.append(key)
            print(f"{key.split(':')[0]} to {m.name}")
        if sent:
            prefs.mark_sent(sent, today)
        if not due and not new:
            print("nothing to remind")
    elif a.cmd == "auto-update":
        from . import updates
        from .club import Club
        club = Club()
        print(updates.auto(club, f"https://{club.auth.get('rp_id', 'localhost')}"))
    elif a.cmd == "ai-ask-about":
        from . import console
        from .club import Club, MEETINGS
        from .store import Archive
        n = console.refresh_ask_about(Archive(MEETINGS, Club()))
        print(f"prompts for {n} members saved")
    elif a.cmd == "chow-import":
        from . import chow
        from .club import Club
        club = Club()
        s, problems = chow.import_xlsx(club, a.xlsx, [x.strip() for x in a.order.split(",")], "import", a.year)
        if a.next:
            s["next"] = chow.match_name(club, a.next) or problems.append(f"--next: no member matches {a.next!r}")
        for d, x in sorted(s["nights"].items()):
            print(d, club.name(x["member"]) if x["member"] else f"no chow ({x.get('note') or ''})")
        print("Rotation:", ", ".join(club.name(m) for m in s["order"]))
        print("Next up:", club.name(chow.next_up(club, s)))
        print("Not in the rotation yet:", ", ".join(club.name(m) for m in s["pending"]) or "nobody")
        for p in problems:
            print("CHECK:", p)
        if a.write:
            if chow._path().exists():
                raise SystemExit(f"{chow._path()} already exists; not overwriting it.")
            chow.save(s)
            chow._log("import", "import", file=a.xlsx.name, nights=len(s["nights"]))
            print(f"Saved {len(s['nights'])} nights to {chow._path()}")
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("w8lbz_minutes.web:app", host=a.host, port=a.port, reload=a.reload,
                    proxy_headers=True, forwarded_allow_ips="127.0.0.1")
