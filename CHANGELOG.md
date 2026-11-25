# Changelog

What changed in each version of Club Minutes. Ideas and requests: the repository's [Issues](https://github.com/KO4CGQ/minutes/issues). Security fixes are summed up here; their details are in the repository's [security advisories](https://github.com/KO4CGQ/minutes/security/advisories), once the fix is out.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/). Versions follow [semantic versioning](https://semver.org): the first number goes up for a change to settings, folders or command names that a club has to act on (a 2.0), the middle for new features, the last for fixes. Each version is a git tag (`v1.0.0`), and the app's footer shows the version that's running.

## Unreleased

### Added
- **Clubs that aren't amateur radio clubs:** a new **Amateur radio club** setting on Admin → Site. Unticked, callsigns and license classes aren't shown or asked for anywhere, and emails end "Thanks," instead of "73,". Clubs that have a callsign keep it ticked. Turning it off hides callsigns without deleting them.
- Members no longer need a callsign or license class to join the roster: someone without one is shown by name.
- **Cancelling a meeting night:** delete that date of the weekly meeting, or add a "No meeting" entry on the club's calendar (the meeting then drops off the calendar that night). Either one takes the night off Home, the business notes and the leader console, and the next week's meeting shows instead.
- **Admin → Security → Uploads** shows how much space pictures take and how much the disk has free, with a warning when it's nearly full.
- **Dues by the week, the month or the year:** the Treasurer sets the period with the amount (a yearly club picks the day dues fall due), and "behind after" counts in that period. Meetings, cancellations and schedule changes never change what anyone owes. A weekly club's dues stay the same.

### Changed
- **When a member leaves**, their email, phone, address, birthday, photo and calendar feed link are deleted 90 days after they're made Inactive or a Silent Key (or taken off the roster). Their name, callsign, years of membership and offices are kept. Made Active again within the 90 days, nothing is lost; roster keepers see the date on the member's page. If more than a quarter of the roster leaves in one night (a roster mistake, say), the countdown waits until an admin confirms it on the Admin page, and roster keepers get a list each Monday of who is waiting and when.
- In minutes made from now on, a faint line separates the items in every section, as it did in Community Reports, on the page and in the PDF. Minutes already published look as they did.
- In the leader console, members' comments on a business item show their first two lines, formatted and ending in …, with **Show more** for the rest.
- The leader console's **Submit minutes** buttons show only once the finished minutes are on screen, so they're never submitted unseen.
- The treasurer's **Delete** asks about the entry by its amount, date and member, and the leader console's Other reports show a character count, as the other boxes do.
- In the leader console's Community Reports, **Nothing new** shows only while the report box is empty.
- In the leader console's Community Reports, **Other reports** is a card under the floor, closed until you open it (or it has something in it), with a box as wide as the floor's.
- Starting a meeting in the leader console marks you present: in person on the meeting place's network, online from anywhere else.
- A member's **New callsign** no longer needs the backup passphrase or moves any records: they keep everything, links to their page use the new callsign, and old links still work.

### Fixed
- A correction to a meeting's attendance now updates the quorum line in its minutes, and the leader console checks the quorum again when a motion is added.
- On iPhones, **Download PDF** opened the PDF in place of the minutes (or, in Firefox, did nothing) instead of saving it.
- Minutes from before 1.2 no longer show a line between community reports: they look as they did when they were made.

### Security
- Fixes the server's log keeping members' calendar feed addresses and sign-in link tokens.
- Someone signed out can no longer tell which video rooms are for members only.
- Someone who knows a member's email address can no longer stop them signing in with an emailed link for an hour.

## 1.2.0 (2026-11-22)

### Added
- **Pictures** on community reports, business items, motions, comments and notes for the next meeting, up to four each. Members only, never public, and saved without the hidden details a phone adds (such as where it was taken).
- **Formatting** in reports, business items, comments and the leader console: bold, italic, lists, quotes, code and links (with words to show and a numbered QR code, if you like), from a toolbar above the box. **Preview** shows how it will look.
- **@ mentions:** a member mentioned shows as their name tag, linked to their page.
- **Business comments in the minutes:** the leader can add members' comments on a business item, and what's said about it at the meeting, under the item.
- **Quorum check** (optional): the leader console shows whether there's a quorum and the votes a motion needs, and the minutes record it.

### Changed
- **Published minutes stay as they were** when the roster, names, agenda or club details change later.
- A community report's name shows once, as the tag leading the report, in the PDF too. A club can show full names instead of short ones, and short names now tell apart members who share a first name and last initial.
- Business items, comments and notes can run to 5,000 characters, with a count; longer text is refused instead of cut off.
- **Download PDF** saves the file and keeps the minutes page open.
- In the formatting toolbar, **Preview** is an eye icon, and while the preview shows it turns into a pencil to go back to editing.

### Fixed
- On iPhones, an empty Due or Hold date box on the Business page shrank to almost nothing.
- On a phone, the top bar ran off the screen while a meeting's minutes were open.
- Keyboard focus was hard to see.
- A name the club never records could reach the minutes through a business item, a note or a motion.

### Security
- Hardening of uploads and of how the server updates itself. Update soon.

### For server admins
- This release upgrades the data (going back to 1.1 means restoring a backup made before updating) and makes root the owner of the app's code. The server admin guide has the details.

## 1.1.0 (2026-11-19)
- **Custom roles:** a club decides what each role may do and who has it (by office, or member by member), and can add roles of its own. Leading meetings (Meeting leader) and fixing published minutes (Minutes editor) are now separate roles, and a Secretary role gets the roster emails.
- **Correct these minutes:** the meeting's leader or recorder, a Minutes editor or an admin can correct minutes in the archive, with a reason and a side-by-side preview. Earlier versions are kept (for editors only, if a correction takes out private details), and members can be emailed what changed. Imported minutes can be marked as reviewed.
- **Video meetings (optional):** members join the club's own Jitsi Meet server already signed in; officers and meeting leaders host, and guests wait in the lobby. Each room has its own rules for who comes straight in and what guests may do, members can open rooms of their own, and a calendar event can name a room. Setting up the server: the video meetings guide, in Help for admins.
- **Recover admin access:** if the club can't reach its admins, the officers can make a member an admin and suspend the others, with approvals, a waiting period and every member told at each step. The last active admin can't be removed. The officers guide covers it, and choosing an admin, with a template admin agreement.
- **Email changes:** a new address takes effect once its owner confirms it, and the change signs them out everywhere. Only an admin can change another office holder's or an admin's email, status or callsign.
- **Support the app:** a **Support Club Minutes** link (Ko-fi) at the foot of the menu, and both ways to give (GitHub Sponsors or Ko-fi) on the About page.
- **Smaller changes:**
    - Replies to the minutes email go to whoever took them and whoever led the meeting, each only if they share their email with members; otherwise to the club's address.
    - **Join online**, on the calendar and Home, opens the meeting in a new tab.
    - The chow schedule shows every upcoming night, 16 to a page.
- **Fixed:** a member changing their own email never got the new one; a license class the roster spells its own way ("Extra") showed as none when editing that member, and saving their details cleared it; a picture in Help opened full size sits in the middle of the screen, with its ✕ at its corner.
- **Security:** fixes from a security review, including ways a member could see or change more than their role allows, and a hardening of the updater. Update soon.
- **Removed:** restoring backups made before 1.0.0 (a 0.x install restores them).
- **For server admins:** 1.1.0 upgrades the data, so going back to 1.0.x means restoring a backup made before updating (a failed update goes back by itself, data included). `club-minutes rename-id` finishes a callsign change that stopped partway (server admin guide). A member made Inactive or a Silent Key loses their saved choices, passkeys and sign-in records that night (the first night after updating, those already so); old sign-in records, the Email log, the admin log and the roster API's log keep 90 days.

## 1.0.1 (2026-11-15)
- **Security:** fixes a way a roster keeper could gain another member's access, an admin's included. Update soon.

## 1.0.0 (2026-11-13)
**Initial release.** Club Minutes has been redesigned so any amateur radio club can set it up and shape it to its own needs (its agenda, offices, dues, calendars and look) from the app. Earlier test versions (0.x) aren't listed here. From 1.0.0 on, settings, folders, services and command names stay the same from one version to the next; a change a club would have to act on waits for 2.0 (the developer guide's Compatibility section lists what that covers).

