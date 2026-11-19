# Officers guide

For officers, meeting leaders, minutes editors, roster keepers, calendar editors and the Treasurer: running the club from the app. What every member sees is in the [members guide](members.md).

## 🔑 Who can do what
What a member may do comes from their **roles**. A role is given by the offices a member holds (the whole office title: "Assistant Treasurer" isn't the Treasurer's office, and a title that isn't an office gives no role) and by members picked by hand. Admins set what each role allows, which offices give it and who is picked, and add the club's own roles, on **Admin → Site → Roles**. A member's roles show on their page. As each role ships:

- 🏛️ **Officers** (holders of an officer's office) see every member's contact details and Roster changes, and can undo profile changes; they moderate (any business item, motions and suggestions, comments, pictures, the calendar change log), lead meetings and edit the calendar too.
- ✏️ **Minutes editors** (holders of any office) correct minutes already in the archive. A meeting's own leader and recorder may always correct its minutes.
- 🎙️ **Meeting leaders** (picked on Site → Roles) run the meeting console and edit the minutes and the calendar.
- 📅 **Calendar editors** (picked on Site → Roles, say the net manager) edit the calendar.
- 📋 **Roster keepers** (holders of the offices that add members: the Secretary and Treasurer, unless the club picks others) edit the roster.
- 📨 **The Secretary** gets the roster emails (new members, roster changes, the chow list).
- 💰 **The Treasurer** records money, and sees the Treasurer pages.

> [!TIP]
> Admins can do all of the above while admin mode is on.

## 👥 The roster
**Roster** in the menu. Members keep their own email, phone, address, birthday and sharing choices up to date on their profile.

- **Officers see everything**; other members see only what each member chose to share.
- **Every profile change** is emailed to the Secretary and listed under **Roster → Recent changes**, where any officer can undo it.
- **Editing a member** (roster keepers): **Edit roster details** on their page, in four tabs, each saved on its own. **Details:** name, class, status, contact details and other spellings of their name. **Titles:** their offices (shown; they change on the Offices page, which keeps their history) and other titles (ticked from those the club uses, or added). Roles aren't set here: they follow the offices, or an admin gives them on Site → Roles. **New callsign** and **Membership log** (joined, left, suspended…). Not their birthday or sharing choices: those are theirs.
- **Another member's email or status** are permissions of their own (Roster keeper has both, as it ships). A member's email, status and callsign are changed by an admin in admin mode, not a roster keeper, when the member holds an office, is an admin, or can do something the roster keeper can't; their phone, address, titles and other spellings stay everyday roster edits. A new email waits for its owner: a link goes to the new address and the old one is told at once; the change happens when the link is opened, and signs the member out everywhere.
- **A new callsign** (vanity call or upgrade): **New callsign** on the same page. The app takes a backup, then moves everything (minutes, chow, business, sign-ins, passkeys) to the new call. Old links keep working.
- **Birthdays and anniversaries** a member shares show at the meeting's Attendance step.
- **Recover admin access** (a link at the foot of the Offices page, `/recover`): for when the club can't reach its admins, or one is working against it. An officer names a member to become admin (any active member; they accept it themselves) and, if needed, admins to suspend, with the board's motion if there was one. A majority of the officers (at least 2) approve, each confirming it's them with a passkey or emailed link; once the member has accepted, it completes after 7 days. If an admin objects, it needs two-thirds of the officers and 14 days: an objection can delay it, not stop it. Every member is emailed at each step. The officer who started it can call it off, or a majority of the officers by each asking; and if it isn't approved and accepted within 30 days, it ends by itself. A suspended admin's rights stop counting (club.yaml's too) until another admin lifts it on Admin → Security → Admins. It can't protect against someone with the server itself.
- **Offices** (Roster → Offices): who holds each office now and who held it before. An admin changes a holder in one step; the app updates both members' titles, logs it and emails the officers. **Add a past term** adds history only.

![Editing a member's roster details](screenshots/roster-edit.png)

![The Offices page, in dark mode](screenshots/offices.png)

## 📅 Calendars
**Calendar** in the menu. The app keeps three calendars: **Club events** (meetings, nets, VE testing, fox hunts), **Planning** (officers' dates, like the annual meeting) and **Community** (what's on nearby). It adds chow nights, shared birthdays (never private ones), business items' due dates (on Planning), and holidays and election days if an admin turned them on.

![The calendar's month view, in dark mode](screenshots/calendar.png)

**Adding and changing events** (leaders and calendar editors):

- Start, optional end, where, notes, an **Online link** (shown as a **Join online** button), and a time zone if it isn't the club's (Field Day in UTC, say).
- **Repeats**: daily, weekly, every 2 weeks, monthly (2nd Tuesday, last Tuesday, 1st and 3rd Tuesday, or a date), yearly.
- Change or delete a repeating event for **this date only**, **this and following dates**, or **every date**.
- Look-alike events on the same day are marked **possible duplicate**; delete one if they're the same.
- **Leave out of the leader console's Upcoming Events**: tick it for things not worth reading out (the weekly meeting is ticked already).
- **Who changed what** (officers) lists every change.

> [!TIP]
> To skip the meeting on a holiday, delete that one date.

**The weekly meeting** is on Club events. To change its day, time, place or online link, an admin uses **Admin → Site → Club** and picks the date it starts; earlier meetings stay as they were.

📥 **Import .ics** (Calendar → Import .ics): events from Google Calendar, Outlook or Apple. In Google Calendar: Settings → Import & export → Export. Importing the same file again updates rather than doubles. Anything it can't bring in is listed, to add by hand.

📲 **Subscribing:** every member gets a private link under **Subscribe in your calendar app**, for their phone or calendar app. **Make a new link** if it's been shared by mistake. An admin can also make some calendars public (Admin → Site → Features); chow, birthdays and due dates are never public.

## 🎙️ At the meeting
The leader console's **Upcoming Events** step lists what's on until the next meeting, to read out. **+ Add** puts one in the minutes. **Use the calendar's** swaps a typed entry for the calendar's own, so it shows once. Events you add in the minutes go on the calendar you pick beside them (Club events unless you change it).

✏️ **Correct these minutes**, on a meeting's page, fixes minutes already in the archive (the console only works on a meeting in progress). The meeting's leader and recorder can, and so can any board member and an admin in admin mode. Open a part (attendance, guests, any section) to change it; **+ Add** adds a guest or an item left out, **Remove** takes one out (click again to keep it). Say why in a line ("Corrected a misspelled name"), then **Preview the changes**: the version now and the new one side by side, removed lines in red, added ones in green, and in a changed line the words that changed. Saving makes a new version: the old one stays readable, linked from the footer, which shows who corrected it and why. **Send the corrected minutes to members** (ticked) emails them what changed: a changed line's new text and the few words it replaced, and only how many lines were removed, never their words. Untick it for something no one needs telling about, and the footer says "no notice sent". **This removes private details**: tick it when taking out something that shouldn't have been recorded (a health matter, a phone number); the email then shows no old words at all, and every earlier version (an imported meeting's original PDF too) is visible only to those who can correct minutes. Old Business and Upcoming Chow are the text read that night: correcting them doesn't change the business items or the chow schedule.

Imported minutes (from before the app) say "not yet reviewed" until someone reads them against the original: **Mark as reviewed** if nothing needs fixing, or correct them, which reviews them too.

## 🍲 Chow
Only if the club has the chow module (an admin turns it on under Admin → Site → Features). **Chow** in the menu.

- Members ask each other to swap; leaders change the list directly.
- On meeting night the console records who provided, missed or was covered.
- **The printable list** (PDF): officers email it to members from the Chow page, which shows whether it has changed since it was last sent.

![The chow schedule](screenshots/chow.png)

## 💰 Treasurer
**Treasurer** in the menu: officers see it; the Treasurer (and admins in admin mode) record money and change settings.

⚙️ **Getting started (Settings tab):**

- **Tracking:** Off, **Preview** (officers see everything, members nothing, no emails: for checking the figures first) or **On** (members see their own figures on Home).
- **Start date:** nothing is owed before it. Older balances go in as adjustments on that date.
- **Amounts:** dues per meeting night, initiation fee, the bond, a missed chow night. Each change has a date it takes effect, so earlier weeks keep the old amount.
- **Arrears:** how many weeks behind counts (4 by default), and whether the Treasurer and Board get an email when someone gets there.
- **Accounts, funds** and **spending categories** (up to 8, each keeps its chart colour).

![Treasurer → Settings](screenshots/treasurer-settings.png)

🧾 **Who owes what** is worked out, never typed: dues for every meeting night (holidays too; 52 × $3.50 is $182 a year), initiation, missed chow nights and adjustments, less payments. Members may pay weekly, monthly or yearly; paying ahead shows as "paid through" a date. Members in arrears show in red.

> [!NOTE]
> **An example:** dues are $3.50 a meeting night and tracking started 26 weeks ago, so a long-time member owes $91.00 so far. Pat has paid $66.50: Pat owes **$24.50**, 7 weeks, in red (4 weeks or more). Max joined 8 weeks ago: the $25.00 initiation fee plus 8 nights ($28.00), less what he's paid.

![Who owes what](screenshots/treasurer.png)

**The bond** (only if the club has one): who still owes it and by when. A bond bought before the app: **Record a bond bought before…** at the top of Who owes what. It's kept on file, not counted as money now.

> [!IMPORTANT]
> **Arrears and suspension:** the app never suspends anyone. When the club does, a roster keeper sets the member's status to **Suspended** and adds a Membership Log row. Dues keep counting.

**Recording money:** each expense gets a category guessed from its words ("pizza" → Food), which you can change. Payments, other income (drink sales, donations), adjustments (a charge, or a negative one as a credit) and expenses, each with a date and an account. **Delete** fixes a mistake; the log keeps it.

📄 **Bills:**

- **Regular bills** (Bills tab): what the club pays every month or year. The tab shows which haven't been paid yet.
- **Bills read at meetings:** each amount under Bills and Communications in the minutes gets its own card ("Gas Bill $81"). Mark each **Paid** (an expense), **Not a bill**, or match it to a regular bill so it isn't counted twice. Nothing counts until it's marked. **Look in older minutes** finds bills from before the start date.
- At the meeting, the console's Bills step lists what was paid since the last one; **Add to tonight's bills** puts it in the minutes.

![Treasurer → Bills: the regular bills, each paid this month or year](screenshots/treasurer-bills.png)

🥤 **Funds** run by a committee (the drinks fund, say): their keepers see only their fund's account and record its money.

📊 **Spending** (in the menu): the last 12 months by category, and the cost per member each month next to what dues bring in. Every member sees it once tracking is On. It never shows anyone's dues.

![Spending: a year of the club's bills by category](screenshots/spending.png)

> [!NOTE]
> Not a bookkeeping system: no account balances or transfers yet.

## 🛡️ Choosing an admin
Admins can see and change everything in the app while admin mode is on: every member's contact details, the roles,
the backups, the updates and the Security page. A **server admin** also signs in to the server itself, so can read
every file there. Choose them the way the club would choose who holds its bank card.

**How many.** At least two, so the club isn't locked out if one is unavailable (the Admin page warns while there's
only one). They needn't be officers. If the club ever can't reach its admins, the officers can make a new one:
**Recover admin access** (above, under The roster).

**Who.** A member the club trusts with every member's personal details, who will be around for a while, and who is:

- **Careful with accounts:** a password manager, a different password for everything, a passkey or two-step sign-in on
  their own email (their email is a way into admin mode), a screen lock on their phone, and never sharing a sign-in.
- **Security-minded:** suspicious of unexpected links and requests (an "officer" emailing to ask for someone's
  details, say), and quick to tell the club when something looks wrong.
- **Comfortable with the app** (an app admin), or **also with a command line** (a server admin): signing in to the
  server over SSH, pasting the commands the [server admin guide](server-admin.md) gives, and reading a log. Updates and
  backups run from the Admin page, so day to day a server admin rarely needs the command line; it's for when
  something goes wrong.

**Before they start** (a checklist for the officers and the new admin):

1. They read the [server admin guide](server-admin.md) (a server admin) or its Admin mode and Security parts (an app admin).
2. On a test copy, if the club has one (a staging server): turn on admin mode, change a role and put it back, view
   the app as a member, and (a server admin) restore a backup into an empty folder and run an update.
3. They know the club's rules for its IT: who else holds each password (two people for each), where the backup
   passphrase is kept, and who they answer to.
4. They sign the club's admin agreement (below), kept with the club's records.
5. An existing admin adds them on Admin → Security → Admins; they confirm they can turn on admin mode.

**When an admin steps down:** add the successor first (the last active admin can't be removed), hand over or
change the passwords they held, and remove them on Admin → Security → Admins.

### An admin agreement (a template)
Adapt it to the club's bylaws and records; it isn't legal advice.

> **[Club name] — Club Minutes administrator agreement**
>
> I, **[name, callsign]**, agree to serve as an administrator of the club's Club Minutes app
> (☐ app admin ☐ server admin) from **[date]**, and that:
>
> 1. I'll use admin access only for club business, and only what the job needs.
> 2. Members' personal details (contact details, birthdays, dues, attendance) are confidential: I won't copy, share or
>    keep them outside the app, except the club's own backups.
> 3. I'll keep my accounts secure: a unique password in a password manager, a passkey or two-step sign-in on the email
>    I use for the club, and I won't share my sign-in.
> 4. I'll tell **[the President / the Board]** within **[24 hours]** of anything that may have exposed club data or
>    my access (a lost phone, a suspicious sign-in, a mistake).
> 5. When I step down, or if the Board asks, I'll hand over what I hold (passwords, keys, the backup passphrase) and
>    help add a successor. I understand the officers can recover admin access without me if they need to.
>
> Signed: ____________________ Date: __________
>
> For the club (President): ____________________ Date: __________
>
> Witness (Secretary): ____________________ Date: __________
