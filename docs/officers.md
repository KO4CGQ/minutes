# Officers guide

For officers, minutes editors, roster keepers and the Treasurer: running the club from the app. Setup steps for admins sit alongside; the rest of the setup is in the [server admin guide](server-admin.md). Back to the [README](../README.md).

## Member profiles
Members edit their own email, phone, address, birthday and sharing choices at `/profile`. The app writes those cells to the roster sheet, values only, and never changes columns, dropdowns or formatting. A birthday shared with members, and each membership anniversary, shows at the top of the leader console's Attendance step at the first meeting on or after the day (a month-only birthday: the month's first meeting).
- **Setup:** share the roster sheet with the service account as **Editor**, and add `birthday` and `share` columns to the Member Roster tab (headers exactly; any position).
- Each change is logged in `profile-changes.jsonl` in the data directory, emailed to members whose title includes Secretary, and can be undone by any officer at `/roster/changes`.
- Officers see all contact details; other members see only what each member shares.
- **Roster keepers edit the roster** (Secretary and Treasurer by default, `roster_keepers:`; admins in admin mode): **Edit roster details** on a member's page (`/members/ID/edit`) changes name, class, status, email, phone, address, titles, aliases and the `is_officer`, `is_board`, `minutes_editor` and `calendar_editor` ticks, through the same path as members' own edits (values only, logged, undoable, emailed). It also adds Membership Log rows. Not birthday or sharing (the member's own choices).
- **Callsign changes** (same page, **New callsign**): the callsign is the member's id across the data folder, so a change renames it everywhere (`callsign.py`): current state (`chow.json`, `business.json`, `events.json`, `calendar-feeds.json`, `ask-about.json`, `email-prefs.json`, `settings.json`, `treasurer.json`, drafts' `console.json`), the minutes' front matter and report tags (never their text), the sign-in database and the picture. Logs (`*.jsonl`) keep the old id as history. It needs the backup passphrase (a backup is taken first and kept as `…-before-NEWCALL.tar.gz.enc`); the old call goes into `aliases` on the sheet, so `Club.canonical` maps it to the member (club.yaml's `admins:` and `email.only_to` included). Needs `aliases`, `minutes_editor` and `calendar_editor` columns on the sheet to change those.
- **Roster API** (`GET /api/roster`), for the club's other tools on the same server: active members' `callsign`, `name`, `email`, `officer` and `former` (earlier callsigns, so the bot can follow a callsign change), as JSON. Off until `W8LBZ_ROSTER_API_TOKEN=...` (a long random string) is in `/etc/w8lbz-minutes/env`; then it answers only requests with `Authorization: Bearer <token>` from the server itself or an address in `W8LBZ_ROSTER_API_ALLOW` (comma-separated; the Discord bot's container), and never through the web proxy. E.g. from the bot's container: `curl -H "Authorization: Bearer $TOKEN" http://<this server's LAN IP>:8000/api/roster`. Give the bot's container a fixed IP, and ideally limit port 8000 to it with the Proxmox firewall. Every call, allowed or not, is logged in `api-log.jsonl` in the data directory.
- **Offices** (`/offices`, linked from the Roster): the offices in club.yaml's `offices:` (a title is an officer's office with one seat; `{title: Trustee, officer: false, seats: 3}` is a board seat), who holds each, and who held them when, from the Membership Log's "Took office" / "Left office" rows (the office in the `note` column). An admin in admin mode changes a holder in one step: the app writes `Title(s)`, `is_officer` and `is_board` for both members (officer if any office they hold is an officer's, board if any is in the list; other titles are kept), adds the log rows, and emails the officers. "Add a past term" adds history only.
- **Email** and **Settings** tabs: which emails each member gets (`email-prefs.json`) and how the app shows things to them, e.g. the first day of the week (`settings.json`), both in the data directory.

