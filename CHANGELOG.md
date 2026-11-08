# Changelog

What changed in each version of W8LBZ Minutes. Planned work is in [ROADMAP.md](ROADMAP.md).

Versions follow [semantic versioning](https://semver.org): while the app is 0.x, the middle number goes up for new features and the last for fixes. Each version is a git tag (`v0.2.0`), and the app's footer shows the version that's running.

## Unreleased
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
- **Calendar page** (menu → Calendar): an **agenda** (8 weeks at a time, ← Earlier and Later →) and a **month** view (on phones, a dot per event; tap a day for details). Leaders and minutes editors **add, change and delete** events: start, optional end (a time, or a last day for several days), where, notes and **Repeats** (weekly, every 2 weeks, monthly on the 2nd or last Tuesday, monthly on a date, yearly on a date or on the 1st Thursday of January, optionally until a date). Changing or deleting one date of a repeating event asks: this date only, this and following dates, or every date. Every change is logged. Tap a calendar in the legend to hide or show it (remembered in that browser).
- **Import .ics:** bring in events exported from Google Calendar, Outlook or Apple, into the calendar you pick. Re-importing updates rather than doubles; events already on the calendar are left alone; anything the app can't repeat the same way is listed to add by hand. Google's formatted descriptions come in as plain text.
- **Subscribe from your calendar app:** each member gets a private calendar link (Calendar → Subscribe) for Apple Calendar, Outlook, Google or a phone; tick which calendars to include (one at a time gives each its own color in the app). "Make a new link" replaces it.
- **Public calendars (optional):** an admin can make chosen club calendars public (`public_calendars` in club.yaml): a page and a feed anyone can see without signing in. Chow nights and birthdays are never public.
- **Copy to Google Calendar (optional):** the app can copy its calendars and chow nights to the club's Google Calendars, one way, touching only events it made. Off until named in club.yaml; see the README.
- **Home: Coming up is the calendar:** the next two weeks (nets, VE testing, fox hunts, Planning, Community, shared birthdays), not only the last minutes' Upcoming Events, with a link to the Calendar page. Chow keeps its own card.
- **Upcoming Events have dates:** in the leader console each event gets a start (date, optional time) and an optional end: an end time ("Sat, Nov 21, 10:00 AM – 1:00 PM: VE testing") or a last day for an event over several days ("Sat, Jun 26 – Sun, Jun 27: Field Day"). The minutes show the dates first. Events that have ended drop off when the next meeting starts. Events without a date work as before; they just don't go on the calendar.
- **Brand logos for links:** the Links editor's icon picker has a search box over about 3,400 brand logos from Simple Icons (Home Assistant, Discord, YouTube, GitHub…), stored with the app rather than loaded from the internet.
- **Emoji that always show:** every emoji in the app (✅ ✏️ 📅 🔴 💬 and the rest) is the device's own emoji where it has color emoji, and a matching line icon drawn by the app where it doesn't (some Linux computers showed empty boxes).
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
