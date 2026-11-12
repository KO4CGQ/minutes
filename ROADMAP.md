# Roadmap

Planned work, roughly in priority order. Each item becomes its own branch and PR; when it merges, it leaves this file and gets a line in the changelog.

Finished work moves to [CHANGELOG.md](CHANGELOG.md).

## 1.0.0: Club Minutes for any club
The rename becomes 1.0.0: from then on the settings, folders, services and command names stay put (a change to them is a 2.0). Built on the `rename` branch, released once, then the history is squashed in place. In order:

### Club rules out of the code
- **The meeting's steps.** The console's steps and their hints (Winners and the 50/50 drawing, the Financial Report) are W8LBZ's agenda: make the step list a setting with today's as the example, and the drawing step an optional module, switched like chow (`modules:` in club.yaml).
- **Other defaults to check while there:** the meeting day (Thursday) and time zone (America/New_York) defaults, Teams as the meeting link in the example config, US date formats, and the PDF importer for old minutes (a W8LBZ archive format: keep as an optional tool, documented as such, or drop).

### Look
- **Theme:** the club's colours on Site (the top bar, buttons and links: a few colour pickers setting the CSS variables `style.css` already uses, checked for readable contrast in light and dark), so another club's app looks like theirs. Custom CSS beyond that, if ever, admins only: the security policy already stops a stylesheet loading anything from elsewhere.

### A new club can start
- **First run on an empty server:** today data comes from W8LBZ's backups and archive. Needed: an empty start (no roster-sheet.json, no meetings) that works, the first admin from club.yaml signing in by emailed link, then adding members on the Roster page; a short "Getting started" in the server admin guide.
- **A demo club:** invented data (Example Radio Club, W0XYZ, Amy Able, Bob Baker...) as a fixture: for the screenshots, for previews without the club's data, and for anyone trying the app.

### Docs and repo
- **README made presentable:** what it is in a paragraph, screenshots (taken from the demo club, never real data: phone and desktop, light and dark), the feature list, install in three lines, links to the guides.
- **Drop REQUIREMENTS.md and ROADMAP.md** from the repo; fix every link to them (README, CHANGELOG, docs). Both are copied to the maintainer's private notes first, kept until whatever is still wanted is opened as GitHub Issues.
- **CHANGELOG starts at 1.0.0:** one section describing what the app does, instead of the 0.x history.
- The guides (members, officers, server admin, developers) reread for W8LBZ assumptions; `club.example.yaml` covers every new setting.

