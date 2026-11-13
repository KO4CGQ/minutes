# Server admin guide

For whoever runs the server: install, configure, back up and secure the app. Back to the [README](../README.md).

## Getting started (a new club)
1. **Install** (below): `setup.sh` on a Debian container.
2. **club.yaml:** copy `club.example.yaml` to `/etc/club-minutes/club.yaml` and fill it in. At least the club's name and callsign, `meeting_day` and `meeting_time` (required), `auth: rp_id` (the HTTPS address members will use: passkeys only work there) and your own callsign under `admins:`. Then `systemctl restart club-minutes`.
3. **You, the first member:** `club-minutes first-member YOURCALL --first First --last Last --email you@example.org --class General` starts the roster with you and prints a sign-in link. (It runs once: with a roster in place it refuses, so it can't overwrite one.)
4. **Sign in** with that link, add a passkey when offered, then admin mode (name menu → Admin): on your member page tick yourself (and the other officers, once added) as an officer; add the other members on the Roster page; set the name, logo and colours on Site.
5. **Email** (sign-in links, the minutes): the SMTP settings in `/etc/club-minutes/env` (Login, below). Until then, links are written to the server's log (`journalctl -u club-minutes`), and the pages say so.
6. **Backups** (Admin → Backups) before anything else is entered for real.

As root on the container:
```sh
curl -fsSL https://raw.githubusercontent.com/KO4CGQ/minutes/main/deploy/setup.sh | bash   # first time
/opt/club-minutes/deploy/update.sh                                                     # by hand: the latest main (TAG=v0.15.0 for a release)
```
To set the server's time zone too, run setup as `… | TZ_NAME=America/Chicago bash` (otherwise it keeps the server's own; the app uses that unless club.yaml names one).
Then put `club.yaml` in `/etc/club-minutes/` (and `google-service-account.json`, only for backups to Google Drive), and data in `/var/lib/club-minutes/`.
The app runs as the `minutes` user on port 8000. The roster cache (`roster.json`) is rebuilt daily at 04:00 from the roster the app keeps (`roster-sheet.json`); backups run on the schedule set on the Backups page.

## Staging server (optional)
A second container that runs the app on a copy of the live data, to try a change before it's released. It pulls the
live data **one way** (nightly at 03:00, and **Sync now**); nothing on it can change the live server. Every page has a
STAGING banner, and email goes only to the person whose click caused it (a sign-in link: to whoever asked for it), with
"[STAGE]" in the subject; email with nobody clicking (reminders, timers) is held, and all of it is on the Email log page.
Roles are the same as live; passkeys are per address, so each person adds one on stage too (or signs in by emailed link).

1. Set up the container as above (`setup.sh`, `club.yaml` with stage's own address under the sign-in settings, the
   SMTP settings in `/etc/club-minutes/env`), and `apt install rsync` on **both** servers.
2. On stage, as root: `ssh-keygen -t ed25519 -N "" -f /etc/club-minutes/stage-sync-key`.
3. On the **live** server, add its `.pub` line to `/root/.ssh/authorized_keys`, limited to reading the data folder:
   `command="rrsync -ro /var/lib/club-minutes/",restrict ssh-ed25519 AAAA... stage-sync`
4. On stage, add to `/etc/club-minutes/env`: `CLUB_MINUTES_STAGE=1` and `STAGE_SYNC_FROM=root@<live server>:./`. Leave
   `CLUB_MINUTES_BACKUP_PASSPHRASE` out (stage makes no backups).
