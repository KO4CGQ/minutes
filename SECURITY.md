# Security

## Reporting a vulnerability
Please report security problems privately, not in a public issue: on GitHub, open this repository's **Security** tab
and choose **Report a vulnerability**. Only the maintainer sees the report.

Include what you found, how to reproduce it, and the version (the footer, or Admin → Updates). This is a one-person
volunteer project: expect a reply within a week or two, and a fix in a release once it's understood.

## Supported versions
Only the latest release gets security fixes. A club's server updates itself (Admin → Updates → Automatic updates)
or with **Update now**.

## What's checked on every change
The tests (including lint: ruff, shellcheck on the server scripts, zizmor on the GitHub workflows), CodeQL, pip-audit
against the locked packages, and gitleaks over the whole history. The Security page in the app (admins) shows sign-in
failures and bans; the [server admin guide](docs/server-admin.md) covers hardening.
