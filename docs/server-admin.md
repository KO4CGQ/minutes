# Server admin guide

For whoever runs the server: install, configure, back up and secure the app. Back to the [README](../README.md).

## Deploy (Debian container)
As root on the container:
```sh
curl -fsSL https://raw.githubusercontent.com/KO4CGQ/minutes/main/deploy/setup.sh | bash   # first time
/opt/w8lbz-minutes/deploy/update.sh                                                    # by hand: the latest main (TAG=v0.15.0 for a release)
```
Then put `club.yaml` in `/etc/w8lbz-minutes/` (and `google-service-account.json`, only for backups to Google Drive), and data in `/var/lib/w8lbz-minutes/`.
The app runs as the `minutes` user on port 8000. The roster cache (`roster.json`) is rebuilt daily at 04:00 from the roster the app keeps (`roster-sheet.json`); backups run on the schedule set on the Backups page.

## Config
`club.yaml` (local only, git-ignored; copy from `club.example.yaml`) holds the sign-in settings, the admins list, email test mode, the starting club links and the private never-record list. People, their titles and who may lead come from the roster the app keeps (`roster-sheet.json` in the data folder, edited on the Roster pages; moved off the club's Google Sheet in 0.13.0); the list of offices is `offices:` in club.yaml.

Club data (`archive/`, `data/`, `club.yaml`, credentials) is never committed.

## Login
Members sign in with a passkey, or with a one-time link emailed to their roster address.
- **Passkeys** only work at the HTTPS address in `club.yaml` (`auth.rp_id`).
- **Email** needs `W8LBZ_SMTP_USER` / `W8LBZ_SMTP_PASSWORD` (a Gmail app password) in `/etc/w8lbz-minutes/env`.
  Without them, links are written to the server log instead.
- **AI** (optional) needs `W8LBZ_OPENROUTER_KEY` in `/etc/w8lbz-minutes/env` and `ai: {model: ...}` in club.yaml. Each call is logged to `ai-log.jsonl` in the data folder (time, model, size, cost; never the text).
- **Recovery:** an admin on the server can print a one-time link for a member: `w8lbz-minutes login-link KD8XYZ`

Officers and members ticked `minutes_editor` on the roster can lead a meeting (the leader console). Admins are a separate list in `club.yaml`.

## Emailing the minutes
When a leader submits the minutes, they go to every roster member with an email address (except Inactive and Silent Key), each getting their own copy: the PDF attached, or a link, as each member chose on the Email tab of their profile (or not at all, if they turned it off). Replies go to the meeting's leader.
- **Test mode:** while `club.yaml` has `email.only_to`, only those callsigns or addresses get mail; everyone else is withheld and logged. The submit dialog says which mode is on.
- Each meeting is emailed once. The outcome is in `emailed.json` beside the minutes, and leaders see it on the minutes page.
- **Removing a test meeting:** `w8lbz-minutes delete-meeting 2026-12-31`, then `systemctl restart w8lbz-minutes`.

## Admin mode
Callsigns listed under `admins:` in `club.yaml` get **Turn on admin mode** in the name menu. For an hour (or until turned off) that sign-in can see and do what officers can, with a red banner showing. Each switch is logged in `admin-log.jsonl` in the data directory.

In admin mode, a member's page has **View the app as …**: the app as that member sees it, read-only (no changes, and not their sign-in or passkey page), until **Stop** or admin mode ends. Also logged. Trying to save anything while viewing as someone opens a small "Read-only" dialog instead.

## Backups
The app backs up its whole data folder (minutes, chow, business, calendars, settings, logs, the roster cache, and a consistent copy of the sign-in database) into one compressed `.tar.gz`, encrypts it (scrypt + AES-256-GCM) with a passphrase only the admins hold, and decrypts it again to check it opens. It keeps the newest on the server (`backups/` in the data folder) and sends each to **S3-compatible storage** (Backblaze B2), a **Google Drive** folder, or both. If anything fails, the admins get an email.

Admin mode → Security → **Backups** has everything else: the size of the data and of the last backup, Back up now, **Download a backup now** (a fresh one, compressed and encrypted, to your computer), the server's copies, the **schedule** (daily, weekly, or only by hand, at a chosen hour; the `w8lbz-minutes-backup.timer` checks hourly), **how many to keep** on the server and on Drive, and the S3 and Drive settings, with the recommended values: daily, 7 on the server, 30 on Drive, 90 days on S3. The settings are in `backup-settings.json`, which holds the S3 secret and is left out of the backups (re-enter it after a restore).

**Setup:**
1. A long passphrase in `/etc/w8lbz-minutes/env`: `W8LBZ_BACKUP_PASSPHRASE=...`, then restart. Keep a copy somewhere other than the server (a password manager the admins share, and a printed copy somewhere safe): without it no backup can be opened. It's not set from the app on purpose: a stolen admin sign-in could otherwise set one it knows. The Backups page **suggests** one (24 capitals and digits without look-alikes, in groups of four: easy to type from paper, about 119 bits) and **checks** one: whether it matches the server's, its strength, and whether it's in Have I Been Pwned's leaked passwords (free Pwned Passwords range API: only the first 5 characters of the SHA-1 are sent). Neither stores nor logs the passphrase.
2. **S3 / Backblaze B2:** a private bucket with a lifecycle rule for the folder (B2: Lifecycle Settings → custom rule for `minutes/`: hide files 90 days after upload, delete them 1 day after hiding), and an application key limited to that bucket with read and write. Enter the endpoint, region, bucket, key ID and secret on the Backups page (`W8LBZ_S3_KEY_ID` / `W8LBZ_S3_SECRET` in the env file override them). The app never deletes anything there.
3. **Google Drive:** a folder in a **Shared Drive** (Google Workspace). Google gives service accounts no storage of their own, so a folder in someone's My Drive fails with a storage-quota error. Add the service account (its email is in `google-service-account.json`) to the Shared Drive as **Content manager**, so it can delete backups beyond the number to keep, and paste the folder's link on the Backups page.
4. Back up now, and check the page says where it was sent.

Until the passphrase is set, scheduled backups don't run (and send no "failed" emails); the Backups page shows "Passphrase: not set". With only the passphrase (no S3 or Drive), backups are kept on the server and can be downloaded: enough to undo a mistake, not to survive losing the server.

**Restore** (onto this server or a fresh one set up with `setup.sh`):
```sh
sudo -u minutes /opt/w8lbz-minutes/.venv/bin/w8lbz-minutes restore BACKUP.tar.gz.enc /tmp/restored   # asks for the passphrase
systemctl stop w8lbz-minutes
rsync -a /tmp/restored/ /var/lib/w8lbz-minutes/ && chown -R minutes:minutes /var/lib/w8lbz-minutes
systemctl start w8lbz-minutes
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
`/bylaws` shows the club's constitution and bylaws from `bylaws.md` in the data directory (`/var/lib/w8lbz-minutes/bylaws.md` on the server). It's kept out of this public repo.