5. On stage: `install -m 644 /opt/club-minutes/deploy/club-stage-sync.* /etc/systemd/system/ && systemctl daemon-reload &&
   systemctl enable --now club-stage-sync.timer club-stage-sync.path && systemctl start club-stage-sync && systemctl restart club-minutes`
   (these units aren't installed by `update.sh`, so the live server never gets them).

Admin mode → name menu → **Stage sync**: the last sync, **Sync now**, and **Pause nightly sync** (to keep test changes
overnight). A sync replaces everything except stage's own sign-ins and passkeys, email log, backups and update settings.
Stage doesn't update itself; a developer installs a branch with `deploy/stage-push.sh` (developer guide).

## Config
`club.yaml` (local only, git-ignored; copy from `club.example.yaml`) holds the sign-in settings, the optional modules (`modules: {chow: true}`; each is off unless turned on), the holidays and election days by region (`calendar: {holidays: us, elections: us-oh}`; none by default), the bylaws sections the app cites (`bylaws: refs:`, each shown only if given), a certificate members buy (`treasurer: certificate:`, off unless named), the meeting's sections in order (`meeting: sections:`, the built-ins and the club's own), and under `club:` the meeting's day and time (`meeting_day`, `meeting_time`: required; the app won't start without them), how dates are written (`date_formats`: US order unless set; club.example.yaml shows day-first; admins can also choose on Admin → Site → Dates), the admins list, email test mode, the starting club links and the private never-record list. People, their titles and who may lead come from the roster the app keeps (`roster-sheet.json` in the data folder, edited on the Roster pages; moved off the club's Google Sheet in 0.13.0); the list of offices is `offices:` in club.yaml.

Some of these settings can also be changed by admins on **Admin → Site** (in admin mode). What's saved there is kept in `site-settings.json` in the data folder, under the same names, and wins over club.yaml until an admin picks **Back to club.yaml**; it's checked like club.yaml (a wrong value is refused, saying why) and used at once, without a restart. Sign-in (`auth:`), `admins:`, email test mode and AI can only be set in club.yaml. On the page now: **Club** (`name`, `callsign`, `meeting_day`, `meeting_time`, `timezone`), **Agenda** (`meeting: sections:`, `invite_after_visits`, `remind_days_before`) and **Dates** (`date_formats`). The other processes (reminders, backups, the roster sync) read the same file, so they follow the page too.

Club data (`archive/`, `data/`, `club.yaml`, credentials) is never committed.

## Login
Members sign in with a passkey, or with a one-time link emailed to their roster address.
- **Passkeys** only work at the HTTPS address in `club.yaml` (`auth.rp_id`).
- **Email** needs `CLUB_MINUTES_SMTP_USER` / `CLUB_MINUTES_SMTP_PASSWORD` (a Gmail app password) in `/etc/club-minutes/env`.
  Without them, links are written to the server log instead.
- **AI** (optional) needs `CLUB_MINUTES_OPENROUTER_KEY` in `/etc/club-minutes/env` and `ai: {model: ...}` in club.yaml. Each call is logged to `ai-log.jsonl` in the data folder (time, model, size, cost; never the text).
- **Recovery:** an admin on the server can print a one-time link for a member: `club-minutes login-link KD8XYZ`

In admin mode, the name menu's **Admin** (`/admin`) lists the admin tools, each with a line of how things stand: Security (failed sign-ins today, bans), Backups (the last one), Updates (what's running, what's available), Site, the Email log (anything held back this week) and, on a staging server, Stage sync.

Admin mode → Admin → **Site**: on **Club**, the app's name (default "<callsign> Minutes", or `app_name:` in club.yaml); on **Look**, three pictures, each with its own **Upload…** and **Reset**: the logo (pages, minutes, PDFs), the browser tab icon and the phone home-screen icon. The icons are made from the logo until one is uploaded of its own. **Behind it** sets what's under the logo on pages, so a logo of dark lines on a see-through background still shows in dark mode; the Logo card shows it on a light and a dark page. **Colours:** one main colour sets the buttons, links, top bar and the minutes' section headings (a top bar of its own is optional; the logo's colours are offered as one-click picks). The app makes the light and dark palettes from it, checks every text colour for readability and adjusts a shade if needed, and previews both before Save; **Back to the default** returns to the app's blue. Uploads are redrawn as fresh PNGs in `branding/` in the data folder; the name, the colours (and which icons are the club's own) are in `branding.json`. Both are in the backups.

Admin mode → Security → **Members** lists every member on the roster: their last sign-in, how many passkeys they've set up (and when one was last used), and how many devices are signed in now, so you can see who hasn't signed in or still uses emailed links. 

Officers and members ticked `minutes_editor` on the roster can lead a meeting (the leader console). Admins are a separate list in `club.yaml`.

## Emailing the minutes
When a leader submits the minutes, they go to every roster member with an email address (except Inactive and Silent Key), each getting their own copy: the PDF attached, or a link, as each member chose on the Email tab of their profile (or not at all, if they turned it off). Replies go to the meeting's leader.
- **Test mode:** while `club.yaml` has `email.only_to`, only those callsigns or addresses get mail; everyone else is withheld and logged. The submit dialog says which mode is on.
- Each meeting is emailed once. The outcome is in `emailed.json` beside the minutes, and leaders see it on the minutes page.
- **Removing a test meeting:** `club-minutes delete-meeting 2026-12-31`, then `systemctl restart club-minutes`.

## Admin mode
Admins get **Turn on admin mode** in the name menu: callsigns listed under `admins:` in `club.yaml`, and members made admins in the app (admin mode → Admin → Security → **Admins**, kept in `admins.json` in the data directory). Making someone an admin asks for a passkey or emailed link unless you confirmed in the last 5 minutes; each change is in Admin actions and emailed to the other admins, and the last admin can't be removed. Admins in `club.yaml` can only be removed there, so whoever has the server can always get back in. For an hour (or until turned off) that sign-in can see and do what officers can, with a red banner showing. Each switch is logged in `admin-log.jsonl` in the data directory.

