# Roadmap

Planned work, roughly in priority order. Each item becomes its own branch and PR; when it merges, it leaves this file and gets a line in the changelog.

Finished work moves to [CHANGELOG.md](CHANGELOG.md).

## Leader console
- **Suggest edits during a live meeting:** an editor who isn't leading proposes changes to the live minutes; the leader accepts or rejects each one (REQUIREMENTS §3).
- **Weekly drawing and 50/50 tracking:** record each drawing in the Winners step (winner, amount, registered or not), and keep a running pot for the weekly drawing, so the minutes show where the pot sits and a light stats page shows who has won and how much. Later this feeds the Treasurer's records (below): the club's share of each 50/50, and anything donated back, as income, so Spending can show money in beside money out. Today the Winners section is free text and isn't read.

## Look and feel: ideas to consider
- **Auto icons from brand logos:** Auto could pick the brand logo for known sites (Discord, YouTube, GitHub, Facebook) instead of the generic chat or play icon.
- **Meeting page:** "Open in the leader console" smaller and below the title, so the minutes come first.

## Backups
Built (server admin guide → Backups): encrypted, scheduled and configured from the app, to S3 and/or Google Drive. Later, if wanted:
- **SFTP** to another server, as a third destination.
- **A restore drill:** a periodic test restore onto a scratch container, beyond the check that each backup opens.

## Admin tools
- **Release Dependabot security fixes by themselves** (decided 2026-11-09: security fixes only, patch or minor versions, every check green): Dependabot's security PRs auto-merge; a workflow then opens a "Release X.Y.Z" PR (patch bump, a changelog line naming the advisory) that auto-merges once its checks pass and is tagged; the nightly auto-update installs it. Needs a token as an Actions secret (a fine-grained token for this repo, contents and pull requests read/write, or a small GitHub App), because changes made with the built-in token don't start other workflows (the required checks, the Release workflow), and "Allow auto-merge" in the repo settings.
- **Moving the app to a new address** (e.g. members.w8lbz.org): change `auth.rp_id` in club.yaml and the proxy; club links to the app's pages already follow. Passkeys are tied to the address, so every member signs in once by emailed link and adds a new one (the offer after a link sign-in does this); old feed and sign-in links stop working. **A sub-path** (w8lbz.org/members) is a bigger job: every page links to `/…` from the site's root, so the app would need a path prefix throughout (FastAPI `root_path`, and the templates, scripts and redirects).
- **SSH bans on the Security page:** fail2ban's SSH bans listed with Unban, through a small root-owned helper (like the update unit), since the app itself can't run fail2ban-client.
- **Filter the Admin actions log** by admin, kind of action and date, with paging (it shows the last 50 today).
- **Act as a member:** View as is read-only today. A later option could let an admin make changes as the member to reproduce a problem, clearly logged and with emails still going to the admin, not the member.

## Members and roster management
The roster lives in the app (since 0.13.0); the Discord bot reads it from the roster API.
- **Offices:** open question: should the Secretary also change offices, not only admins?
- **Nicknames in search:** a small built-in table of common English nicknames (Richard = Rich = Rick = Dick, Robert = Bob = Rob, William = Bill = Will, James = Jim, …), used only for the first names of people on the roster, so searching "Richard" also finds "Rich" when a Richard is a member, and "bill" doesn't find every William unless one is. The roster's Other spellings stay for anything the table doesn't know.

- **Guest to member:** a fuller process (application, vote, initiation fee and dues) behind today's "Add a member".
- **Associate members** (GMRS or unlicensed), only if the bylaws add them; today a member must hold an amateur license.
- **Admin page for officers:** sync the roster now, email test mode on/off, delete test minutes, review imported minutes.

## Bylaws
- **Now:** a link in the app (Club Links) to the Google Doc version.
- **Later:** the bylaws as text under version control, with each amendment tied to the meeting and motion that adopted it, and a history page showing what changed and when. Open question: members-only or public.

## Treasurer
Built (officers guide → Treasurer): who owes what, payments, missed-chow charges, certificates, bills from the minutes, and each member's own figures on Home. Next:
- **Monthly report:** the bylaws (IV.C) want a report at the first meeting of each month; fill the Financial Report step from the recorded money, with account balances.
- **Hardship, deferral or reduced dues** (waiting on the Treasurer and the Board: the bylaws don't provide for it, so it's their policy to set first). Possible shapes: a reduced weekly rate for a member from a date, payments deferred until a date (no arrears email meanwhile), or forgiving a balance. Today an adjustment (a credit) covers a one-off.
- **Bond on the roster:** show whether each member has bought their bond (Certificate of Indebtedness) on the Roster page (bylaws VII.E).
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
- **Clubhouse DNS override:** the club router resolves the app's hostname to the server's LAN address, and the server serves HTTPS locally with the Tailscale-issued certificate. Then the same URL works at the clubhouse without an internet round trip, even when the internet is down.
- **Permanent hostname** (e.g. `minutes.w8lbz.org`) once the club controls its DNS. Members re-add passkeys once.
- **Anubis proof-of-work in front of public pages** (sign-in, public calendar), to turn away scrapers. Signed-in members never see it:
  - Keep Anubis's pass cookie at least as long as a session (`session_days`), so passing it once at sign-in covers the whole session.
  - Never challenge machine clients: the `.ics` feeds, `/api/roster` and the live-meeting polling.
  - Skip the challenge on the LAN with an Anubis rule on the club's network address ranges. This needs the real client address: a proxy in front must pass it on, and Anubis must not trust a forwarded address a client sends itself.
  - Don't let requests skip the challenge just because they carry a session cookie. Anubis can't check that the cookie is valid, so a bot could send a fake one.
- **Import review:** a page to mark imported historical minutes as reviewed, and fix their attributions.
