#!/usr/bin/env bash
# One-time setup of the minutes app on a Debian container. Run as root. Safe to re-run.
#   Layout:  /opt/club-minutes        app code (git clone of main), owned by the 'minutes' user
#            /etc/club-minutes/       club.yaml, google-service-account.json, env (secrets; not in git)
#            /var/lib/club-minutes/   data: meetings archive, roster cache
set -euo pipefail
REPO=${REPO:-https://github.com/KO4CGQ/minutes.git}
APP=/opt/club-minutes CONF=/etc/club-minutes DATA=/var/lib/club-minutes
# The club's time zone: "today" in the console and the calendars' times depend on it. TZ_NAME=America/Chicago sets
# the server's; without it the server keeps its own (club.yaml club: timezone: can also name one for the app).
if [ -n "${TZ_NAME:-}" ]; then
  timedatectl set-timezone "$TZ_NAME" 2>/dev/null || ln -sf "/usr/share/zoneinfo/$TZ_NAME" /etc/localtime
fi

apt-get update -qq
# git/curl: deploy; sudo: update.sh runs git and uv as the app's user; pango + fonts: PDF rendering (WeasyPrint)
apt-get install -y -qq git curl sudo ca-certificates libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core fonts-liberation >/dev/null

id minutes >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/club-minutes-home --shell /usr/sbin/nologin minutes
install -d -o minutes -g minutes -m 750 "$DATA"
install -d -o root -g minutes -m 750 "$CONF"
[ -f "$CONF/env" ] || install -o root -g minutes -m 640 /dev/null "$CONF/env"

# uv (Python package manager), installed system-wide for the service user
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi

if [ ! -d "$APP/.git" ]; then
  install -d -o minutes -g minutes "$APP"
  sudo -u minutes git clone -q "$REPO" "$APP"
fi

# Admin command on the server, e.g. `club-minutes login-link N0CALL` or `club-minutes roster`
cat > /usr/local/bin/club-minutes <<'WRAP'
#!/bin/sh
exec sudo -u minutes env CLUB_MINUTES_CONFIG=/etc/club-minutes/club.yaml CLUB_MINUTES_DATA=/var/lib/club-minutes \
  CLUB_MINUTES_GOOGLE_KEY=/etc/club-minutes/google-service-account.json /opt/club-minutes/.venv/bin/club-minutes "$@"
WRAP
chmod 755 /usr/local/bin/club-minutes

install -m 644 "$APP/deploy/club-minutes.service" "$APP/deploy/club-minutes-roster.service" \
  "$APP/deploy/club-minutes-roster.timer" /etc/systemd/system/
systemctl daemon-reload
"$APP/deploy/update.sh"
systemctl enable --now club-minutes-roster.timer
echo "Setup done. Copy $APP/club.minimal.yaml to $CONF/club.yaml and fill it in (docs/server-admin.md, Getting started), then: systemctl restart club-minutes"
