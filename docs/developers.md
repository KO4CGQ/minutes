# Developer guide

For working on the code: running it locally, previews, tests, releases and the security checks. Back to the [README](../README.md).

## Run
```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # once
uv sync
uv run w8lbz-minutes roster                          # sync people from the roster (Google Sheet, or roster-sheet.json once moved)
uv run w8lbz-minutes import archive                  # (re)build data/ from archive/
uv run w8lbz-minutes serve --host 0.0.0.0 --port 8765
uv run playwright install chromium                  # once: the mobile-first test uses it (skipped without it)
uv run pytest -q
```
The roster sync needs a Google service-account key at `private/google-service-account.json`. Share the sheet with the account's email as Editor (profile edits are written back to it).

PDF generation needs the system Pango library: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`.

## Previewing a branch
`deploy/preview.sh BRANCH` runs a branch on a development machine (ports from 8100) so it can be tried before merging, without touching the live server. It uses the local branch when there is one, so work can be previewed before it's pushed; otherwise the branch on GitHub. It prints the address and a one-time sign-in link (passkeys don't work on a bare IP address).
- **Preview mode:** a banner on every page; email is recorded on the **Email log** page (name menu → Email log, in admin mode) instead of being sent; roster edits go to a local copy of the sheet.
- Each preview has its own checkout and a fresh copy of `data/` (meetings, roster cache, bylaws) on every update; its sign-ins are kept.
- `deploy/preview.sh --list` shows running previews; `deploy/preview.sh --stop BRANCH` stops one and deletes its copy.
- They run as user services (`systemctl --user`), so they stop if this user logs out entirely, unless lingering is enabled (`loginctl enable-linger`).

## Changes and releases
- Each PR adds a line under "Unreleased" in `CHANGELOG.md`, and removes its item from `ROADMAP.md` if it finishes one.
- **Stacked PRs** (one branch built on another, while earlier ones wait for review): each PR targets the branch below it, so its diff shows only its own change. To release the stack, a release PR from the top branch into main carries everything. Just before merging it, retarget every PR in the stack to main; GitHub then marks each one merged when the release lands, because its commits are in main. A PR left targeting another branch stays open and has to be closed by hand. Keep stacks short where possible.
- To release: in a PR, rename "Unreleased" to the new version and date and bump `version` in `pyproject.toml` (then `uv lock`). When it merges, the Release workflow tags the merge commit `vX.Y.Z` and publishes a GitHub release with that CHANGELOG section. Deploy with `update.sh`; the footer then shows the new version.
- **Releases that need steps by hand** (a config change, a data file to copy, a new secret): add a line `- **Deploy notes:**` to that version's CHANGELOG section, with the steps indented under it as a numbered list. Put each command or file content in its own code block (indented 8 spaces under its step), one step per action, so they can be followed and copied. The admin Updates page shows them, formatted, and asks the admin to confirm they're done before updating. Only steps the release needs belong here; optional server hardening goes in this README.
- **Updating from the app:** admins in admin mode see "Version X is available" and an **Updates** page with **Update now**. It writes `update-request.json` in the data folder; the root-owned `w8lbz-minutes-update.path` unit sees it and runs `deploy/update-from-app.sh` (which runs `update.sh` and records the result). `update.sh` installs and enables that unit, so the first update after this feature arrives is still by hand. **Back up first** is ticked by default once backups are set up: a full backup (kept and sent off as usual) runs before the update is requested, and if it fails the update doesn't start.
- Every pull request runs the tests (the Tests workflow).

## Security checks
Run on every pull request, every push to main and **every Monday**, so a newly published advisory turns up even in a quiet week. Any of them can also be run by hand: Actions → the workflow → Run workflow.
- **Security workflow:** `pip-audit` checks the locked Python packages (`uv.lock`) against the PyPI and OSV advisory databases; `gitleaks` looks for keys, tokens and passwords anywhere in the git history.
- **CodeQL workflow:** GitHub's code scanner for the Python and JavaScript (injection, script injection (XSS), unsafe redirects...). Findings are under Security → Code scanning.
- **Dependabot** (`.github/dependabot.yml`): a weekly PR for newer Python packages and GitHub Actions, and one right away for a security fix. Review and merge like any PR.
- **Tests:** `tests/test_security.py` tries every route signed out (only the public pages answer), officer and admin pages as a member, and members changing what isn't theirs; a new route is covered automatically. The mobile test opens every page in a real browser and fails if the Content Security Policy blocks anything or a script errors. The policy runs scripts only from the app's own files or the `<script>` blocks a page was given a one-time nonce for: no inline `on…=` handlers (use `data-confirm`, `data-autosubmit`, `data-select`, `data-tick` from `base.html`, or a listener in a `<script nonce="{{ csp_nonce }}">`).
- Actions in the workflows are pinned to a commit, with the version in a comment; Dependabot updates both.
- **Repository settings** worth having on (Settings → Code security): Dependabot alerts, secret scanning and push protection (blocks a push that contains a key).

## Links page icons
Besides the 21 built-in icons, the icon picker searches about 3,400 brand logos from [Simple Icons](https://simpleicons.org) (CC0). They're stored in the repo (`src/w8lbz_minutes/simple_icons.json`), never loaded from a CDN, and drawn in the club's color like the built-in icons. To update them: `python3 deploy/update-simple-icons.py [VERSION]`.