- **Meeting night:** the leader console takes the minutes live, step by step through the club's agenda (its own sections, order and hints), with attendance in person and online, guests, reports, Old and New Business, motions (mover, seconder, result) and upcoming events from the calendar. Members follow along live. One console holds the meeting at a time (others see it read-only, with Take over). Submit publishes the minutes and emails them to every member.
- **The minutes:** every meeting as a page and a PDF with the club's logo and colours, attendance stats, per-member history and guest tracking; one search box for minutes, members (nicknames too, and members' other spellings), guests, business, chow nights and events. Older minutes can be imported from PDFs with a plain parser (no AI), marked "imported, unreviewed".
- **Home:** what's next for each member: the next meeting (with Join online), their chow night and swap requests, their business items and dues, the latest minutes and what's coming up.
- **Business items:** Old and New Business carried from week to week, with owners, due dates, reminders, comments and history, and notes for the next meeting.
- **Calendar:** the club's events, planning and community calendars kept by the app, with chow nights, shared birthdays, business due dates and holidays for any country, state or region (plus the club's own); a place and an online meeting link per event; .ics import, private subscription links for phones and calendar apps, and calendars the club chooses to make public. The weekly meeting is set from Site → Club, from a Starting date on.
- **Members:** sign-in with a passkey or an emailed one-time link (no passwords); the roster kept in the app, with profiles, pictures, offices and their holders over time, and who changed what; each member chooses which emails they get, including a meeting-day email.
- **Chow** (an optional module): the rotation, swaps between members, reminders before your night, and who provided, missed or covered each night.
- **Treasurer:** who owes what (dues, fees, an optional certificate each member buys once), payments and expenses, regular bills and bills read at meetings, and committee funds; each member sees their own figures on Home.
- **Site** (admins): the club's name, callsign, logo, colours (light and dark), meeting night, place and online link, agenda, the club's offices and roles (what each allows and who has it), optional features, holidays and election days, bylaws references, date formats and names never recorded, set in the app over the server's club.yaml.
- **Admin and security:** admin mode for an hour at a time, confirmed with a passkey or an emailed link; admins managed in the app; viewing the app as another member (read-only); the guides in the app, with pictures and in the search (Help for members and officers, the server admin guide under Admin); a Security page with sign-ins, devices (sign one out), failed attempts with details, and bans (automatic after repeated failures from an internet address); changes accepted only from the app's own pages; a strict Content Security Policy.
- **Running a server:** one script installs it on Debian (`setup.sh`), with a hardening guide (fail2ban, keys-only SSH, automatic updates, the app's port closed to the LAN), and `club-minutes first-member` starts an empty roster; updates from the app (Update now, or nightly), rolled back if the new version doesn't start; a new version that stores data differently reshapes it by itself (after a copy, put back if it doesn't start), so updates within 1.x need no steps by hand; encrypted backups on a schedule, kept on the server and optionally sent to Backblaze B2 or Google Drive; an optional staging server with a one-way copy of the live data, whose email goes only to whoever's testing.
- **Every page works on a phone,** in light and dark mode. Every change is checked by the tests in Chromium, Firefox and WebKit (phone width, dark-mode contrast, the security policy), lint (ruff, shellcheck, zizmor), guards that what a club relies on is still there, that the screenshots match their pages and the docs' links work, and that no real-looking contact details get in, CodeQL, pip-audit and gitleaks. Vulnerabilities can be reported privately (SECURITY.md).
- **Deploy notes:** a server installed before 1.0.0 moves to the new names once, by hand, with the maintainer's move script (Update now can't do it, and this note keeps the nightly update from trying). A new install needs nothing more: `setup.sh`, then the server admin guide's Getting started.
