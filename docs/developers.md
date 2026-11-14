# Developer guide

For working on the code: running it locally, previews, tests, releases and the security checks. Back to the [README](../README.md).

## Run
```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # once
uv sync
uv run club-minutes roster                          # rebuild data/roster.json from data/roster-sheet.json
uv run club-minutes import archive                  # optional: (re)build data/ from old minutes in archive/ (importer.py)
uv run club-minutes serve --host 0.0.0.0 --port 8765
uv run playwright install chromium firefox webkit   # once: the browser tests run in each (one not installed is skipped)
sudo .venv/bin/playwright install-deps              # once, on Linux: the libraries Firefox and WebKit need
uv run pytest -q                                    # the tests, the browser tests in all three, and ruff (pinned in uv.lock)
uvx ruff@latest check .                             # warn only: what the newest ruff would flag
```
People come from `data/roster-sheet.json` (the roster's two tabs, Member Roster and Membership Log, as grids of values). Copy it from a backup of the club's server, or start one with `uv run club-minutes first-member YOURCALL --first … --last … --email … --class General` (only when there's none).

PDF generation needs the system Pango library: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`.

## Previewing a branch
`deploy/preview.sh BRANCH` runs a branch on a development machine (ports from 8100) so it can be tried before merging, without touching the live server. It uses the local branch when there is one, so work can be previewed before it's pushed; otherwise the branch on GitHub. It prints the address and a one-time sign-in link (passkeys don't work on a bare IP address).
- **Preview mode:** a banner on every page; email is recorded on the **Email log** page (name menu → Email log, in admin mode) instead of being sent; roster edits go to a copy of the roster (`preview-sheet.json`), never the real one.
- Each preview has its own checkout and a fresh copy of `data/` (meetings, roster cache, bylaws) on every update; its sign-ins are kept.
- **Staging server** (server admin guide → Staging server): `deploy/stage-push.sh [BRANCH] [SSH-HOST]` installs a local branch there over ssh (default: the current branch on `minutes-stage`), without pushing it to GitHub; `update.sh` installs it with the usual rollback. Its data is the live app's, synced nightly.
- **The demo club:** `DEMO=1 deploy/preview.sh BRANCH demo` runs the preview with the demo club instead of the club's data: Example Radio Club (W0DEMO), twelve invented members (Jane Doe, N0JANE, is the president and admin, and the sign-in link is hers), three published minutes, business items, a chow schedule and calendar events, all dated around today. `demo/build.py OUTDIR` builds it through the app's own code (`demo/club.yaml` is its config), so it follows changes to the data files; `tests/test_demo.py` builds it and opens the main pages. Use it for screenshots and the video: every name, callsign (four-letter suffixes are never issued), email (example.org) and phone (555-01xx) is made up. `uv run python demo/screenshots.py` takes the README's screenshots from it (no server; the PREVIEW banner left out), and records in `docs/screenshots/manifest.json` which templates, CSS and scripts each one shows: `tests/test_guards.py` fails when one of those changes, naming the pictures to retake. Python changes aren't tracked: retake by eye when a page shows something new.
- `deploy/preview.sh --list` shows running previews; `deploy/preview.sh --stop BRANCH` stops one and deletes its copy.
- They run as user services (`systemctl --user`), so they stop if this user logs out entirely, unless lingering is enabled (`loginctl enable-linger`).

## Changes and releases
- Each PR adds a line under "Unreleased" in `CHANGELOG.md`, and says which issue it settles, if any (`Closes #12`).
- **Stacked PRs** (one branch built on another, while earlier ones wait for review): each PR targets the branch below it, so its diff shows only its own change. To release the stack, a release PR from the top branch into main carries everything. Just before merging it, retarget every PR in the stack to main; GitHub then marks each one merged when the release lands, because its commits are in main. A PR left targeting another branch stays open and has to be closed by hand. Keep stacks short where possible.
- To release: in a PR, rename "Unreleased" to the new version and date and bump `version` in `pyproject.toml` (then `uv lock`). When it merges, the Release workflow tags the merge commit `vX.Y.Z` and publishes a GitHub release with that CHANGELOG section. Deploy with `update.sh`; the footer then shows the new version.
- **Releases that need steps by hand** (a config change, a data file to copy, a new secret): add a line `- **Deploy notes:**` to that version's CHANGELOG section, with the steps indented under it as a numbered list. Put each command or file content in its own code block (indented 8 spaces under its step), one step per action, so they can be followed and copied. The admin Updates page shows them, formatted, and asks the admin to confirm they're done before updating. Only steps the release needs belong here; optional server hardening goes in this README.
- **Updating from the app:** admins in admin mode see "Version X is available" and an **Updates** page with **Update now**. It writes `update-request.json` in the data folder; the root-owned `club-minutes-update.path` unit sees it and runs `deploy/update-from-app.sh` (which runs `update.sh` and records the result). `update.sh` installs and enables that unit, so the first update after this feature arrives is still by hand. **Back up first** is ticked by default once backups are set up: a full backup (kept and sent off as usual) runs before the update is requested, and if it fails the update doesn't start.
- **Installs the release, and rolls back:** "Update now" and the nightly auto-update ask for a release by its version; `update-from-app.sh` accepts only a plain `X.Y.Z` from the request file (the app writes it) and runs `TAG=vX.Y.Z update.sh`, which installs that tag, not the latest main. If the sign-in page doesn't answer within 5 minutes after the restart (startup takes about a second), `update.sh` resets to the commit it had, reinstalls and restarts, and exits non-zero (the Updates page shows it failed). Run by hand without `TAG`, `update.sh` still installs the latest main.
- **Automatic updates** (Updates page → Turn on; off by default, needs backups set up): `club-minutes-autoupdate.timer` runs `club-minutes auto-update` hourly at :30, and it acts only at the hour set on the Updates page (2:30 by default). If no release newer than the running one has deploy notes (all of them are read, not only the newest), it takes a full backup (none, no update), asks for that release as above, waits for the outcome and emails the admins. A release with deploy notes is only emailed about (once); one that failed isn't tried again until an admin acts.
- **Changing how data is stored** (a file renamed, a field reshaped): add a step to `STEPS` in `src/club_minutes/upgrade.py` with a test that runs it on data of the old shape, so a 1.x needs no deploy notes. The app runs any steps the data folder hasn't had (`data-version` says which it has) at start, after a copy to `before-upgrade/`; if the new version doesn't start, `update.sh` puts that copy back (`club-minutes data-undo`) before going back. Never change or remove a released step, and make each safe to run twice (it can be stopped part way). An older version refuses data newer than it knows.
- Every pull request runs the tests (the Tests workflow).

## Compatibility (what a 1.x keeps)
From 1.0.0 on, a club updates within 1.x without doing anything by hand, so a 1.x may add but never take away or rename. Breaking any of this waits for 2.0 (the first number in the version: see CHANGELOG.md).

What a club relies on:
- **club.yaml:** every setting's name and meaning, and the values it takes.
- **The data folder:** what's stored in it, which a newer version may reshape only through an upgrade step (Changes and releases, above); and the backup format, so an older backup can always be restored.
- **The server:** the folders (`/opt/club-minutes`, `/var/lib/club-minutes`, `/etc/club-minutes`), the service and timer names, the `club-minutes` commands and their options, the `CLUB_MINUTES_*` environment settings, and `setup.sh` and `update.sh`.
- **Addresses others keep:** members' calendar subscription links (`/calendar/feed/…`, `/calendar/public.ics`) and public calendar page, sign-in links in emails already sent, and any page address in an email or a bookmark (a moved page answers its old address with a redirect, as Backups' does).
- **The roster API** (`/api/roster`, used by the Discord bot): its token, its fields and what they mean. Fields may be added; none removed or renamed.

The rules:
- **A new setting has a default** that keeps things as they were, so a club.yaml from 1.0.0 still starts. A setting that must be given is a 2.0 change.
- **An old name keeps working until 2.0.** To rename a club.yaml setting, add it to `RENAMED` in `src/club_minutes/club.py`: the app reads the old name as the new one and says so on the Admin page. A setting saved on Admin → Site (site-settings.json) is renamed with an upgrade step instead, since the app wrote it. A renamed command, service or address keeps its old name alongside (an alias, a second unit, a redirect).
- **A name is never reused** for something different, even after the old thing is gone: pick a new one.
- **Removing a feature is a 2.0 change.** Switching a module off (like chow) is the club's choice, and switching it back on finds its data where it was.
- When in doubt, a release needs Deploy notes only for something a club chooses to do, never for something it has to.
- **Repo checks** (`tests/test_guards.py`, in every PR's tests): the screenshots are up to date (above) and each is used; links and pictures in the README and guides lead to files that exist, and every app address they name in backticks (`/calendar/public.ics`) is a page; and no tracked file has a real-looking callsign (only four-letter suffixes, KO4CGQ, and W8LBZ as the old name), email (only example.org/.com/.net and other reserved names) or phone number (only 555-0100 to 555-0199).
- **The guards:** `tests/test_compat.py` (in every PR's tests) fails if a 1.0.0 club.yaml with every setting no longer starts (`tests/fixtures/club-1.0.0.yaml`: never edit it), a setting the code reads isn't in `club.example.yaml`, a 1.0.0 setting is no longer read or renamed, or a command, address, roster API field, systemd unit or `CLUB_MINUTES_*` setting from 1.0.0 is gone. Don't edit its lists to make it pass: keep the old name working. Adding is always fine.

## Security checks
Run on every pull request, every push to main and **every Monday**, so a newly published advisory turns up even in a quiet week. Any of them can also be run by hand: Actions → the workflow → Run workflow.
- **Security workflow:** `pip-audit` checks the locked Python packages (`uv.lock`) against the PyPI and OSV advisory databases; `gitleaks` looks for keys, tokens and passwords anywhere in the git history.
- **CodeQL workflow:** GitHub's code scanner for the Python and JavaScript (injection, script injection (XSS), unsafe redirects...). Findings are under Security → Code scanning.
- **Dependabot** (`.github/dependabot.yml`): a weekly PR for newer Python packages and GitHub Actions, and one right away for a security fix. Review and merge like any PR.
- **Lint:** with the tests (`tests/test_lint.py`), here and on GitHub, each at the version pinned in `uv.lock`: ruff on the Python (its default rules), shellcheck on `deploy/*.sh` (several run as root), zizmor on the GitHub workflows. GitHub's Tests also run the newest ruff as a warning (the ruff-latest job: red there doesn't fail the check), and Dependabot opens a "lint" pull request of its own when any of the three has a new version: fix what it flags in that PR, then merge it to move the pins. gitleaks (Security workflow) is a pinned download Dependabot can't update: its gitleaks-latest job warns when a newer one is out (set `GITLEAKS_VERSION` and `GITLEAKS_SHA256` in security.yml).
- **Tests:** `tests/test_security.py` tries every route signed out (only the public pages answer), officer and admin pages as a member, and members changing what isn't theirs; a new route is covered automatically. The mobile test opens every page in real browsers (Chromium, Firefox and WebKit, Safari's engine), again in dark mode (failing on text under 3:1 contrast with its background), and fails if the Content Security Policy blocks anything or a script errors. The policy runs scripts only from the app's own files or the `<script>` blocks a page was given a one-time nonce for: no inline `on…=` handlers (use `data-confirm`, `data-autosubmit`, `data-select`, `data-tick` from `base.html`, or a listener in a `<script nonce="{{ csp_nonce }}">`).
- **Changes only from the app's own pages:** a POST (or PUT, PATCH, DELETE) that a browser marks as from another site or a sibling host (`Sec-Fetch-Site`), or whose `Origin` isn't the address it was sent to, gets a 403 (`same_origin_only` in `web.py`). The sign-in cookie's `SameSite=lax` stops other sites but not sibling hosts on the same domain (other names on the tailnet). New forms and `fetch` calls on the app's own pages need nothing.
- Actions in the workflows are pinned to a commit, with the version in a comment; Dependabot updates both.
- **Repository settings** worth having on (Settings → Code security): Dependabot alerts, secret scanning and push protection (blocks a push that contains a key).

## Checks before a push
`dev/checks.sh` runs here what GitHub runs on a pull request, so nothing turns up only there: the tests (with ruff, shellcheck, zizmor and the browser tests), pip-audit on the locked packages, gitleaks at the version `security.yml` pins on the branch's history, and CodeQL with the `security-extended` queries on the committed files (Python and JavaScript). About four minutes; `--quick` skips the tests when they've just passed. Tools live in `~/.cache/club-minutes-checks`: gitleaks downloads itself (checked against the pinned checksum); for CodeQL, unpack `codeql-bundle-linux64` from github/codeql-action's releases there (checking its `.checksum.txt`), or set `CODEQL`.

CodeQL findings reviewed as false positives are listed in `dev/codeql-accepted.txt` (rule, file, the line's text, why), matched by the line's text so moving code doesn't bring them back. A new finding fails: fix it, or, after a careful look, add it there with the reason and dismiss it on GitHub with the same words. To run the checks on every push, add a `.git/hooks/pre-push` that runs `dev/checks.sh`.

## Roles and permissions
What a member may do is a **permission** (`PERMISSIONS` in `src/club_minutes/club.py`: lead, calendar, chow, contacts, oversee, treasurer, money, members), and **roles** (`ROLES`) say which permissions each gives. A new check asks `archive.club.may(member, "permission")`, plus `admin_mode(request)` (admins in admin mode may do everything); never `member.officer` or a title directly. Officer, Board, Treasurer and Roster keeper follow the offices a member holds; Minutes editor and Calendar editor (`PICKED`) are given on Admin → Site → Roles and kept in `data/roles.json` (the roster's old ticks are read only until that file exists). Admin isn't a role: it's `admins:` and Admin → Security, with admin mode and its re-confirm. A new permission goes in both tables and on the Site → Roles page by itself.

## Election days for another state
Election days are worked out from a state's rules in a small file in `src/club_minutes/regions/`, named for its code: `us_oh.py` is `us-oh`. A file there is listed on Admin → Site → Features by itself; nothing else needs changing. It has:
- `NAME`: how the list shows it, e.g. `"Ohio: primaries and general election"`.
- `elections(lo, hi)`: every election day from `lo` to `hi` (dates) as `[(date, name, notes)]`; `notes` is shown with the event (polling hours, where to find your ballot).
- A docstring citing the law the rules come from, and a `shortcut:` line with the year they were checked.

Copy `us_oh.py` and change its rules; `nth_weekday(year, month, weekday, n)` (Monday 0, n -1 for the last) is there for "the first Tuesday after the first Monday" kinds of rules. Add a test like `test_holidays_and_elections_are_none_unless_club_yaml_picks_a_region`. Holidays need no file: they come from python-holidays, and a club adds its own on the Features tab.

## Links page icons
Besides the 21 built-in icons, the icon picker searches about 3,400 brand logos from [Simple Icons](https://simpleicons.org) (CC0). They're stored in the repo (`src/club_minutes/simple_icons.json`), never loaded from a CDN, and drawn in the club's color like the built-in icons. To update them: `python3 deploy/update-simple-icons.py [VERSION]`.

## Callsign changes
The callsign is the member's id across the data folder, so **New callsign** renames it everywhere (`callsign.py`): current state (`chow.json`, `business.json`, `events.json`, `calendar-feeds.json`, `ask-about.json`, `email-prefs.json`, `settings.json`, `treasurer.json`, `admins.json`, drafts' `console.json`), the minutes' front matter and report tags (never their text), the sign-in database and the picture. Logs (`*.jsonl`) keep the old id as history. It needs the backup passphrase (a backup is taken first and kept as `…-before-NEWCALL.tar.gz.enc`); the old call goes into the member's `aliases` on the roster, so `Club.canonical` maps it to the member (club.yaml's `admins:` and `email.only_to` included).
