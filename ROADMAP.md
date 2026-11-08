# Roadmap

Planned work, roughly in priority order. Each item becomes its own branch and PR; when it merges, it leaves this file and gets a line in the changelog.

Finished work moves to [CHANGELOG.md](CHANGELOG.md).

## Next
- **Chow list, what's left** (see below): the printable chow list.
- **Email the minutes to the whole club:** remove test mode (`email.only_to`) once the club is ready.

## Chow list, what's left
The schedule, rotation, swaps, reminders, meeting-night records and the minutes' Upcoming Chow section are done.
- **Printable chow list:** a PDF of the nights still to come, emailed only when the list is released or changes (not with every set of minutes), replacing the Secretary's spreadsheet.

## Leader console
- **Suggest edits during a live meeting:** an editor who isn't leading proposes changes to the live minutes; the leader accepts or rejects each one (REQUIREMENTS §3).
- **Weekly drawing and 50/50 tracking:** record each drawing in the Winners step (winner, amount, registered or not), and keep a running pot for the weekly drawing, so the minutes show where the pot sits and a light stats page shows who has won and how much. Later this feeds the Treasurer's records (below).
- **Motions as records:** keep a motion's mover, seconder and result as data, not just text, so bylaws amendments and spending can be traced to the motion that approved them (see Bylaws).

## Look and feel: ideas to consider
- **Unified search:** one search box for minutes, members (name, callsign), business items and chow nights, with results grouped by kind. Minutes already have full-text search; the others are small lists, so this is a modest change.
- **Line icons instead of emoji** on buttons and labels: emoji show as empty boxes on computers without an emoji font. The Links page's icon set (`_icons.html`) could cover them.
- **More link icons, hosted locally:** add [Simple Icons](https://simpleicons.org) (CC0 brand logos: Discord, YouTube, GitHub, Facebook…) as SVGs copied into the repo, never from a CDN, and give the icon picker a search box like the combo box (`combo.js`). Font Awesome Free (CC BY 4.0, credit needed) is the fallback for general icons; Nerd Fonts is a terminal font, and svgl's logos have mixed licenses, so neither fits.
- **Calmer Business cards:** History and Comment buttons only on hover or tap, and a one-line intro.
- **Meeting page:** "Open in the leader console" smaller and below the title, so the minutes come first.

## Admin tools
- **SSH bans on the Security page:** fail2ban's SSH bans listed with Unban, through a small root-owned helper (like the update unit), since the app itself can't run fail2ban-client.
- **Filter the Admin actions log** by admin, kind of action and date, with paging (it shows the last 50 today).
- **Act as a member:** View as is read-only today. A later option could let an admin make changes as the member to reproduce a problem, clearly logged and with emails still going to the admin, not the member.

## Club calendars
Chow nights and dated Upcoming Events are synced (see the README). Still to do:
- **The weekly meeting** on Meetings & Events, if the club doesn't already keep it there as a recurring event (then the app would only add cancellations, e.g. holidays).
- **New repeating events from the Calendar page:** changing one date, this and following, or every date works; creating a new repeating event (a new net) still needs Google Calendar.
- **Calendar editors:** editing is for leaders and minutes editors; a separate `calendar_editor` tick on the roster sheet could let others (e.g. the net manager) edit too.
- **Who changed what:** a page for officers listing `calendar-log.jsonl`.
- **Planning:** due dates of tracked business items.
- **Community Events:** non-club happenings in the area; the app could show them, edited in Google Calendar.
- The service account is added to each calendar with "Make changes to events"; the app only touches events it created.

## Members and roster management
The Google Sheet stays the source of truth; the app writes values back to it (values only, never formatting or structure).
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
