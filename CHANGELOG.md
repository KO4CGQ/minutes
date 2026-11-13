# Changelog

What changed in each version of Club Minutes. Planned work is in [ROADMAP.md](ROADMAP.md).

Versions follow [semantic versioning](https://semver.org): while the app is 0.x, the middle number goes up for new features and the last for fixes. Each version is a git tag (`v0.2.0`), and the app's footer shows the version that's running.

## Unreleased
- **Holidays for any country, state or region:** from the python-holidays package (every US state and territory, France, Germany and its Länder, the UK and its nations, and many more), with Easter-based days and weekend substitutes, in English; picked on Admin → Site → Features by typing a name. **The club's own holidays** are added there too, a row each with **+ Add a holiday** and **Remove** (Undo until saved; no script): a date (December 24) or a weekday rule (the last Friday of June), plus days after (Black Friday: the day after the 4th Thursday of November), with or without a country (`calendar: extra_holidays:`). Christmas Eve and New Year's Eve, which the old US list added, are now entered this way. Names follow the package ("Thanksgiving Day", "Christmas Day"). Election days: Ohio's as before, or `us`, the US general election in even years; another state is one small file (docs/developers.md).
- **Admin → Site → Features and Bylaws:** chow on or off, which calendars are public, the holiday and election regions; the bylaws references the app cites, the arrears note, and the certificate (bond) with its name, short word and note (shown only while it's ticked, in CSS). Only changes from club.yaml are kept, and turning something off here wins over club.yaml turning it on; turning a part off keeps its data.
- **Admin → Site → Agenda:** the meeting's sections (tick the ones used, a number for the order, the console hint for each, and new ones of the club's own) and the two reminders: invite a guest to apply after so many visits, and remind business item owners so many days ahead. Only changes from club.yaml are kept; a wrong one is refused, saying why. Past minutes keep their sections. The two reminders in club.yaml are now checked at start too (a whole number), like the other settings.
- **About page:** headed "About Club Minutes by KO4CGQ", describing the app for amateur radio clubs rather than naming this club.
- **Admin → Site → Club:** the club's name and callsign, the app's name (moved here from Look), and its meeting day, start time and time zone, checked like club.yaml (a wrong one is refused, saying why) and used at once. Only what differs from club.yaml is kept, each field says where its value comes from, and **Back to club.yaml** undoes them all. A new meeting day or time doesn't move what's planned: the page says so, with how many chow nights are on the old day and a button to change the calendar's weekly meeting from the next date on.
- **Admin → Site has tabs, and settings move into the app:** **Look** (pictures and colours, each a card that folds away from its heading) and **Dates**: US or day-first, with an example of each, or your own formats (shown only when picked, with a live preview); it's used at once and wins over club.yaml's `date_formats` until **Back to club.yaml**. The Site page's settings are kept in `site-settings.json` in the data folder; sign-in, admins, email test mode and AI stay in club.yaml only.
- **A demo club** for screenshots, the video and previews without a club's data: `DEMO=1 deploy/preview.sh BRANCH demo`, built by `demo/build.py` (Example Radio Club, invented members, minutes, business, chow and calendar, dated around today).
- **Changes come only from the app's own pages:** a form or request sent from another site, or from another server on the same domain (another name on the tailnet, say), is refused, so a page elsewhere can't act as a signed-in member (or admin).
- **Admins managed in the app** (admin mode → Admin → Security → **Admins**): make a member an admin, or remove one, without the server's club.yaml, so the club doesn't depend on whoever has the server. Making someone an admin asks for a passkey or emailed link unless you just confirmed; each change is in Admin actions and emailed to the other admins (the one added or removed too); the last admin can't be removed. Admins in club.yaml still count and are changed only there. Backup and update emails reach every admin.
- **Dates in the club's format everywhere:** the calendar, Home, search, emails and messages follow `date_formats` too (they wrote US order themselves), with two new formats without the year, `short` (Nov 5) and `long` (November 5); the weekday forms are built from them. Upcoming Events in the minutes read "Sat Nov 21" (was "Sat, Nov 21"), like the rest of the app.
- **The app is called Club Minutes inside too:** the Python package is `club_minutes`, the admin command `club-minutes`, the services `club-minutes*` (staging: `club-stage-sync*`), the folders `/opt/club-minutes`, `/etc/club-minutes` and `/var/lib/club-minutes`, and the settings in the env file `CLUB_MINUTES_…` (were `W8LBZ_…`). Nothing members see changes; they stay signed in. Backups made before still restore, and old and new ones are kept and pruned by date. The ROADMAP keeps only work on the app.
- **Chow is an optional module,** off unless club.yaml has `modules: {chow: true}`. Off, it leaves no trace: no Chow page (its addresses answer "not found"), and nothing about chow on Home, the menu, the calendars, search, email choices and reminders, the console and the minutes, or the Treasurer pages (the missed-night fee). New members join the rotation only while it's on.
- **Holidays and election days by region,** none unless club.yaml picks them: `calendar: {holidays: us, elections: us-oh}`. Each region is one small file (`regions/`), so another state or country is one file to add; a misspelled region stops the app at start, saying which. Without holidays, the Holidays calendar isn't offered.
- **Bylaws references are the club's own:** the sections the app cites (Treasurer pages, the arrears email, the bond, a missed chow night) come from club.yaml (`bylaws: refs:`), each shown only if given, and the arrears email's note on what happens next is the club's (`bylaws: arrears_note:`). Recording a missed chow night now names the fee actually set on the Treasurer pages (it said $35 whatever the setting).
- **The bond is the club's own, or none:** a certificate each member buys once is on only when club.yaml names it (`treasurer: certificate:` with its name, a short word for sentences, and an optional note). Without one, nobody owes one, and the Treasurer pages and Home don't mention it.
- **The meeting's sections are the club's agenda** (`meeting: sections:` in club.yaml): the built-in ones in the club's order, and its own free-text sections with a hint for the leader console. Without it, the built-ins (no Winners: that was one club's own section). Minutes keep any section they have, even one later dropped from the agenda.
- **No club's defaults:** the meeting's day and time are required in club.yaml (a missing or mistyped one stops the app at start, saying which; they used to fall back to Thursday at 7:30 PM), the time zone is the server's unless club.yaml names one (it was New York), and `setup.sh` sets the server's zone only when given `TZ_NAME`. Home's "Join online" is the first video meeting link (Teams, Zoom, Meet, Jitsi…), not only a Teams one.
- **The club's colours** (admin mode → Admin → **Site** → Colours): one main colour, and optionally the top bar's (in dark mode, a dimmed version of it), for buttons, links, the top bar and the minutes' section headings, in light and dark mode, the PDFs included. The palettes are made from it and checked so every text stays readable (a shade is adjusted if needed, and Site says so); the logo's colours are offered as one-click picks, and both modes are previewed before Save. **The app's own colour is now blue** (it was green: a club keeps it by picking it on Site).
- **A new club can start:** `club-minutes first-member CALLSIGN --first … --last … --email … --class …` starts an empty server's roster with its first member (an admin) and prints their sign-in link; it runs only when there's no roster, so it can't overwrite one. The server admin guide has a Getting started for a new club. Until a server has its email settings, pages that send a link say it's in the server's log instead of claiming it was emailed.
- **Staging banner:** signed out, it only says this is a staging server (a test copy of the app); what it holds and where email goes is shown once signed in.
- **Fix:** `setup.sh` installs `sudo` too (a fresh Debian container doesn't have it, and updates need it).
- **Fix:** a calendar feed address with a non-ASCII character in it gave a server error instead of "not found".
- **Deploy notes:** the server's folders, services and settings move to the new names once, by hand (Update now can't do it, and this note keeps the nightly update from trying):
  1. Take a snapshot of the container.
  2. Add the club's settings that used to be built in (the chow list, holidays and election days, bylaws references) to `/etc/w8lbz-minutes/club.yaml`; it moves with the rest. The developer's `private/club.yaml-1.0.0-additions.yaml` has W8LBZ's; another club picks its own (`club.example.yaml`). `meeting_time` under `club:` is now required: add it if it isn't there.

  3. As root, run the developer's `private/rename-from-w8lbz.sh` (copied to the server) with the release: it stops the app, keeps a copy of the data in `/var/backups`, moves everything, installs the release and puts it all back if the app doesn't come up:

          bash rename-from-w8lbz.sh v1.0.0

  4. After the move, to keep the green: Admin → Site → Colours → Main colour `#4f6f2a` → Save (the same green as before, exactly).
  5. From then on the admin command is `club-minutes` and the logs are `journalctl -u club-minutes`. Once all's well, delete the data copy and the snapshot.

