# Roadmap

Planned work, roughly in priority order. Each item becomes its own branch and PR; when it merges, it leaves this file and gets a line in the changelog.

Finished work moves to [CHANGELOG.md](CHANGELOG.md).

## Next
- **Email the minutes to the whole club:** remove test mode (`email.only_to`) once the club is ready.

## Leader console
- **Suggest edits during a live meeting:** an editor who isn't leading proposes changes to the live minutes; the leader accepts or rejects each one (REQUIREMENTS §3).
- **Weekly drawing and 50/50 tracking:** record each drawing in the Winners step (winner, amount, registered or not), and keep a running pot for the weekly drawing, so the minutes show where the pot sits and a light stats page shows who has won and how much. Later this feeds the Treasurer's records (below).
- **Motions as records:** keep a motion's mover, seconder and result as data, not just text, so bylaws amendments and spending can be traced to the motion that approved them (see Bylaws).

## Look and feel: ideas to consider
- **Auto icons from brand logos:** Auto could pick the brand logo for known sites (Discord, YouTube, GitHub, Facebook) instead of the generic chat or play icon.
- **Calmer Business cards:** History and Comment buttons only on hover or tap, and a one-line intro.
- **Meeting page:** "Open in the leader console" smaller and below the title, so the minutes come first.

## Backups
Today the server relies on Proxmox snapshots of the container. A backup the app makes itself, kept somewhere else:
- **What:** the whole data folder (minutes, chow, business items, calendars, settings, the sign-in database), in one archive.
- **Encrypted** before it leaves the server, with a passphrase only the admins hold, so the storage provider can't read club data.
- **Where:** Google Drive (the service account already exists), S3 or S3-compatible storage (Backblaze B2, Wasabi), or another server over SFTP; one or more at a time.
- **Configured from the app** (admin mode): destinations, schedule (e.g. nightly), how many to keep, and the last backup's result, with an email to the admins if one fails.
- **Download a backup** from the app, encrypted, for safekeeping on your own computer.
- **Restore:** a documented, tested way to restore onto a fresh server, and a periodic check that a backup actually opens.

## Admin tools
- **SSH bans on the Security page:** fail2ban's SSH bans listed with Unban, through a small root-owned helper (like the update unit), since the app itself can't run fail2ban-client.
- **Filter the Admin actions log** by admin, kind of action and date, with paging (it shows the last 50 today).
- **Act as a member:** View as is read-only today. A later option could let an admin make changes as the member to reproduce a problem, clearly logged and with emails still going to the admin, not the member.

## Members and roster management
The Google Sheet is the source of truth today; the app writes values back to it (values only, never formatting or structure). The plan below retires it.

### Roster in the app (retire the Google Sheet)
Nobody uses the sheet directly any more; one other club tool on the same server reads four columns from it (callsign, name, email, officer). Serve that from the app and the app can own the roster outright, with no Google dependency left.
- **Roster API:** `GET /api/roster`, read-only: active members' callsign, name, email and officer flag. It answers only requests made on the server itself (not through the web proxy) that carry a token kept on the server, outside the repo; every call is logged.
- **Edit everything in the app:** roster keepers (Secretary, Treasurer) and admins edit what the sheet holds today: name, callsign, email, titles, officer and board, license class, status, aliases, `minutes_editor` / `calendar_editor`, member since and the Membership Log. Members keep editing their own contact details. Every change logged, with undo, as profile changes are now.
- **Offices:** a page listing the club's offices (President, Vice President, Secretary, Treasurer, board seats; the list from the bylaws, kept in club.yaml) and who holds each. Changing a holder (after an election, or a resignation) is one step: pick the member, give the date, and the app sets their title and officer/board flags, takes them off the previous holder, and records both as Membership Log events. That's the "Officer history" item below, built in. Who may change offices: admins in admin mode, with the change emailed to the officers (open question: also the Secretary?).
- **Move over once:** import the sheet and its Membership Log tab, show what differs, then turn off the sync and the write-back. The sheet stays as a read-only archive.
- **Then remove** the Google Sheets code and the service account's access to the sheet. With no copy to Google Calendar either (decided 2026-11-08), the app needs no Google account at all.
- **Before this:** Backups (below). Once the roster lives only in the app, the app's own encrypted, off-server backup is the only copy.

- **Celebrations:** birthdays (opt-in; a month-only birthday at the first meeting of that month) and membership anniversaries shown in the console on meeting night ("Happy birthday, …", "5 years as a member").
- **Guest to member:** a fuller process (application, vote, initiation fee and dues) behind today's "Add a member".
- **Associate members** (GMRS or unlicensed), only if the bylaws add them; today a member must hold an amateur license.
- **Officer history:** when someone starts or ends an office, recorded as Membership Log events, so the app can show who held each office when.
- **Admin page for officers:** sync the roster now, email test mode on/off, delete test minutes, review imported minutes.

## Bylaws
- **Now:** a link in the app (Club Links) to the Google Doc version.
- **Later:** the bylaws as text under version control, with each amendment tied to the meeting and motion that adopted it, and a history page showing what changed and when. Open question: members-only or public.

## Treasurer: dues and Certificates of Indebtedness
For the treasurer, later. The bylaws (VII) set dues at $182/year ($3.50 a week), a $25 initiation fee, and a $100 Certificate of Indebtedness within two years of joining; members four weeks in arrears can be suspended.
- **Record payments:** dues, initiation fees and certificate purchases, per member, with date and amount. Missed-chow charges show up here as well.
- **Standing:** who's paid up, who's behind, and who still owes a certificate (the bylaws require the roster to show whether each member has one).
- **Officers only:** treasurer data is never shown to other members.
- **On each member's Home page:** what they owe and when (dues, the Certificate of Indebtedness, any missed-chow charges). Only their own figures; other members never see them.
- **Not a bookkeeping system:** if the treasurer uses QuickBooks (to be confirmed), the app exports what QuickBooks needs rather than duplicating it.

## AI assistance
Suggested follow-ups are done. Next, using the typed minutes we already have (no audio needed):
- **Summaries:** a short "this week at the club" paragraph at the top of the minutes email, and later for the Discord post.
- **Ask the archive:** questions like "when did we last discuss the tower?", answered with links to the minutes they came from.
- **Ground rules:**
  - A person always approves; the AI never adds or changes anything on its own, and AI-drafted text is marked until someone accepts it.
  - Names on the never-record list are removed before any text leaves the server. The club is told that minutes text goes to an AI service.
  - Through OpenRouter, so the model can be chosen and changed; the expected cost is pennies a month.

## Later
- **Audio capture and live transcription** (see REQUIREMENTS.md §4.1–4.2): browser room capture plus a meeting bot, cloud speech-to-text, OpenRouter drafting.
- **Discord:** a post-meeting summary and weekly reminders.
- **Clubhouse DNS override:** the club router resolves the app's hostname to the server's LAN address, and the server serves HTTPS locally with the Tailscale-issued certificate. Then the same URL works at the clubhouse without an internet round trip, even when the internet is down.
- **Permanent hostname** (e.g. `minutes.w8lbz.org`) once the club controls its DNS. Members re-add passkeys once.
- **Import review:** a page to mark imported historical minutes as reviewed, and fix their attributions.
