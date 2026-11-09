# W8LBZ Minutes

Meeting-minutes assistant for W8LBZ. See [REQUIREMENTS.md](REQUIREMENTS.md).

## What it does
Every change is listed in [CHANGELOG.md](CHANGELOG.md), and planned work in [ROADMAP.md](ROADMAP.md).
- **Archive:** each meeting is a Markdown file at `data/meetings/YYYY-MM-DD/minutes.md`, next to the original PDF. The importer converts the historical PDFs (and `*CORRECTED*.txt`) with a plain parser, no AI; imported meetings are marked "imported, unreviewed".
- **Home:** what's next for you: the next meeting, your chow night and swap requests, your business items, the latest minutes and upcoming events.
- **Minutes:** the archive (`/minutes`) in the modernized layout with PDF download (View original instead for imported minutes), one search box for the minutes (full text), members, guests, business items, chow nights and calendar events, per-member history, attendance stats with a chart, and guest tracking.
- **Leader console:** take the minutes live on meeting night while members follow along, then submit to publish and email them.
- **Members:** sign-in with passkeys or an emailed link, the roster, member profiles, and admin mode.
- **Business items:** Old and New Business tracked from week to week, with owners, due dates and reminders. Motions keep their mover, seconder and result (Business → Motions).
- **Calendar:** the club's calendars kept by the app, with .ics import, subscriptions and an optional copy to Google ([officers guide → Club calendars](docs/officers.md#club-calendars)).
- **Chow schedule:** the rotation, swaps, reminders before your night, and who provided, missed or covered each night.
- **Treasurer:** who owes what (dues, fees, missed chow, certificates), payments and expenses, regular bills and bills read at meetings, and funds a committee keeps; each member sees their own figures on Home.
- **Email preferences:** each member turns each kind of email on or off, including a meeting-day email (the time, your chow, your business items, the calendar until the next meeting and last meeting's minutes). Every optional email ends with a link to change it. The daily `reminders` timer (8 am) sends business and chow reminders and the meeting-day email.

## Guides
- [Members guide](docs/members.md): signing in, Home, your profile and emails, chow swaps, business, the calendar, your dues.
- [Officers guide](docs/officers.md): the roster and profiles, calendars, the chow schedule, the Treasurer.
- [Server admin guide](docs/server-admin.md): deploy, config, sign-in and email, admin mode, backups, hardening.
- [Developer guide](docs/developers.md): running it locally, previews, releases, security checks.

## License
Copyright © 2026 James Flattery, KO4CGQ.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE).

If you run a modified version for others over a network, you must offer them its source code (AGPL section 13). The About page's source link does this for the club's copy.
