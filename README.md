# W8LBZ Minutes

Meeting-minutes assistant for W8LBZ. See [REQUIREMENTS.md](REQUIREMENTS.md).

## What it does
Every change is listed in [CHANGELOG.md](CHANGELOG.md), and planned work in [ROADMAP.md](ROADMAP.md).
- **Archive:** each meeting is a Markdown file at `data/meetings/YYYY-MM-DD/minutes.md`, next to the original PDF. The importer converts the historical PDFs (and `*CORRECTED*.txt`) with a plain parser, no AI; imported meetings are marked "imported, unreviewed".
- **Home:** what's next for you: the next meeting, your chow night and swap requests, your business items, the latest minutes and upcoming events.
- **Minutes:** the archive (`/minutes`) in the modernized layout with PDF download (View original instead for imported minutes), one search box for the minutes (full text), members, guests, business items, chow nights and calendar events, per-member history, attendance stats and guest tracking.
- **Leader console:** take the minutes live on meeting night while members follow along, then submit to publish and email them.
- **Members:** sign-in with passkeys or an emailed link, the roster, member profiles, and admin mode.
- **Business items:** Old and New Business tracked from week to week, with owners, due dates and reminders. Motions keep their mover, seconder and result (Business → Motions).
- **Calendar:** the club's calendars kept by the app, with .ics import, subscriptions and an optional copy to Google (see Club calendars below).
- **Chow schedule:** the rotation, swaps, reminders before your night, and who provided, missed or covered each night.
- **Email preferences:** each member turns each kind of email on or off, including a meeting-day email (the time, your chow, your business items, the calendar until the next meeting and last meeting's minutes). Every optional email ends with a link to change it. The daily `reminders` timer (8 am) sends business and chow reminders and the meeting-day email.

## Run
```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # once
uv sync
uv run w8lbz-minutes roster                          # sync people from the roster Google Sheet
uv run w8lbz-minutes import archive                  # (re)build data/ from archive/
uv run w8lbz-minutes serve --host 0.0.0.0 --port 8765
uv run playwright install chromium                  # once: the mobile-first test uses it (skipped without it)
uv run pytest -q
```
The roster sync needs a Google service-account key at `private/google-service-account.json`. Share the sheet with the account's email as Editor (profile edits are written back to it).

PDF generation needs the system Pango library: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`.

## Deploy (Debian container)
As root on the container:
```sh
curl -fsSL https://raw.githubusercontent.com/W8LBZ/minutes/main/deploy/setup.sh | bash   # first time
/opt/w8lbz-minutes/deploy/update.sh                                                    # after each merge to main
```
Then put `club.yaml` and `google-service-account.json` in `/etc/w8lbz-minutes/`, and data in `/var/lib/w8lbz-minutes/`.
The app runs as the `minutes` user on port 8000. The roster syncs daily at 04:00; backups run at 02:30 (see Backups).

## Backups
Every night at 2:30 (`w8lbz-minutes-backup.timer`) the app puts the whole data folder (minutes, chow, business, calendars, settings, logs, the roster cache, and a consistent copy of the sign-in database) into one `.tar.gz`, encrypts it (scrypt + AES-256-GCM) with a passphrase only the admins hold, decrypts it again to check it opens, keeps the last 7 in `backups/` in the data folder, and sends it to S3-compatible storage. If anything fails, the admins get an email. Admin mode → Security → **Backups** shows the last result, runs one now, and downloads the local copies (still encrypted).

**Setup:**
1. A long passphrase in `/etc/w8lbz-minutes/env`: `W8LBZ_BACKUP_PASSPHRASE=...`. Keep a copy somewhere other than the server (a password manager the admins share): without it no backup can be opened.
2. Backblaze B2 (or any S3-compatible storage): a private bucket, and an application key limited to that bucket with read and write access. In club.yaml:
   ```yaml
   backup:
     s3: {endpoint: https://s3.us-west-004.backblazeb2.com, region: us-west-004, bucket: BUCKET, prefix: minutes/}
   ```
   and in `/etc/w8lbz-minutes/env`: `W8LBZ_S3_KEY_ID=...` and `W8LBZ_S3_SECRET=...`. How long backups are kept there is the bucket's lifecycle rule (B2: Lifecycle Settings → custom rule for `minutes/`: hide files 90 days after upload, delete them 1 day after hiding), so the app never deletes anything there.
3. `systemctl start w8lbz-minutes-backup` (or Back up now) and check the Backups tab.

**Restore** (onto this server or a fresh one set up with `setup.sh`):
```sh
sudo -u minutes /opt/w8lbz-minutes/.venv/bin/w8lbz-minutes restore BACKUP.tar.gz.enc /tmp/restored   # asks for the passphrase
systemctl stop w8lbz-minutes
rsync -a /tmp/restored/ /var/lib/w8lbz-minutes/ && chown -R minutes:minutes /var/lib/w8lbz-minutes
systemctl start w8lbz-minutes
```
`restore` only unpacks into an empty folder, never over live data. Members stay signed in (the sign-in database is in the backup); passkeys keep working as long as the hostname is the same.

## Server hardening (recommended)
The app blocks web visitors itself after repeated failed sign-ins (admin **Security** page). For SSH, use fail2ban:

1. Install it:

        apt install fail2ban python3-systemd

2. Create `/etc/fail2ban/jail.d/sshd.local` with this content (on `ignoreip`, list your own LAN and tailnet so you can't lock yourself out):

        [sshd]
        enabled = true
        backend = systemd
        ignoreip = 127.0.0.1/8 ::1 192.168.1.0/24 100.64.0.0/10

3. Restart it and check that the jail is running:

        systemctl restart fail2ban
        fail2ban-client status sshd

To lift a ban: `fail2ban-client set sshd unbanip ADDRESS`.

Also worth doing: SSH with keys only (`PasswordAuthentication no` in `/etc/ssh/sshd_config`), automatic security updates (`apt install unattended-upgrades`), and a firewall that allows only SSH and the app's port.

## Login
Members sign in with a passkey, or with a one-time link emailed to their roster address.
- **Passkeys** only work at the HTTPS address in `club.yaml` (`auth.rp_id`).
- **Email** needs `W8LBZ_SMTP_USER` / `W8LBZ_SMTP_PASSWORD` (a Gmail app password) in `/etc/w8lbz-minutes/env`.
  Without them, links are written to the server log instead.
- **AI** (optional) needs `W8LBZ_OPENROUTER_KEY` in `/etc/w8lbz-minutes/env` and `ai: {model: ...}` in club.yaml. Each call is logged to `ai-log.jsonl` in the data folder (time, model, size, cost; never the text).
- **Recovery:** an admin on the server can print a one-time link for a member: `w8lbz-minutes login-link KD8XYZ`

Officers and members ticked `minutes_editor` in the roster sheet can lead a meeting (the leader console). Admins are a separate list in `club.yaml`.

## Config
`club.yaml` (local only, git-ignored; copy from `club.example.yaml`) holds the sign-in settings, the admins list, email test mode, the club links, the roster sheet's address and the private never-record list. People, their titles and who may lead come from the roster sheet; the list of offices is `offices:` in club.yaml.

Club data (`archive/`, `data/`, `club.yaml`, credentials) is never committed.

## Emailing the minutes
When a leader submits the minutes, they go to every roster member with an email address (except Inactive and Silent Key), each getting their own copy: the PDF attached, or a link, as each member chose on the Email tab of their profile (or not at all, if they turned it off). Replies go to the meeting's leader.
- **Test mode:** while `club.yaml` has `email.only_to`, only those callsigns or addresses get mail; everyone else is withheld and logged. The submit dialog says which mode is on.
- Each meeting is emailed once. The outcome is in `emailed.json` beside the minutes, and leaders see it on the minutes page.
- **Removing a test meeting:** `w8lbz-minutes delete-meeting 2026-12-31`, then `systemctl restart w8lbz-minutes`.

## Member profiles
Members edit their own email, phone, address, birthday and sharing choices at `/profile`. The app writes those cells to the roster sheet, values only, and never changes columns, dropdowns or formatting.
- **Setup:** share the roster sheet with the service account as **Editor**, and add `birthday` and `share` columns to the Member Roster tab (headers exactly; any position).
- Each change is logged in `profile-changes.jsonl` in the data directory, emailed to members whose title includes Secretary, and can be undone by any officer at `/roster/changes`.
- Officers see all contact details; other members see only what each member shares.
- **Roster keepers edit the roster** (Secretary and Treasurer by default, `roster_keepers:`; admins in admin mode): **Edit roster details** on a member's page (`/members/ID/edit`) changes name, class, status, email, phone, address, titles, aliases and the `is_officer`, `is_board`, `minutes_editor` and `calendar_editor` ticks, through the same path as members' own edits (values only, logged, undoable, emailed). It also adds Membership Log rows. Not the callsign (it's the member's id in the app's records) and not birthday or sharing (the member's own choices). Needs `aliases`, `minutes_editor` and `calendar_editor` columns on the sheet to change those.
- **Roster API** (`GET /api/roster`), for the club's other tools on the same server: active members' `callsign`, `name`, `email` and `officer`, as JSON. Off until `W8LBZ_ROSTER_API_TOKEN=...` (a long random string) is in `/etc/w8lbz-minutes/env`; then it answers only requests from the server itself (127.0.0.1, not through the web proxy) with `Authorization: Bearer <token>`, e.g. `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/roster`. Every call, allowed or not, is logged in `api-log.jsonl` in the data directory.
- **Offices** (`/offices`, linked from the Roster): the offices in club.yaml's `offices:` (a title is an officer's office with one seat; `{title: Trustee, officer: false, seats: 3}` is a board seat), who holds each, and who held them when, from the Membership Log's "Took office" / "Left office" rows (the office in the `note` column). An admin in admin mode changes a holder in one step: the app writes `Title(s)`, `is_officer` and `is_board` for both members (officer if any office they hold is an officer's, board if any is in the list; other titles are kept), adds the log rows, and emails the officers. "Add a past term" adds history only.
- **Email** and **Settings** tabs: which emails each member gets (`email-prefs.json`) and how the app shows things to them, e.g. the first day of the week (`settings.json`), both in the data directory.

## Admin mode
Callsigns listed under `admins:` in `club.yaml` get **Turn on admin mode** in the name menu. For an hour (or until turned off) that sign-in can see and do what officers can, with a red banner showing. Each switch is logged in `admin-log.jsonl` in the data directory.

In admin mode, a member's page has **View the app as …**: the app as that member sees it, read-only (no changes, and not their sign-in or passkey page), until **Stop** or admin mode ends. Also logged.

## Links page icons
Besides the 21 built-in icons, the icon picker searches about 3,400 brand logos from [Simple Icons](https://simpleicons.org) (CC0). They're stored in the repo (`src/w8lbz_minutes/simple_icons.json`), never loaded from a CDN, and drawn in the club's color like the built-in icons. To update them: `python3 deploy/update-simple-icons.py [VERSION]`.

## Bylaws
`/bylaws` shows the club's constitution and bylaws from `bylaws.md` in the data directory (`/var/lib/w8lbz-minutes/bylaws.md` on the server). It's kept out of this public repo.

## Previewing a branch
`deploy/preview.sh BRANCH` runs a branch on a development machine (ports from 8100) so it can be tried before merging, without touching the live server. It prints the address and a one-time sign-in link (passkeys don't work on a bare IP address).
- **Preview mode:** a banner on every page; email is recorded on the **Email log** page (name menu → Email log, in admin mode) instead of being sent; roster edits go to a local copy of the sheet.
- Each preview has its own checkout and a fresh copy of `data/` (meetings, roster cache, bylaws) on every update; its sign-ins are kept.
- `deploy/preview.sh --list` shows running previews; `deploy/preview.sh --stop BRANCH` stops one and deletes its copy.
- They run as user services (`systemctl --user`), so they stop if this user logs out entirely, unless lingering is enabled (`loginctl enable-linger`).

## Changes and releases
- Each PR adds a line under "Unreleased" in `CHANGELOG.md`, and removes its item from `ROADMAP.md` if it finishes one.
- **Stacked PRs** (one branch built on another, while earlier ones wait for review): each PR targets the branch below it, so its diff shows only its own change. To release the stack, a release PR from the top branch into main carries everything. Just before merging it, retarget every PR in the stack to main; GitHub then marks each one merged when the release lands, because its commits are in main. A PR left targeting another branch stays open and has to be closed by hand. Keep stacks short where possible.
- To release: in a PR, rename "Unreleased" to the new version and date and bump `version` in `pyproject.toml` (then `uv lock`). When it merges, the Release workflow tags the merge commit `vX.Y.Z` and publishes a GitHub release with that CHANGELOG section. Deploy with `update.sh`; the footer then shows the new version.
- **Releases that need steps by hand** (a config change, a data file to copy, a new secret): add a line `- **Deploy notes:**` to that version's CHANGELOG section, with the steps indented under it as a numbered list. Put each command or file content in its own code block (indented 8 spaces under its step), one step per action, so they can be followed and copied. The admin Updates page shows them, formatted, and asks the admin to confirm they're done before updating. Only steps the release needs belong here; optional server hardening goes in this README.
- **Updating from the app:** admins in admin mode see "Version X is available" and an **Updates** page with **Update now**. It writes `update-request.json` in the data folder; the root-owned `w8lbz-minutes-update.path` unit sees it and runs `deploy/update-from-app.sh` (which runs `update.sh` and records the result). `update.sh` installs and enables that unit, so the first update after this feature arrives is still by hand.
- Every pull request runs the tests (the Tests workflow).

## Security checks
Run on every pull request, every push to main and **every Monday**, so a newly published advisory turns up even in a quiet week. Any of them can also be run by hand: Actions → the workflow → Run workflow.
- **Security workflow:** `pip-audit` checks the locked Python packages (`uv.lock`) against the PyPI and OSV advisory databases; `gitleaks` looks for keys, tokens and passwords anywhere in the git history.
- **CodeQL workflow:** GitHub's code scanner for the Python and JavaScript (injection, script injection (XSS), unsafe redirects...). Findings are under Security → Code scanning.
- **Dependabot** (`.github/dependabot.yml`): a weekly PR for newer Python packages and GitHub Actions, and one right away for a security fix. Review and merge like any PR.
- **Tests:** the mobile test opens every page in a real browser and fails if the Content Security Policy blocks anything or a script errors. The policy runs scripts only from the app's own files or the `<script>` blocks a page was given a one-time nonce for: no inline `on…=` handlers (use `data-confirm`, `data-autosubmit`, `data-select`, `data-tick` from `base.html`, or a listener in a `<script nonce="{{ csp_nonce }}">`).
- Actions in the workflows are pinned to a commit, with the version in a comment; Dependabot updates both.
- **Repository settings** worth having on (Settings → Code security): Dependabot alerts, secret scanning and push protection (blocks a push that contains a key).

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

## License
Copyright © 2026 James Flattery, KO4CGQ.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE).

If you run a modified version for others over a network, you must offer them its source code (AGPL section 13). The About page's source link does this for the club's copy.