### Release and the clean start
- **Scan the tree first:** the squash keeps today's files, so they must hold no club data (names, callsigns, emails, phones, addresses), checked file by file (tests, fixtures, docs, examples) besides gitleaks.
- **Squash in place:** one commit with the 1.0.0 tree, force-pushed to main (needs force-push allowed on main for that moment). Delete the v0.x tags and GitHub Releases, then tag v1.0.0 (the Release workflow publishes it). Old PRs keep their pages but their commit links stop working; Dependabot and code scanning start over. Clones elsewhere (the dev box, CT103) fetch the new main; CT103's updater resets to tags, so it follows.
- **Install on CT103:** a snapshot, then the move script (the developer's private copy) with v1.0.0; it also points CT103's checkout at the new history.
- **The repo stays `KO4CGQ/minutes`** (decided 2026-11-11: renaming it would add work for little gain). Jim does the force-push himself, or allows it for that moment.

## Leader console
- **Upcoming Events → From the calendar: one line per repeating event.** A weekly event (the nets) fills the list with its next several dates. A tick beside "the next __" (on by default) shows only each repeating event's next date, so one-off events aren't pushed off the list. Also: "the next __" sits out of line with the "From the calendar" heading; line them up.
- **Weekly drawing and 50/50 tracking:** record each drawing in the Winners step (winner, amount, registered or not), and keep a running pot for the weekly drawing, so the minutes show where the pot sits and a light stats page shows who has won and how much. Later this feeds the Treasurer's records (below): the club's share of each 50/50, and anything donated back, as income, so Spending can show money in beside money out. Today the Winners section is free text and isn't read.

## Look and feel: ideas to consider
- **Menu (nav panel) set from the Links page:** move pages and links up and down, add dividers, and choose which roles see each one (members, leaders, officers, admins), instead of the fixed list.
- **24-hour clock:** a setting on Your profile → Settings to show times as 19:30 instead of 7:30 PM, across the app (and its emails, if wanted).
- **Donate button:** a link in the app to a donation page the admin sets (the app handles no payments). Open question: also a "Support the app" link to the developer's sponsor page.
- **Auto icons from brand logos:** Auto could pick the brand logo for known sites (Discord, YouTube, GitHub, Facebook) instead of the generic chat or play icon.

## Backups
Built (server admin guide → Backups): encrypted, scheduled and configured from the app, to S3 and/or Google Drive. Later, if wanted:
- **SFTP** to another server, as a third destination.
- **A restore drill:** a periodic test restore onto a scratch container, beyond the check that each backup opens.

## Admin tools
- **Release Dependabot security fixes by themselves** (decided 2026-11-09: security fixes only, patch or minor versions, every check green): Dependabot's security PRs auto-merge; a workflow then opens a "Release X.Y.Z" PR (patch bump, a changelog line naming the advisory) that auto-merges once its checks pass and is tagged; the nightly auto-update installs it. Needs a token as an Actions secret (a fine-grained token for this repo, contents and pull requests read/write, or a small GitHub App), because changes made with the built-in token don't start other workflows (the required checks, the Release workflow), and "Allow auto-merge" in the repo settings. Then revisit installing **security** releases within the hour instead of at the nightly hour (never on meeting night or while minutes are being taken: one built-in rule, no settings); decided 2026-11-09 not to add an "immediately" mode or blackout windows before that, since the nightly hour and Update now cover it.
- **Moving the app to a new address** (e.g. members.example.org): change `auth.rp_id` in club.yaml and the proxy; club links to the app's pages already follow. Passkeys are tied to the address, so every member signs in once by emailed link and adds a new one (the offer after a link sign-in does this); old feed and sign-in links stop working. **A sub-path** (example.org/members) is a bigger job: every page links to `/…` from the site's root, so the app would need a path prefix throughout (FastAPI `root_path`, and the templates, scripts and redirects).
- **SSH bans on the Security page:** fail2ban's SSH bans listed with Unban, through a small root-owned helper (like the update unit), since the app itself can't run fail2ban-client.
- **Filter the Admin actions log** by admin, kind of action and date, with paging (it shows the last 50 today).
- **Act as a member:** View as is read-only today. A later option could let an admin make changes as the member to reproduce a problem, clearly logged and with emails still going to the admin, not the member.

## Members and roster management
The roster lives in the app (since 0.13.0); the Discord bot reads it from the roster API.
- **Guest to member:** a fuller process (application, vote, initiation fee and dues) behind today's "Add a member".
- **Admin page for officers:** sync the roster now, email test mode on/off, delete test minutes, review imported minutes.

## Bylaws
- The bylaws as text under version control, with each amendment tied to the meeting and motion that adopted it, and a history page showing what changed and when. Open question: members-only or public.

## Treasurer
Built (officers guide → Treasurer): who owes what, payments, missed-chow charges, certificates, bills from the minutes, and each member's own figures on Home. Next:
- **Monthly report:** fill the Financial Report step from the recorded money, with account balances.
- **Bond on the roster:** show whether each member has bought their bond (Certificate of Indebtedness) on the Roster page.
- **Bookkeeping, if wanted:** every entry already has an account and whole-cent amounts, so a proper ledger (opening balances, transfers between accounts) or a QuickBooks export can be built on it without re-keying.

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
- **Discord:** a post-meeting summary and weekly reminders, and a post when a meeting starts (when the leader opens the console), with the link to join online.
- **Anubis proof-of-work in front of public pages** (sign-in, public calendar), to turn away scrapers. Signed-in members never see it:
  - Keep Anubis's pass cookie at least as long as a session (`session_days`), so passing it once at sign-in covers the whole session.
  - Never challenge machine clients: the `.ics` feeds, `/api/roster` and the live-meeting polling.
  - Skip the challenge on the LAN with an Anubis rule on the club's network address ranges. This needs the real client address: a proxy in front must pass it on, and Anubis must not trust a forwarded address a client sends itself.
  - Don't let requests skip the challenge just because they carry a session cookie. Anubis can't check that the cookie is valid, so a bot could send a fake one.
- **Import review:** a page to mark imported historical minutes as reviewed, and fix their attributions.