## Club calendars
The app keeps the club calendars itself (`events.json` in the data folder), so nothing depends on Google: **Club events** (what the club runs or joins: meetings, nets, VE testing, fox hunts), **Planning** (officers' dates, like the annual meeting) and **Community** (what's on in the area). It adds chow nights, the minutes' dated Upcoming Events, the birthdays members share (never private ones, not even for officers), open business items' due dates (on Planning, as "Due: …") a **Holidays** calendar (US federal holidays, plus Christmas Eve and New Year's Eve; the list is `HOLIDAYS` in `calview.py`) and Ohio's election days on Community (`elections()` in `calview.py`). Both are worked out from their rules, so they need no updating unless a law changes (a new federal holiday, a new Ohio election date). The weekly meeting is put on Club events once, from `meeting_day` and `meeting_time`, as an ordinary repeating event: delete one date for a holiday, or move it.

**Calendar page** (`/calendar`, in the menu): an agenda (8 weeks at a time by default, or 3 months, 6 months or a year; earlier or later) or a month grid. Leaders and minutes editors, members ticked `calendar_editor` in the roster sheet (say, the net manager; add the column if it isn't there), and admins in admin mode **add, change and delete** events there: start, optional end (a time, or a last day for several days), an optional **time zone** (the club's by default; e.g. UTC for Field Day, shown in club time), where, notes, and **Repeats** (every day, every week, every 2 weeks, monthly on the 2nd Tuesday or the last Tuesday, monthly on several weeks like the 1st and 3rd Tuesday, monthly on a date, yearly on a date or on the 1st Thursday of January; optionally until a date). A repeating event can be changed or deleted for **this date only**, **this and following dates**, or **every date**. Events from the minutes are changed in the leader console. Every change is logged in `calendar-log.jsonl`; officers see it at **Calendar → Who changed what** (`/calendar/changes`).

**Import .ics** (Calendar → Import .ics, leaders): events from Google Calendar, Outlook, Apple or anything that exports an .ics file, into the calendar you pick. In Google Calendar: Settings → Import & export → Export (a .zip with one .ics per calendar). Importing the same file again updates what it imported, so nothing is doubled; an event already on the calendar with the same title on the same day is left alone; events that ended before a chosen date are skipped. Repeating events come in when they repeat daily, weekly (several weekdays become one event per day), every few weeks, monthly (on a date, a weekday, or several weeks like the 1st and 3rd Tuesday) or yearly (on a date, or like the 1st Thursday of January); anything else is listed as not imported, to add by hand. Formatted (HTML) descriptions, as Google exports them, come in as plain text.

**Subscribing:** Calendar → "Subscribe in your calendar app" gives each member a private link (`/calendar/feed/SECRET.ics`) for Apple Calendar, Outlook, Google or a phone, a month back and a year ahead; tick what to include (`?show=events,chow`), and subscribe more than once for separate colors. The link works without signing in, so it's secret; "Make a new link" replaces it. It uses the app's HTTPS name, so calendar services on the internet can fetch it (Google refreshes subscribed calendars only every few hours).

**Public calendars:** `public_calendars: [events]` (any of events, planning, community, holiday) under `club:` shows those calendars to anyone, signed in or not, at `/calendar/public`, with a public feed at `/calendar/public.ics`. Chow nights, birthdays and business due dates are never public. Empty by default.

**Copy to Google Calendar (optional):** for people who follow the club in Google Calendar, the app can copy its calendars there, one way: chow nights, Club events, Planning (with business due dates), Community (each date of a repeating event, a year ahead) and the minutes' dated events. It copies a little after any change and in the daily `reminders` run, only touches events it made, only from today on, and doesn't add an event someone typed into Google by hand with the same title on the same day. Google being down changes nothing in the app. To turn it on:
1. Share each Google calendar with the service account's email (the `client_email` in `private/google-service-account.json`), with **Make changes to events**.
2. In club.yaml, under `club:`, add `timezone:` and `calendars:` with the IDs of the ones to copy to (`chow`, `events`, `planning`, `community`; Settings → the calendar → Integrate calendar → Calendar ID; an account's main calendar has the account's email address as its ID). See `club.example.yaml`.
3. Restart. The Security page's **Server** tab shows the last copy, or why it failed.

Previews never copy to Google.

## Chow schedule
`/chow` is the chow list; the app is the source of truth (state in `chow.json`, every change in `chow-log.jsonl`, both in the data directory).
- One-time import of the Secretary's spreadsheet: `w8lbz-minutes chow-import FILE.xlsx --order "Surname,Surname,..." --next Surname` prints what it found, including anything to check; add `--write` to save it (it won't overwrite an existing schedule).
- Members ask each other to swap; leaders edit directly; the console records provided, missed or covered on meeting night.
- The printable list (`/chow/list.pdf`): every night still to come. Officers email it to members from the Chow page (each member can turn it off); the page shows whether the schedule changed since it was last sent.

## Treasurer
**Treasurer** in the menu (`/treasurer`), for officers; whoever holds Treasurer, and admins in admin mode, record the money (`treasurer.py`, `treasurer.json` in the data folder, every change logged in `treasurer-log.jsonl`).
- **Who owes what** is worked out, never typed: dues for each meeting night since tracking started (or since the member joined), the initiation fee for members who joined since then, a charge for each chow night marked missed, plus adjustments, minus payments. Every meeting night counts, holidays included (52 × $3.50 is the bylaws' $182 a year); a credit covers a night the club calls off. Members may pay weekly, monthly or for the year: paying ahead shows as "paid through" a date. Four weeks or more behind shows in red. It also shows who still owes a Certificate of Indebtedness, and by when.
- **Money in and out:** payments (dues, initiation fee, missed chow, certificate), other income (drink sales, donations), adjustments (a charge, or a credit when negative: a balance from before the app, a night with no meeting) and expenses, each with a date and an account. Delete fixes a mistake; the log keeps it.
- **Regular bills** (Bills tab): the Treasurer lists what the club pays every month or year (payee, about how much; a yearly one's month), and the tab shows which haven't been paid this month or year, whether or not they were read at a meeting.
- **Bills read at meetings:** each Bills and Communications item with a $ amount (`Gas Bill $81`) since tracking started waits for the Treasurer to mark it paid (an expense) or not a bill; when it's one of the regular bills, pick which, so it isn't counted twice.
- **Funds** run by a committee (a drinks fund with its own checking account, say): list them in the config with their keepers' callsigns. Keepers get the Treasurer page with only their fund's account: they record its income and expenses and see only those; the Treasurer sees everything.
- **Home** shows each member their own figures, never anyone else's.
- **Config** under `club:` in club.yaml; only `start` is needed, the rest default to the bylaws (VII):

      treasurer:
        start: 2026-11-12        # nothing is owed before this date
        accounts: [Checking, Cash]
        dues_weekly: 3.50
        initiation: 25
        certificate: 100
        certificate_years: 2
        missed_chow: 35
        arrears_weeks: 4
        funds:
          - {name: Drinks fund, account: Drinks Checking, keepers: [KD8AAA, KD8BBB]}

  When the bylaws change an amount, list it with the date it starts, so earlier weeks keep the old amount (the certificate's is the one in effect when a member joined):

      dues_weekly:
        - {from: 2020-01-01, amount: 3.50}
        - {from: 2027-01-01, amount: 4.00}

  Until `start` is set, officers opening the page are told to ask an admin (named from `admins:`), and members see nothing.

Not a bookkeeping system: there are no account balances or transfers yet (ROADMAP → Treasurer).