## 0.19.0 (2026-11-11)
- **The app's name and logo are set in the app** (admin mode → **Admin** → **Site**): the name in the top bar, page titles, the sign-in page, emails and a passkey's name on phones ("<callsign> Minutes" unless changed), and three pictures, each uploaded and reset on its own: the logo (pages, minutes, PDFs), the browser tab's icon and a phone's home-screen icon. The icons are made from the logo unless one is uploaded of its own; each upload is redrawn as a fresh PNG. **Behind the logo** (white circle, white rounded square or nothing; Automatic picks from its shape) keeps dark line art visible in dark mode; it used to be a white circle for every logo, which cut a wide one into an oval. A tab icon made from the logo gets the same white behind it, so it shows on a dark browser tab. The app now comes with a plain logo instead of W8LBZ's, so another club starts with its own name and no one else's emblem.
- **Site works preview-then-Save:** a chosen picture, or a new "behind it", shows in its card first (marked "Not saved yet"), with Save and Cancel; nothing on the site changes until Save.
- **Admin page:** in admin mode, the name menu has one **Admin** link instead of every tool (and each tool's page has **‹ Admin** back to it); `/admin` lists Security, Backups, Updates, Site, the Email log (and Stage sync on a staging server), each with a line of how things stand. **Backups** is its own page there, at `/admin/backups` (the old `/admin/security/backups`, linked from earlier emails, forwards there), no longer also a tab under Security.
- **Home → Your business items:** each item opens in the same dialog as on the Business page (details, Edit, Mark done, comments), and you come back to Home after; pointing at one shows a faint tick in its box, and a › marks it as one to tap on a phone.
- **Fix:** opening "Add an event" with a date that isn't a date gave a server error; it starts on today.
- **Fix:** uploading a damaged or cut-off picture (a profile picture, or the logo) gave a server error; it now says the file can't be read.
- **Deploy notes:** after updating:
  1. Admin mode → name menu → **Admin** → **Site** → Logo → **Choose…** the club's logo (W8LBZ's is in the developer's `private/branding/logo.png`; any copy of it works) → **Save**. Until then the plain logo shows; the tab and phone icons are made from it.

## 0.18.0 (2026-11-10)
- **One console takes the minutes:** while someone has the leader console open, anyone else who opens it sees it read-only ("Chris is taking these minutes"; the steps still move, to look around), with a link to the live view and **Take over** (their console then turns read-only, and whoever took over is recorded as taking the minutes). Before, two open consoles silently overwrote each other. The hold lets go about 2 minutes after the console is closed or loses its connection; Discard and Submit wait for it too.
- **One meeting open at a time:** Start meeting with one still in progress (say, last week's never submitted) shows it with **Continue** and **Discard**, instead of starting a second; two leaders clicking Start together get one meeting. While one from another day is open, the top bar's Start meeting reads **Continue minutes (Nov 6)** for leaders (no red dot: that's for a meeting happening today).
- **Possible duplicates highlighted:** two entries the same day whose titles look alike (half the shorter one's words in the other, like "VE testing" and "VE Testing session") are highlighted on the Calendar page with "possible duplicate of …", and in the leader console's Upcoming Events (both the calendar list and the minutes' own events). Nothing is hidden or merged on its own; in the console, **use the calendar's** swaps a minutes entry for the calendar's version (title, place, date, time), so it stays in the minutes and shows once on the calendar.
- **The chow list by link:** on the Email tab, the chow list can now come as a link to the schedule online (and its PDF), instead of the PDF attached.
- **Security → Members:** every member's last sign-in, passkeys (how many, last used) and devices signed in now. `/admin` goes to the Security page, and an address that isn't there shows a "Page not found" page instead of a line of code. **Plans:** a 24-hour clock setting.
- **Plans:** site name, logo and favicon set in the app; the menu set from the Links page; a Donate button.
- **Upcoming Events in the leader console: from the calendar.** The step lists what's on the calendar until the next meeting (a menu shows 2 weeks, a month or 3 months), for the leader to read out: club and community events, Planning, holidays and shared birthdays. **+ Add** puts one in the minutes with its date and time; the calendar still shows it once. Any event can be left out (a tick on its calendar form); the weekly meeting is, unless unticked.
- **Upcoming Events pick their calendar:** each dated event in the minutes has a Club events / Community / Planning menu (Club events by default), and shows on that calendar.
- **Fix: empty date and time boxes in Safari** (Mac) showed today's date and time in grey, which read like a value. In Safari they're now blank until clicked; other browsers keep their mm/dd/yyyy hint.
- **Staging server** (optional; server admin guide → Staging server): a second container that pulls the live data one way each night (or Sync now; the nightly run can be paused), shows a STAGING banner, and sends email only to whoever clicked ("[STAGE]" in the subject; the rest is held on the Email log page). Developers install a branch there with `deploy/stage-push.sh` before a release.
- **Recorded by:** when the president chairs and someone else takes the minutes, pick the president under Led by; the minutes, PDF and email then say "Led by" them and "Recorded by" whoever started the console, and replies to the email go to the recorder. On a usual night, one person, they say "Led & Recorded by" (older minutes, from before this, keep "Led by").
- **Meeting page during a meeting:** "Open in the leader console" is a link at the end of the "Meeting in progress" badge instead of a big button above it, and only people who can lead see it.
- **Plans:** suggested edits during a live meeting are dropped (an editor fixes things after the meeting, before Submit); the bylaws link item is done by the Bylaws page.
- **New home: github.com/KO4CGQ/minutes.** The update check, `setup.sh`, the About page and the server admin guide use the new address (the old one forwards there).

## 0.17.1 (2026-11-10)
- **Fix: updates failed when a tag had moved on GitHub** (0.17.0 overnight, silently): the updater now takes GitHub's tags as they are, says which step failed, and the failure email no longer claims the app went back to its old version when nothing was installed.

## 0.17.0 (2026-11-09)
- **Nicknames in search:** with a Richard on the roster, "Richard", "Rich", "Rick" and "Dick" find each other (about 90 common English names and their nicknames, for roster first names only). Nicknames that are everyday words in minutes ("will", a "bill", "jack") are left out unless they're the member's own name or in their Other spellings, and other spellings match whole words only, so "Al" for Alan doesn't find "all".

## 0.16.0 (2026-11-09)
- **Automatic updates (off until turned on):** admin mode → Updates → Turn on. Every night (2:30 by default; the hour is set on the same page) the server installs the newest release if no release since the running one needs steps by hand, after a full backup (no backup, no update), and emails the admins how it went. A release with steps by hand is only emailed about.
- **Updates install the release itself and roll back if the app doesn't come up:** "Update now" now installs exactly the release shown (its tag), not whatever is newest on main; if the app doesn't answer within 5 minutes afterwards, it goes back to the version it had. With several releases waiting, the page shows all their notes and steps by hand, not only the newest's.

## 0.15.0 (2026-11-09)
- **Hardening (security review of this release):** a page number or year typed with characters like "²" no longer causes a server error (one shared number check, 8 places); a club link can't be a `/\…` address that browsers treat as another site; searches count their first 12 words; a missing roster file no longer shows the server's folder. New fuzz tests (`tests/test_fuzz.py`) throw thousands of hostile inputs at search, club links, sign-in redirects, the roster file, the roster API and every page.
- **No more Google for the roster or calendars:** the roster is only ever the app's own (`roster-sheet.json`), so the Google Sheets code, `roster --import` and the `roster: source: app` setting are gone (it can come out of club.yaml), and so is the optional copy to Google Calendar (`calendars:` in club.yaml is ignored). Google Drive backups are unchanged.
- **Passkey offer after an emailed link:** signing in with an emailed link now offers a passkey in a small banner on the page you were going to, if your device can make one (fingerprint, face or device PIN), even if you already have a passkey on another device. "Not now" hides it until the next emailed-link sign-in. It replaces the stop at the account page, which only members with no passkey at all saw, whether or not their device could make one.
- **Club links to the app's own pages stay relative:** a link like `/bylaws` keeps working if the app's address changes; the minutes and their PDF show it in full (from `auth.rp_id`). Saving the Links page turns links to the app's current address into `/page` ones.

## 0.14.0 (2026-11-09)
- **Search finds other spellings:** a member's name also matches the other spellings the roster lists for them (so a name spelled one way in old minutes is found by the other), and if the minutes still turn up nothing, close spellings of the words are tried.

## 0.13.0 (2026-11-09)
- **Roster in the app:** `w8lbz-minutes roster --import` copies the roster Google Sheet (Member Roster and Membership Log tabs) into the app once and lists any differences; with `roster: source: app` in club.yaml the app then reads and writes its own copy (`roster-sheet.json` in the data folder, backed up with it) and never touches Google for the roster. Pages now say "the roster", not "the roster sheet".
- **Attendance:** a chart of each meeting's attendance (in person, online, guests) over the chosen period, above the tables. Hover or tap a bar for its numbers; click to open that meeting.
- **Business items, one tap for the details:** each item is a small card (wording, owner, due date, how many comments); tapping it opens a dialog with everything else: Edit and Mark done (or Reopen, or a motion's Edit), all the comments and a box to add one, and the history folded away. After a comment or an edit the page comes back with that item open. The intro is one line.
- **Celebrations:** the leader console's Attendance step opens with the week's shared birthdays ("Happy birthday, …"; a month-only birthday at the month's first meeting) and membership anniversaries ("5 years as a member").
- **Previews** (`deploy/preview.sh`) use the local branch when there is one, so work can be tried before it's pushed.
- **Docs by audience:** the README is now a short overview; the detail moved to guides in `docs/`: members, officers, server admins and developers.
- **Treasurer** (officers' menu): who owes what, worked out from the bylaws ($3.50 a meeting night, the $25 initiation fee, $35 for each missed chow night, and the $100 Bond (Certificate of Indebtedness) within two years); payments, adjustments and expenses, each with an account; and the bills read under Bills and Communications, picked up by their $ amount for the Treasurer to mark paid or not a bill. Dues paid ahead (monthly, yearly) show as "paid through" a date, and an amount the bylaws change can be listed with the date it starts. Officers look; the Treasurer (and admins in admin mode) record. Each member sees their own figures on Home.
- **Treasurer settings on the page:** the Treasurer turns tracking (dues, bills and spending) off, to Preview (officers only, no emails: for checking the figures on the live app) or on, sets the start date, changes the amounts with the date each takes effect, and adds accounts and funds. Nothing goes in club.yaml.
- **Arrears:** an optional daily email to the Treasurer and the Board when a member reaches the arrears line (bylaws VII.B). Suspending stays with the Board (bylaws VIII): members can now be given the status Suspended, and the Membership Log has Suspended and Reinstated rows.
- **Bills from the minutes:** every amount in a bullet is its own bill, amounts without a $ count, and a bullet repeated from the week before is marked.
- **Spending, for every member:** the club's expenses over the last 12 months by category and month (chart and tables), once the Treasurer turns tracking on (officers only in Preview). Each expense gets a category, guessed from its words and changeable by the Treasurer. In the leader console, the Bills step lists what the Treasurer recorded as paid since the last meeting, with **Add to tonight's bills**.
- **Cost per member** on the Spending page: each month's spending over the members that month (following joins and departures in the Membership Log), the 12-month average, and what dues bring in per member to compare.
- **Bonds bought years ago** go on file (date and amount, no account): the member shows as having bought one, with no money in or out now. Payments can't be dated before tracking starts.
- **Bond wording:** every page that says "bond" explains it's the club's name for the Certificate of Indebtedness, not a government or savings bond. Who owes what lists who owes something, with **Everyone** a tap away.
- **Spending categories** are the Treasurer's to add, rename and remove (Settings), each keeping its chart colour. **Bills from the minutes** are titled by their own words when a bullet holds several, open on what's waiting, and older minutes are read only when the Treasurer looks back (and can undo it). Spending shows how "dues bring in" is worked out.
- **View as is gentler:** trying to change something while viewing the app as another member opens a "Read-only" dialog instead of a page saying no. **Bills** have Waiting, Paid, Not a bill and All tabs, the Treasurer names a bill as he marks it paid, and can rename entries later on the Money tab.
- **Console comments show when:** in the leader console's Old Business, each comment since the last meeting shows when it was written ("Mon, Nov 9, 10:44 AM").
- **Treasurer input checks:** dates are stored one way whatever form they arrive in, money can't be dated in the future, amounts take plain digits (commas only between thousands) up to $1,000,000, and bill references must be the app's own. Tests try every Treasurer action as an ordinary member and as a fund keeper, and hostile text in the minutes, notes and categories.
- **Security review of the whole app:** signing in can no longer send you to another site afterwards (a "next" address with a backslash or a tab was read by browsers as another site); unreadable sign-in requests are a 400, not a server error; an original PDF is only served for a real meeting. New tests try every page and action signed out, officer pages as a member, and members changing someone else's items, comments or chow swaps.
- **A slow roster sheet no longer holds everyone up:** saving a profile, adding a member, editing roster details, business edits, chow availability, backup settings and profile pictures do their slow part (the Google Sheet, email, S3) off to the side, so other members' pages keep loading; roster changes still go one at a time.
- **The nightly roster sync and the web app take turns:** a profile saved while the sync runs waits for it, so neither writes over the other's copy of the roster.
- **Upload limit an admin sets:** Security → Server → Largest upload (10 MB by default) covers profile pictures and calendar imports, and turns bigger requests away before reading them.
- **Treasurer, more:** regular bills (what the club pays every month or year) show whether they've been paid this month or year; other income (drink sales, donations); and funds a committee runs, like a drinks fund with its own checking account, whose keepers record that account's money and see only it.

## 0.12.1 (2026-11-09)
- **Motions:** Moved by and Seconded by each leave out whoever is picked in the other (leader console and Business → Motions).
- **Business items:** an item can only be deleted at a meeting that's still being written, and a past motion can't be deleted at all (a delete leaves no trace).

## 0.12.0 (2026-11-09)
- **Deploy notes:** before updating:
  1. Add the club's offices (bylaws II.A and III.A) to `/etc/w8lbz-minutes/club.yaml`, for the new Offices page (without it the page shows a generic four):

          offices:
            - President
            - Vice President
            - Secretary
            - Treasurer
            - Transmitter-Trustee
            - {title: Trustee, officer: false, seats: 3}

  2. In the roster sheet, check the **Membership Log** tab has a `note` column (offices and callsign changes write there). Add it if not.

  Optional, any time later (README): the backup passphrase (`W8LBZ_BACKUP_PASSPHRASE` in `/etc/w8lbz-minutes/env`; until it's set, no backups run and callsign changes are refused), S3 or Google Drive on the Backups page, and the roster API token and allowlist for the Discord bot.
### Roster and offices
- **Roster API for the Discord bot's container:** `W8LBZ_ROSTER_API_ALLOW` lists the addresses (besides the server itself) that may call `/api/roster`, still only with the token and never through the web proxy. The bot runs in its own container on the same Proxmox host. Each member now also lists `former` callsigns, so the bot moves a member's Discord verification to their new call by itself after a callsign change.
- **Callsign changes:** Edit roster details → **New callsign** (a vanity call, or a new call with an upgrade). The app takes a backup first, writes the new call to the roster sheet (the old one goes in Other spellings, so old links and club.yaml entries still find the member), adds a "Callsign Change" row to the Membership Log, and moves the member's chow nights, business items, minutes (attendance, leader, report tags), email choices, calendar link, picture, sign-ins and passkeys to the new call. The member stays signed in. Logs keep the old call as history and show the member's name.
- **Edit the roster in the app:** roster keepers (Secretary, Treasurer) and admins in admin mode get **Edit roster details** on each member's page: name, license class, status, email, phone, address, titles, other spellings, and the officer, board, lead-meetings and change-calendars ticks. Each change is written to the roster sheet (ticks stay checkboxes), shown under Roster changes with Undo, and emailed to the Secretary and the member. The same page adds Membership Log events (Joined, Rejoined, Left, Silent Key, Callsign Change, or anything else), and "member since" follows the latest Joined or Rejoined. Callsign changes still happen on the sheet.
- **Roster API** for the club's Discord bot: `GET /api/roster` gives active members' callsign, name, email and officer tick. It answers only requests made on the server itself (not through the web proxy) that carry the token in `W8LBZ_ROSTER_API_TOKEN`; without that token it's off. Every call is logged in `api-log.jsonl`. That tool can stop reading the Google Sheet, one step toward retiring it.
- **Offices and officer history:** Roster → **Offices** lists the club's offices (`offices:` in club.yaml, from the bylaws), who holds each, and who held them when. An admin in admin mode records a change of holder in one step (after an election, a resignation, or a vacancy filled): the app sets the titles and the officer and board ticks on the roster sheet, adds "Took office" and "Left office" rows to its Membership Log, and emails the officers. Past terms from before the app can be added to the history. Each change shows in Roster changes.
### Backups
- **Back up before updating:** the Updates page has "Back up first", ticked by default once backups are set up. A full backup runs before the update starts; if it fails, the update doesn't start.
- **Backups the app makes itself:** the whole data folder (minutes, chow, business, calendars, settings, the sign-in database) in one compressed archive, encrypted with a passphrase only the admins hold, checked that it opens, kept on the server and sent to Backblaze B2 or other S3-compatible storage, a Google Drive folder (in a Shared Drive), or both. Admin mode → Security → Backups sets it all up: where they go, how often (daily, weekly or by hand, at a chosen hour), how many to keep, with recommended values; it shows the data's size and the last result, runs one now, and downloads a fresh backup to your computer. The page also suggests a passphrase that's easy to type from a printout, and checks one (does it match the server's, how strong is it, is it in Have I Been Pwned's leaked passwords). The admins are emailed if a backup fails. `w8lbz-minutes restore` unpacks one; README → Backups has the steps.
### Meetings and chow
- **Motions as records:** a motion made in the console (+ Motion) now keeps who moved it, who seconded it, its wording and its result as data, not just a line of text. Business → **Motions** lists them all, filtered by result, so spending and bylaws changes can be traced to the motion that approved them. Officers can correct a motion, record what became of a tabled one, and add past motions from older minutes (filed under that meeting's date, never carried into Old Business).
- **Printable chow list:** "Printable list (PDF)" on the Chow page shows every night still to come. Officers email it with "Email the list to members", which says whether the schedule has changed since it was last sent; it goes only when someone sends it, never with each set of minutes. Members can turn it off on their Email tab. This replaces the Secretary's spreadsheet.
### Security
- **Security:** a roster keeper can no longer change an admin's email (it's how admins sign in and confirm admin mode); that needs an admin in admin mode. Over HTTPS the app now sends `Strict-Transport-Security` (browsers refuse a plain-HTTP connection to the hostname) and `Cross-Origin-Opener-Policy`. FastAPI's automatic API pages (`/docs`, `/redoc`, `/openapi.json`) are off. Checked: `pip-audit` finds no known-vulnerable packages.
- **From GitHub's code scanner (CodeQL):** email addresses are checked without a regular expression, so a crafted one can't tie up the server; the S3 secret on the Backups page is stored encrypted with the backup passphrase (re-enter it if the passphrase changes); typed text written to the server's log has its line breaks escaped, so it can't fake a log line; redirects after a form go only to pages on this site; callsign renames check the ids they use as file names.


### Other
- **About page:** each release's notes fold up, with only the newest open; long commands in them scroll on a phone instead of widening the page.
## 0.11.0 (2026-11-08)
### Security
- **Fix: search and the leader console could run script typed into the minutes.** Text with HTML in it (a business item, say, which any member can add) was shown as HTML in search results, and could end the leader console's data early. Both are escaped now. Found by testing; no sign it was used.
- **Stricter Content Security Policy:** pages run only the app's own scripts and the script blocks the page was given a one-time nonce for, so injected markup can't run script even if some text slips through unescaped. The 24 inline `onclick`/`onsubmit`/`onchange` handlers became `data-` attributes handled in one place. The phone-width browser test now also fails on anything the policy blocks. (A member's name with an apostrophe in "Remove …'s picture?" no longer breaks that button.)
- **Security checks, every week and on every PR:** `pip-audit` (known-vulnerable packages), `gitleaks` (secrets in the git history), CodeQL (security bugs in the code) and Dependabot (weekly update PRs, and security fixes right away). Workflow actions are pinned to commits. See README → Security checks.
- **Dependencies:** fastapi 0.162.2, click 8.5.1, cbor2 6.2.1 (patch releases). `pip-audit` finds no known vulnerabilities.

### Search
- **One search for everything:** minutes, members (name, callsign), guests who haven't joined, business items (text, owner), upcoming chow nights (who's bringing it) and calendar events (title, place, notes), each linked to its page.
- **Tabs by kind,** with counts: **All** shows the first 5 of each kind (with "See all"), and a kind's tab lists every result a page at a time (10, 25, 50 or all). Minutes results are no longer capped at 100.

### Calendar
- **Event time zones:** a timed event can be kept in another time zone (the club's, from club.yaml, by default). Field Day, for example, is 1800 UTC Saturday to 2059 UTC Sunday: the calendar shows it in club time, and the event's details also give the UTC times. One date of a repeating event can't change its time zone or calendar; the form says to choose This and following dates or Every date.
- **Holidays calendar:** US federal holidays, plus Christmas Eve and New Year's Eve, as their own calendar (shown or hidden from the legend, in the feeds, and public if `public_calendars` lists `holiday`).
- **Ohio election days** on the Community calendar: the primary (May, or March in a presidential year) and the November general election, worked out from Ohio law (there's no official feed), with poll hours and a pointer to voteohio.gov.
- **More repeat patterns:** every day (or every few days), and monthly on several weeks: the 1st and 3rd Tuesday, or the 1st and last. The .ics import brings these in too.
- **Calendar editors:** members ticked `calendar_editor` in the roster sheet (say, the net manager) can add, change and delete events, as leaders and minutes editors can. Add a `calendar_editor` column to the sheet to use it.
- **Who changed what:** officers see every calendar change (who, when, what, which calendar) at Calendar → Who changed what.
- **Business due dates on Planning:** open business items with a due date show on the Planning calendar ("Due: …", with who's looking after it) and in the feeds.
- **Longer agenda:** the agenda shows 8 weeks by default; **Show** can switch it to 3 months, 6 months or a year.
- **Fixes from testing odd edits:**
  - One date moved to another day (This date only) now shows on its new day, even when the page doesn't cover its original day.
  - Changing a series' time for every date no longer brings back dates deleted from it or drops one-date changes. Moving it to another day still clears them.
  - Making a series a one-off no longer lets an old change to its first date hide the new details.
  - Changing Repeats for "This date only" now says to choose This and following dates or Every date, instead of being ignored.
  - A far-off or mistyped date in a calendar address, or a bad date on an Edit link, shows "not found" instead of an error.

### Minutes, chow and console
- **Minutes buttons:** one or the other. Minutes imported with their original PDF have **View original**. All others have **Download PDF**: minutes made in the app, and corrected minutes imported from text.
- **Console reminder:** when Club events is a public calendar, the Upcoming Events step says dated events are public too, so members-only details stay out of them.
- **Chow fixes from testing odd requests:** a bad date on Assign or No chow is refused (it was a server error); swapping a night with itself is refused by the server too (it logged a swap and emailed the member); filling the rotation stops at two years ahead (a typo like the year 2400 made 19,470 nights).
- **Docs:** how to release a stack of PRs (README), and backups on the roadmap.

## 0.10.0 (2026-11-08)
- **Mobile first, tested:** a new test opens every page in a real browser at phone width (375px) and fails if any page scrolls sideways; CI runs it. It found and fixed: Your profile → Sign-in & passkeys, About, and the Security page's banned addresses. Long (IPv6) addresses now wrap instead of widening tables.
- **Passkey rename:** Save and Cancel appear only while you're renaming (a style rule was showing them all the time). The page now says "Sign out everywhere" includes this device.
- **Signed-in devices** (Security page): filter by member, tick several devices (or Tick all) and sign them out at once, e.g. everywhere for one member.
- **Admin actions** read as plain words ("Turned on admin mode until 9:22 AM (passkey)", "Signed out a device of Amy Able") and fit on a phone.
- **Online guests:** in the leader console a recent guest cycles absent → in person → online like a member does, and a typed-in guest has an In person / Online switch. The minutes show "(online)" after their name.
- **Email preferences:** a new **Email** tab on Your profile sets what you get. **The minutes:** the night they're submitted, with the PDF attached (as before) or as a link, or off. Business-item reminders and (for Secretaries) roster changes: on or off. Chow-night reminders go 7 days, 3 days and/or the day before (3 days by default). Sign-in links, swap requests, changes to your own chow night and security emails always go.
- **Meeting-day email** (on by default, can be turned off): the morning of each meeting, the time, "you're bringing chow tonight" when you are, the business you're looking after, what's on the calendar until the next meeting, and a link to last meeting's minutes. The daily `reminders` run (already on a timer) sends it along with chow reminders, never twice in one day.
- **Every optional email says how to change it:** the minutes ("To get a link instead of the PDF, or stop these emails"), the meeting-day email, chow and business reminders and the Secretaries' roster emails each end with a link to the Email tab.
- **One preview for ongoing work:** `deploy/preview.sh BRANCH NAME` keeps one address (NAME) while it moves between branches.
- **Birthday, month only:** on Your profile you can give just the month of your birthday (the day is optional). It shows as "July", and is saved to the roster sheet as `07`.
- **Leader console, business items:** with no due date, an item shows a **+ Due date** button that opens the date picker. An empty date box was easy to miss, especially on phones. "since Nov 5" now reads "first mentioned Nov 5", in the console and on the Business page.
- **Motions:** **+ Motion** opens a short form: who moved it (pick a member), what it was, who seconded it, and the result (Passed, Failed, Tabled, Withdrawn or Died for lack of a second). There's no default result, so it can't be recorded as passed by accident. It adds "Motion: Bob Baker moved to buy a new antenna, seconded by Amy Able. Passed." as a New Business item, as before.
- **Club calendars, kept by the app:** Club events, Planning and Community calendars live in the app (no Google needed), plus the weekly meeting (added once from the meeting day and time; skip or move a date like any event), chow nights, the minutes' dated Upcoming Events and the birthdays members share (never private ones, not even for officers; you always see your own).
- **Calendar page** (menu → Calendar): an **agenda** (8 weeks at a time, ← Earlier and Later →) and a **month** view (on phones, a dot per event; tap a day for details). Leaders and minutes editors **add, change and delete** events: start, optional end (a time, or a last day for several days), where, notes and **Repeats** (weekly, every 2 weeks, monthly on the 2nd or last Tuesday, monthly on a date, yearly on a date or on the 1st Thursday of January, optionally until a date). Changing or deleting one date of a repeating event asks: this date only, this and following dates, or every date. Every change is logged. Tap a calendar in the legend to hide or show it (remembered in that browser). Tap an event for its details (when, how it repeats, where, notes) in a box over the page, with **Edit** for leaders; agenda days are cards with every other row shaded.
- **Import .ics:** bring in events exported from Google Calendar, Outlook or Apple, into the calendar you pick. Re-importing updates rather than doubles; events already on the calendar are left alone; anything the app can't repeat the same way is listed to add by hand. Google's formatted descriptions come in as plain text.
- **Subscribe from your calendar app:** each member gets a private calendar link (Calendar → Subscribe) for Apple Calendar, Outlook, Google or a phone; tick which calendars to include (one at a time gives each its own color in the app). "Make a new link" replaces it.
- **Public calendars (optional):** an admin can make chosen club calendars public (`public_calendars` in club.yaml): a page and a feed anyone can see without signing in. Chow nights and birthdays are never public.
- **Copy to Google Calendar (optional):** the app can copy its calendars and chow nights to the club's Google Calendars, one way, touching only events it made. Off until named in club.yaml; see the README.
- **Home: Coming up is the calendar:** the next two weeks (nets, VE testing, fox hunts, Planning, Community, shared birthdays), not only the last minutes' Upcoming Events, with a link to the Calendar page. Chow keeps its own card.
- **Upcoming Events have dates:** in the leader console each event gets a start (date, optional time) and an optional end: an end time ("Sat, Nov 21, 10:00 AM – 1:00 PM: VE testing") or a last day for an event over several days ("Sat, Jun 26 – Sun, Jun 27: Field Day"). The minutes show the dates first. Events that have ended drop off when the next meeting starts. Events without a date work as before; they just don't go on the calendar.
- **Brand logos for links:** the Links editor's icon picker has a search box over about 3,400 brand logos from Simple Icons (Home Assistant, Discord, YouTube, GitHub…), stored with the app rather than loaded from the internet.
- **Emoji that always show:** every emoji in the app (✅ ✏️ 📅 🔴 💬 and the rest) is the device's own emoji where it has color emoji, and a matching line icon drawn by the app where it doesn't (some Linux computers showed empty boxes).
- **Your settings:** a **Settings** tab on Your profile, starting with the first day of the week (Sunday or Monday) for the calendar's month view. It's kept with your account, so it follows you to every device.
- **Links editor on phones:** each link is its own spaced card, and saved links start folded to their name (⋯ opens one).

## 0.9.1 (2026-11-08)
- **Readable deploy notes:** the Updates page shows a release's steps by hand formatted (numbered steps, commands in code blocks) instead of run-together lines. The README covers how to write them, and has a new **Server hardening** section (fail2ban for SSH, keys-only SSH, automatic updates, a firewall).
- **No club-specific server names in the repo:** docs, scripts and tests say "the server" and use example addresses.
- **0.9.0's notes fixed:** fail2ban was listed as a 0.9.0 deploy step; it's optional, and is now in the README's Server hardening section.

## 0.9.0 (2026-11-07)
- **New top bar:** ☰ sits on the left on every screen and slides out a drawer (Esc, a tap outside it or a link closes it). Every page is in the drawer; the bar keeps the logo (which goes Home), Start meeting, the clock, search and your name. On narrower screens search moves into the drawer, so nothing appears twice. The bar never wraps onto two lines.
- **Start meeting button:** for leaders, Lead a meeting is now a Start meeting pill that stays in the top bar even on phones. It turns amber and pulses from 30 minutes before the weekly meeting until 30 minutes after it starts (no pulse with reduced motion). The time comes from `club.meeting_time` (default `19:30`). While a meeting is being taken it becomes 🔴 Live meeting for everyone.
- **Home page:** `/` is now a "what's next" page: the next meeting and who brings chow, your chow night, swap requests waiting for your answer, your open business items as a checklist, the latest minutes and Upcoming Events. The archive moved to **Minutes** (`/minutes`); old `/?period=…` links redirect. The archive no longer labels every row "imported, unreviewed"; only a meeting in progress is marked.
- **Club links editable in the app:** admins in admin mode get ✏️ Edit links on the Links page: add, remove and reorder links, pick an icon from 21 in a visual picker (or **Auto**, guessed from the address: Teams or Zoom get a camera, Discord a chat bubble, YouTube a play button and so on), and choose where each shows: the Links page, the minutes' Club Links footer (and PDF), both, or neither. `mailto:` and `tel:` addresses work too. Saved edits live in `links.json` in the data folder and take over from `links:` in club.yaml.
- **Transparent profile pictures:** a PNG or WebP with a transparent background keeps it (saved as PNG); other pictures are still saved as JPEG.
- **Favicon for every browser:** `/favicon.ico` points at the club logo, so browsers that ask for it get it instead of an error.
- **Updates page refreshes itself:** while an update is waiting or running, the admin Updates page checks every few seconds and shows the result (the new version and ✅ or ❌) once the app is back, without a manual reload.
- **Security page** (admins, in admin mode; name menu → Security), in tabs: recent sign-ins (who, when, passkey or email link, address, device), every signed-in device with **Sign out**, failed and suspicious sign-in attempts, banned addresses with **Unban** and **Ban**, the admin log, and server basics. Addresses through Funnel are visitors' real internet addresses.
- **Automatic bans:** an internet address with 10 failed sign-in attempts in an hour (unknown email, used or made-up link, failed passkey, link limit) is blocked for 24 hours. LAN and tailnet addresses are never banned automatically, and an admin can't ban the address they're using.
  For SSH, see "Server hardening" in the README (fail2ban).
- **Sign-in links can't be redirected:** links in emails (sign-in, swaps, confirmations) now only use the app's own addresses (the HTTPS name, or the LAN address the request really reached). Before, a forged `Host` header could make the app email a member a working sign-in link pointing at someone else's server. Pages also send standard security headers: no framing by other sites, no guessing of file types, no full addresses leaked to other sites, and `noindex` for search engines.

## 0.8.0 (2026-11-07)
- **Docs in sync:** the README describes what the app does now, REQUIREMENTS says "leader" throughout (whoever leads that night: an officer or a member ticked `minutes_editor`), and Admin is a separate role. No member names or club email addresses in the repo, its examples or its history.
- **Works on phones:** the top bar is one row (logo, ☰, your name); ☰ opens the pages and the search box as a list. The search page has its own search box. Tables fit the screen; the roster list hides license, office and member-since on a phone, and the roster opens in card view on phones unless you picked the list. Business item buttons line up in even rows that are easy to tap, and typing in a field no longer zooms the page on iPhones. The Guests page keeps dates on one line and hides First visit on a phone.
- **Emoji icons** on buttons and labels (✏️ Edit, ✅ Mark done, 🔄 Reopen, 🔁 Ask to swap and so on) in place of plain text symbols.
- **Live meeting link:** while tonight's meeting is being taken, the top bar shows 🔴 Live meeting (for everyone, in place of Lead a meeting), linking to the minutes as they're written.
- **Favicon:** the club logo on a white circle, so it shows on any tab colour, and as the icon when the site is added to a phone's home screen.
- **Fixed:** more space between the Download PDF / View original buttons and the minutes.

## 0.7.1 (2026-11-07)
- **Fixed:** behind-the-scenes fixes to how the app updates itself.

## 0.7.0 (2026-11-07)
- **Light and dark themes:** the app follows your device, or pick Auto, Light or Dark under your name. Printed minutes stay black on white.
- **A more modern look:** striped, rounded tables (archive, roster, stats, guests), a top bar that shows where you are, and tidier buttons, fields, tabs and sign-in page.
- **Profile pictures:** add one on Your profile; it shows next to your name in the menu, on the roster, on your member page and on business items and comments. Members only; camera details and location are removed. Without one, your initials are shown.
- **Roster card view:** switch the Roster between the list and cards with pictures, using the two buttons next to the filters. Your choice is remembered.
- **Your profile** shows your name, callsign and license class as read-only fields, with a note that the Secretary keeps them.

## 0.6.0 (2026-11-07)
- **Chow pages are easier to read:** the schedule, past nights and rotation are tidy striped lists with date blocks, your own night highlighted, chips for what happened and for each member's limits, and buttons that open a small panel right under the row. Names are picked by typing, as on the Business page.
- **Skip someone's next turn** (chow limits): when a member covered a turn early, the next Fill passes over them once, with no make-up night owed.

## 0.5.1 (2026-11-07)
- **Fixed:** after an update, pages could look broken until you forced your browser to reload (it kept the old styles). Browsers now fetch the new styles and scripts by themselves.

## 0.5.0 (2026-11-07)
- **Business items are tracked** (new **Business** page in the top menu). Each Old or New Business item can have an owner and a due date, and it stays in Old Business every week until it's marked done; then it shows once as done in the minutes and drops off. Nothing gets lost when a week's minutes leave it out. Leaders change any item; members can update the ones they own, between meetings too. An item can be held for a later meeting (e.g. the President's State of the Club). Today's Old Business list was brought in as the starting point.
- **Reminders:** the owner of a business item with a due date gets an email a few days before it's due, and once if it goes past due (daily at 8 am; test mode applies).

- **Suggested follow-ups (AI, optional):** when minutes are submitted, an AI reads them and suggests business items with an owner and due date (e.g. "Amy will call the park by Dec 1"). They wait on the Business page, marked AI, until a leader accepts (after fixing them if needed) or dismisses each one; nothing is added on its own. Lines naming anyone on the never-record list are removed before any text is sent. Items with no change for six weeks are flagged "quiet for a while: still on?".

- **Comments on business items:** any member can add an update, a question or an offer to help; new comments show under the item in the console's Old Business. Each item's **History** shows every change (old and new wording, owner, dates) and the meeting it was made at. Submitted minutes keep the wording they had.
- **Notes for the next meeting:** on the Business page, a member can leave a note for an upcoming meeting (e.g. "I can't make the 12th; since last meeting I…"). The leader sees it in Community Reports and adds it to the reports with one click.

- **"Ask about" prompts (AI, optional):** in Community Reports, instead of a member's last three reports word for word, the leader sees one or two short prompts like "Ask how the 40 m dipole build went", made from those reports after each meeting. Routine and finished things are left out; what they actually said is one click away.

## 0.4.0 (2026-11-06)
- **Sign-in & passkeys** is now a tab of Your profile, which tidies the menu under your name.
- **Add a member** from the Roster page (Secretary or Treasurer). It writes a new row on the roster sheet and a "Joined" row on its Membership Log (values only), and the member shows up at once, at the end of the current chow round. Guests who've come often enough to be invited are listed, and picking one fills in their name and callsign. A callsign and license class are required. The Secretary gets an email, and Recent changes shows who added them.
- **Shorter lists:** the archive, the Stats page's Per meeting table and the Roster show a page at a time, with a "Show 10 / 25 / 50 / All" choice. The archive is one list, newest first, with year headings. The archive and Stats share a Period choice: this year (the default), the last 6 or 12 months, any year, or all time. The Roster shows Active members by default; pick another status or Everyone.
- **Chow schedule** (top menu → Chow), replacing the Secretary's spreadsheet. Officers get three tabs: Schedule, Rotation, and Past nights & changes.
  - Everyone sees who brings chow each night. A member can **ask another to swap nights**; the other member approves or declines, and both (and the Secretary) get an email.
  - Officers can assign nights, swap them, mark no-chow nights (holidays), and **fill the schedule** from the rotation. The rotation keeps the same order each round, new members join at the end of the current round, and limits like "can't do 3rd Thursdays" or "away in February" are respected: that member keeps their turn and gets the next night they can do.
  - On meeting night the console's Upcoming Chow step records whether chow was **provided or not**, and optionally what was brought (or that someone else brought it). Nights without chow are listed for the Treasurer (bylaws VII.F).
  - The minutes' Upcoming Chow section is no longer typed in: on Submit it's written from the schedule (tonight's chow, changes made tonight, and the next four nights).
  - `w8lbz-minutes chow-import` brings in the Secretary's .xlsx once.
- **Members are told when their details change:** an email listing what changed and who changed it, sent to both the old and new address after an email change.
- **Your profile:** members update their own email, phone, address and birthday (month and day only), and choose which of these other members can see. Changes go straight to the roster sheet (values only), the Secretary gets an email, and officers can undo any change from **Recent changes**. A new email takes effect only after it's confirmed from the new address.
- **Fixed:** a roster sync (nightly, or `w8lbz-minutes roster`) now shows up right away; before, the app kept the old roster until it was restarted.
- **Contact details on member pages:** click a name on the Roster for their details. Officers see everyone's (with unshared ones marked); members see what each person chose to share, and all of their own.

## 0.3.0 (2026-11-06)
- **About page,** opened from a new menu under your name (with Your account and Sign out): version, credits, license, source code, what's new and the open-source packages the app uses. Replaces the version link in the footer.
- **Licensed under the GNU AGPL, version 3 or later.** Copyright James Flattery.

## 0.2.0 (2026-11-06)
- **Minutes are emailed on Submit:** the PDF goes to every active roster member with an address, one copy each, with Reply-To set to the leader. Test mode (`email.only_to`) is still on. Leaders see the result on the minutes page. (#7)
- **`w8lbz-minutes delete-meeting`** removes test minutes. (#7)
- **Roster page:** name, callsign, license class, office, status and member since. No contact details. (#8)
- **Archive by year:** collapsible years, a year filter, and 26 meetings per page. (#9)
- **Stats by period:** last 12 months (default), all time, or a single year. (#9)
- **Share bubble** in the leader console copies the live-view link. (#10)
- **Guests page:** guests who joined move to a "Became members" section, with the average number of visits before joining. (#11)
- **Bylaws page,** with the text kept out of the public repo. (#12)
- **Links page** with icons, in the top menu. (#13)
- **Fixed:** choosing "All years" on the archive showed a raw error. (#9)

## 0.1.0 (2026-11-06)
- Archive of past minutes imported from the old PDFs and text files, with search, member pages, stats and guest tracking.
- Roster synced daily from the club Google Sheet. (#1)
- Leader console for taking minutes live; members can follow along. (#2)
- Deployment scripts for the server. (#3)
- Sign-in with passkeys, or an emailed one-time link. (#4)
- Guest picker with one-tap recent guests, and reminders to invite regular guests to apply. (#5)