In admin mode, a member's page has **View the app as …**: the app as that member sees it, read-only (no changes, and not their sign-in or passkey page), until **Stop** or admin mode ends. Also logged. Trying to save anything while viewing as someone opens a small "Read-only" dialog instead.

## Backups
The app backs up its whole data folder (minutes, chow, business, calendars, settings, logs, the roster cache, and a consistent copy of the sign-in database) into one compressed `.tar.gz`, encrypts it (scrypt + AES-256-GCM) with a passphrase only the admins hold, and decrypts it again to check it opens. It keeps the newest on the server (`backups/` in the data folder) and sends each to **S3-compatible storage** (Backblaze B2), a **Google Drive** folder, or both. If anything fails, the admins get an email.

Admin mode → Admin → **Backups** has everything else: the size of the data and of the last backup, Back up now, **Download a backup now** (a fresh one, compressed and encrypted, to your computer), the server's copies, the **schedule** (daily, weekly, or only by hand, at a chosen hour; the `club-minutes-backup.timer` checks hourly), **how many to keep** on the server and on Drive, and the S3 and Drive settings, with the recommended values: daily, 7 on the server, 30 on Drive, 90 days on S3. The settings are in `backup-settings.json`, which holds the S3 secret and is left out of the backups (re-enter it after a restore).

**Setup:**
1. A long passphrase in `/etc/club-minutes/env`: `CLUB_MINUTES_BACKUP_PASSPHRASE=...`, then restart. Keep a copy somewhere other than the server (a password manager the admins share, and a printed copy somewhere safe): without it no backup can be opened. It's not set from the app on purpose: a stolen admin sign-in could otherwise set one it knows. The Backups page **suggests** one (24 capitals and digits without look-alikes, in groups of four: easy to type from paper, about 119 bits) and **checks** one: whether it matches the server's, its strength, and whether it's in Have I Been Pwned's leaked passwords (free Pwned Passwords range API: only the first 5 characters of the SHA-1 are sent). Neither stores nor logs the passphrase.
2. **S3 / Backblaze B2:** a private bucket with a lifecycle rule for the folder (B2: Lifecycle Settings → custom rule for `minutes/`: hide files 90 days after upload, delete them 1 day after hiding), and an application key limited to that bucket with read and write. Enter the endpoint, region, bucket, key ID and secret on the Backups page (`CLUB_MINUTES_S3_KEY_ID` / `CLUB_MINUTES_S3_SECRET` in the env file override them). The app never deletes anything there.
3. **Google Drive:** a folder in a **Shared Drive** (Google Workspace). Google gives service accounts no storage of their own, so a folder in someone's My Drive fails with a storage-quota error. Add the service account (its email is in `google-service-account.json`) to the Shared Drive as **Content manager**, so it can delete backups beyond the number to keep, and paste the folder's link on the Backups page.
4. Back up now, and check the page says where it was sent.

Until the passphrase is set, scheduled backups don't run (and send no "failed" emails); the Backups page shows "Passphrase: not set". With only the passphrase (no S3 or Drive), backups are kept on the server and can be downloaded: enough to undo a mistake, not to survive losing the server.

**Restore** (onto this server or a fresh one set up with `setup.sh`):
```sh
sudo -u minutes /opt/club-minutes/.venv/bin/club-minutes restore BACKUP.tar.gz.enc /tmp/restored   # asks for the passphrase
systemctl stop club-minutes
rsync -a /tmp/restored/ /var/lib/club-minutes/ && chown -R minutes:minutes /var/lib/club-minutes
systemctl start club-minutes
```
`restore` only unpacks into an empty folder, never over live data. Members stay signed in (the sign-in database is in the backup); passkeys keep working as long as the hostname is the same.

## Upload limit
Admin mode → Security → **Server** → **Largest upload** (1–100 MB, 10 by default; kept in `limits.json` in the data folder). It covers profile pictures and calendar .ics imports; a bigger request is turned away (413) before the app reads it. A proxy in front of the app (Caddy, nginx, Traefik) has its own limit, which must be at least as big.

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

Also worth doing: SSH with keys only (`PasswordAuthentication no` in `/etc/ssh/sshd_config`), automatic security updates with restarts afterwards (`apt install unattended-upgrades needrestart`, then `dpkg-reconfigure -plow unattended-upgrades`, and `$nrconf{restart} = 'a';` plus, in a container, `$nrconf{kernelhints} = 0;` in `/etc/needrestart/conf.d/50-auto.conf`: Debian's needrestart otherwise only lists the services still running an updated library), and a firewall that allows only SSH and the app's port.

## Bylaws
`/bylaws` shows the club's constitution and bylaws from `bylaws.md` in the data directory (`/var/lib/club-minutes/bylaws.md` on the server). It's kept out of this public repo.
