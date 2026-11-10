"""Web UI: archive, minutes view and PDF, search, member history, stats, guests, leader console, login."""
from __future__ import annotations

import datetime as dt
import functools
import json
import logging
import os
import re
import threading
import time
from importlib.metadata import metadata, requires, version
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from . import ai, avatars, limits, backup, business, calview, chow, console, events, gcal, membership, offices, prefs, profile, treasurer, updates
from . import auth as authmod
from starlette.concurrency import run_in_threadpool  # async routes hand slow work (the roster sheet, email, S3) to a thread
from . import callsign as callsign_mod
from .auth import Auth, _safe_next
from .club import DATA, PREVIEW, one_line, valid_email
from .roster import COLUMNS, KEY_FILE, SHAREABLE, SheetError

KEEPER_FIELDS = tuple(f for f in COLUMNS if f not in ("birthday", "share"))  # those two are the member's own choice
from .meeting import SECTIONS
from .store import Archive, spellings

HERE = Path(__file__).parent
app = FastAPI(title="W8LBZ Minutes", docs_url=None, redoc_url=None, openapi_url=None)  # no API map for visitors
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
archive = Archive()
authn = Auth(archive.club)

# Show the app's own log lines (emails sent or withheld, rate limits) alongside uvicorn's.
_log = logging.getLogger("w8lbz")
if not _log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
    _log.propagate = False

COOKIE = "w8lbz_session"
CHALLENGE_COOKIE = "w8lbz_pk"
PUBLIC_PATHS = ("/login", "/auth/passkey/login/", "/static/", "/favicon.ico",
                "/calendar/public", "/calendar/feed/",  # public calendars (if any are made public); private feeds carry their own secret
                "/api/roster")  # its own token, and only from the server itself
URL_RE = re.compile(r"(https?://[^\s<]+[^\s<.,)])")


def linkify(text: str) -> Markup:
    return Markup(URL_RE.sub(r'<a href="\1">\1</a>', str(escape(text))))


def by_last_name(ids):
    c = archive.club
    return sorted(ids, key=lambda i: (c.members[i].last, c.members[i].first) if i in c.members else (i, ""))


DATES = {"list": "%b %-d, %Y", "document": "%B %-d, %Y", **(archive.club.info.get("date_formats") or {})}
templates.env.filters["linkify"] = linkify
templates.env.filters["longdate"] = lambda d: d.strftime(DATES["document"])
templates.env.filters["listdate"] = lambda d: d.strftime(DATES["list"])
templates.env.filters["isodate"] = lambda d: dt.date.fromisoformat(d).strftime(DATES["list"])  # "2026-11-09" in the list format
templates.env.filters["by_last_name"] = by_last_name
templates.env.filters["clock"] = lambda t: dt.datetime.fromtimestamp(t).strftime("%-I:%M %p")
def monthday(md: str | None) -> str:
    """'07-04' -> 'July 4', '07' (month only) -> 'July'. Anything else (e.g. a full date typed into the sheet) is shown as is."""
    try:
        if md and len(md) == 2:
            return dt.date(2000, int(md), 1).strftime("%B")
        return dt.date(2000, int(md[:2]), int(md[3:5])).strftime("%B %-d") if md and len(md) == 5 else (md or "")
    except ValueError:
        return md


templates.env.filters["monthday"] = monthday
templates.env.filters["tsfull"] = lambda t: (dt.datetime.fromtimestamp(t).strftime(DATES["list"] + ", %-I:%M %p")
                                             .replace("AM", "am").replace("PM", "pm")) if t else ""
def _initials(mid: str | None) -> str:
    m = archive.club.members.get(mid or "")
    return ((m.first[:1] + m.last[:1]) if m else (mid or "?")[:2]).upper()


templates.env.globals.update(avatar_url=avatars.url, avatar_hue=avatars.hue, initials=_initials)
def _md(text: str | None) -> Markup:
    """Markdown to HTML for release notes (from our own CHANGELOG via GitHub). Raw HTML is escaped first."""
    import markdown
    return Markup(markdown.markdown((text or "").replace("&", "&amp;").replace("<", "&lt;")))


templates.env.filters["md"] = _md
templates.env.filters["isodate"] = lambda d: dt.date.fromisoformat(d)
templates.env.filters["ts"] = lambda t: dt.datetime.fromtimestamp(t).strftime(DATES["list"]) if t else "never"
def _assets_stamp() -> str:
    """A fingerprint of the CSS and JS, added to their links (?v=...) so browsers fetch new copies after an update."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted((HERE / "static").glob("*.[cj]s*")):
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals.update(club=archive.club, SECTIONS=SECTIONS, VERSION=version("w8lbz-minutes"), PREVIEW=PREVIEW,
                             ASSETS=_assets_stamp())


# ---------------- login gate ----------------

@app.middleware("http")
async def require_login(request: Request, call_next):
    if authn.banned(client_ip(request)):
        return HTMLResponse("This address has been blocked after repeated failed sign-ins. "
                            "If that's a mistake, ask a club officer.", status_code=403)
    archive.club.refresh()  # pick up a roster sync without a restart
    token = request.cookies.get(COOKIE)
    real = authn.member_for(token)
    request.state.real_member = real
    request.state.admin_until = authn.admin_until(token) if real else None
    # "View as": an admin in admin mode sees the app exactly as another member does, read-only.
    va = authn.view_as(token) if request.state.admin_until else None
    request.state.viewing_as = va if va in archive.club.members and va != real else None
    request.state.member = request.state.viewing_as or real
    path = request.url.path
    if request.state.viewing_as:
        request.state.admin_until = None  # their permissions, not the admin's
        if path.startswith(("/account", "/profile/email", "/logout", "/auth/")) or (
                request.method not in ("GET", "HEAD") and not path.startswith("/admin/")):
            return HTMLResponse("You're viewing the app as another member, which is read-only. "
                                "Stop viewing as them (banner at the top) to make changes.", status_code=403)
    if request.state.member or path.startswith(PUBLIC_PATHS):
        return await call_next(request)
    if request.method == "GET" and not path.startswith("/api/"):
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", status_code=303)
    return JSONResponse({"detail": "Please sign in"}, status_code=401)


CALENDAR_PATHS = ("/chow", "/api/meetings", "/console", "/calendar")  # changes here can move chow nights or events


@app.middleware("http")
async def upload_limit(request: Request, call_next):
    """Turn away a request bigger than the admin's upload limit before reading it (a huge upload would fill the disk)."""
    size = request.headers.get("content-length", "")
    if size.isdigit() and int(size) > limits.max_upload() + limits.FORM_ROOM:
        return HTMLResponse(f"That's too big to upload: {limits.upload_mb()} MB at most. Go back and pick a smaller file.",
                            status_code=413)
    return await call_next(request)


@app.middleware("http")
async def calendar_after_changes(request: Request, call_next):
    """After a chow or console change, sync the club calendars a little later (see gcal.soon)."""
    response = await call_next(request)
    if (request.method != "GET" and response.status_code < 400 and request.url.path.startswith(CALENDAR_PATHS)
            and getattr(request.state, "member", None)):  # signed-in changes only
        gcal.soon(lambda: archive.club, lambda: archive)
    return response


@app.middleware("http")  # added last, so it runs outermost and covers the login redirects too
async def security_headers(request: Request, call_next):
    import secrets
    request.state.nonce = nonce = secrets.token_urlsafe(16)  # pages mark their own <script> blocks with it (page())
    response = await call_next(request)
    h = response.headers
    # scripts only from the app's own files or a <script> block this page was given the nonce for: injected markup
    # (an <img onerror=...>, a <script> in typed text) doesn't run. Inline style= attributes are still allowed.
    h.setdefault("Content-Security-Policy", f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
                 "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("X-Robots-Tag", "noindex, nofollow")  # members-only; keep the public hostname out of search engines
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if is_https(request):  # browsers then refuse a downgraded (http) connection to this hostname for a year
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


def me(request: Request) -> str:
    return request.state.member


def real_me(request: Request) -> str:
    """The signed-in member, even while viewing the app as someone else."""
    return request.state.real_member


def admin_mode(request: Request) -> bool:
    return bool(getattr(request.state, "admin_until", None))


def privileged(request: Request) -> bool:
    """Officers, and admins while admin mode is on: see all contact details, the change log, undo."""
    m = archive.club.members.get(getattr(request.state, "member", None) or "")
    return bool(m and (m.officer or admin_mode(request)))


def leader(request: Request) -> str:
    if not (archive.club.can_lead(request.state.member) or admin_mode(request)):
        raise HTTPException(403, "Only officers and minutes editors can lead a meeting.")
    return request.state.member


def officer(request: Request) -> str:
    if not privileged(request):
        raise HTTPException(403, "Only officers can do that.")
    return request.state.member


PK_OFFER = "pk_offer"  # set by an emailed-link sign-in; base.html offers a passkey while it's there


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    mid = getattr(request.state, "member", None)
    return templates.TemplateResponse(request, name, {
        "club": archive.club,
        "me": archive.club.members.get(mid) if mid else None,
        "can_lead": (archive.club.can_lead(mid) or admin_mode(request)) if mid else False,
        # JS weekday (0 = Sunday) and time of the weekly meeting, for the "Start meeting" button
        "meeting_at": f"{(chow.WEEKDAYS.index(str(archive.club.info.get('meeting_day', 'Thursday')).lower()) + 1) % 7} "
                      f"{archive.club.info.get('meeting_time', '19:30')}",
        "is_admin": archive.club.is_admin(mid), "admin_until": getattr(request.state, "admin_until", None),
        "viewing_as": getattr(request.state, "viewing_as", None),
        **_money_nav(request, mid),
        # tonight's meeting while it's being taken; the top bar links everyone to its live minutes
        "live": next((m for m in archive.meetings if m.status == "draft" and m.date == dt.date.today()), None),
        "update_available": _update_badge(request),
        "pk_offer": bool(mid and request.cookies.get(PK_OFFER) and request.url.hostname == authn.rp_id),
        "rp_id": authn.rp_id, "csp_nonce": getattr(request.state, "nonce", ""), **ctx})


def _money_nav(request: Request, mid: str | None) -> dict:
    """Menu links: Treasurer for a fund's keepers too, Spending for whoever may see it."""
    if not mid:
        return {"keeps_fund": False, "show_spending": False}
    s = treasurer.load()
    return {"keeps_fund": bool(treasurer.kept_accounts(archive.club, s, mid)), "show_spending": spending_visible(request, s)}


def _update_badge(request: Request) -> str | None:
    """For admins (in admin mode or not): the newer version, if there is one (cached check, see updates.py)."""
    if not archive.club.is_admin(getattr(request.state, "real_member", None)):
        return None
    try:
        rel = updates.available()
    except Exception:
        return None
    return rel["version"] if rel else None


def is_https(request: Request) -> bool:
    # Behind `tailscale serve` the app itself sees plain HTTP; the passkey host is always HTTPS.
    return request.url.scheme == "https" or request.url.hostname == authn.rp_id


def base_url(request: Request) -> str:
    """Where this member reached us (LAN address or the HTTPS tailnet name), for links in emails.
    Only our own names count: a forged Host header would otherwise put a working sign-in link
    pointing at someone else's server into a member's inbox."""
    host = request.headers.get("host", "")
    ip, port = request.scope.get("server") or ("", 0)  # the address this connection actually reached
    if host in (authn.rp_id, f"{ip}:{port}") or (host == ip and port in (80, 443)):
        return f"{'https' if is_https(request) else 'http'}://{host}"
    return (authn.base_url or f"http://{ip}:{port}").rstrip("/")


def client_ip(request: Request) -> str | None:
    """The visitor's address. Through Tailscale Funnel it's their real internet address (X-Forwarded-For,
    trusted only from 127.0.0.1); on the LAN, their LAN address."""
    return request.client.host if request.client else None


def start_session(request: Request, response: Response, member: str, method: str) -> Response:
    token = authn.new_session(member, request.headers.get("user-agent", ""), client_ip(request), method)
    response.set_cookie(COOKIE, token, max_age=authn.session_days * 86400, httponly=True, samesite="lax",
                        secure=is_https(request))
    return response


async def _json(request: Request):
    """The request's JSON body; anything that isn't JSON is a bad request (400), not a server error."""
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(400, "That request wasn't readable.") from None


def get_meeting(slug: str):
    m = archive.by_slug.get(slug)
    if not m:
        raise HTTPException(404, "No minutes for that date")
    return m


# ---------------- login & account ----------------

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/static/favicon.png", status_code=301)  # browsers ask for it regardless of <link rel=icon>


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", sent: int = 0, error: str = ""):
    if request.state.member:
        return RedirectResponse(_safe_next(next), status_code=303)
    return page(request, "login.html", next=_safe_next(next), sent=sent, error=error)


@app.post("/login/email")
def login_email(request: Request, email: str = Form(...), next: str = Form("/")):
    authn.send_link(email, next, base_url=base_url(request), ip=client_ip(request))
    return RedirectResponse(f"/login?sent=1&next={quote(_safe_next(next))}", status_code=303)


@app.get("/login/link")
def login_link(request: Request, t: str = ""):
    used = authn.use_link(t)
    if not used:
        authn.event("bad_link", client_ip(request))
        return RedirectResponse("/login?error=link", status_code=303)
    member, next_url = used
    response = start_session(request, RedirectResponse(next_url, status_code=303), member, "email link")
    if request.url.hostname == authn.rp_id:  # passkeys only work there: offer one (base.html, if this device can make one)
        response.set_cookie(PK_OFFER, "1", samesite="lax", secure=True)  # until the browser closes, Not now, or one is added
    return response


@app.post("/logout")
def logout(request: Request):
    authn.end_session(request.cookies.get(COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


@app.get("/account", response_class=HTMLResponse)
def account(request: Request, mid: str = Depends(me)):
    return page(request, "account.html", passkeys=authn.passkeys(mid), sessions=authn.sessions(mid),
                passkey_host=request.url.hostname == authn.rp_id)


# ---------------- tracked business items ----------------


def can_lead_now(request: Request) -> bool:
    return archive.club.can_lead(request.state.member) or admin_mode(request)


def business_change(request: Request, fn, meeting: str = "", back: str = "/business"):
    """Run fn(state) under the lock, save, and refresh that meeting's draft minutes. JSON for the console."""
    with business.locked():
        s = console.business_state(archive)
        try:
            fn(s)
        except ValueError as e:
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"error": str(e)}, status_code=409)
            path, _, frag = back.partition("#")
            return RedirectResponse(_safe_next(f"{path}{'&' if '?' in path else '?'}error=" + quote(str(e))
                                               + (f"#{frag}" if frag else "")), status_code=303)
        business.save(s)
        if meeting:
            console.sync_business(archive, meeting, s)
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse(console.business_items(archive, meeting, s) if meeting else {"ok": True})
    return RedirectResponse(_safe_next(back), status_code=303)


def owner_id(value: str | None) -> str | None:
    """The owner box takes a name typed or picked from the list (or a member id, or a callsign)."""
    v = " ".join((value or "").split())
    if not v:
        return None
    club = archive.club
    if v.lower() in club.members:
        return v.lower()
    hits = [m.id for m in club.members.values()
            if v.lower() in (m.name.lower(), (m.callsign or "").lower(), club.short(m.id).lower())]
    if len(hits) != 1:
        raise ValueError(f"No member called “{v}”. Pick a name from the list.")
    return hits[0]


def manages_business(request: Request, meeting: str = "") -> bool:
    """Who changes any business item: officers (and admins in admin mode) on the Business page, and whoever
    leads a meeting from its console (`meeting` set). Minutes editors don't get it on the Business page."""
    return privileged(request) or bool(meeting and can_lead_now(request))


def _may_edit_item(request: Request, s: dict, iid: str, meeting: str = "") -> None:
    """Officers (or the meeting's leader) change any item; a member can update the ones they own."""
    if not (manages_business(request, meeting) or business.get(s, iid)["owner"] == request.state.member):
        raise ValueError("Only an officer or the item's owner can change it.")


@app.get("/business", response_class=HTMLResponse)
def business_page(request: Request, tab: str = "open", error: str = "", note: str = "", result: str = "",
                  mid: str = Depends(me)):
    s = console.business_state(archive)
    today = dt.date.today().isoformat()
    items = [business.view(archive.club, x, today) for x in s["items"]]
    open_ = sorted((x for x in items if x["status"] == "open"), key=lambda x: (x["due"] or "9999", x["opened"]))
    done = sorted((x for x in items if x["status"] == "done"), key=lambda x: x["closed"], reverse=True)[:50]
    members = sorted((m for m in archive.club.members.values() if m.status.lower() not in ("inactive", "silent key")),
                     key=lambda m: (m.last, m.first))
    leader_ = manages_business(request)
    pending = [g for g in s["suggestions"] if g["status"] == "pending"] if leader_ else []
    submitted = [m for m in archive.meetings if m.status != "draft"][:6]
    my_notes = [n for n in s["notes"] if n["by"] == mid and n["status"] == "pending"]
    all_notes = [n for n in s["notes"] if n["status"] == "pending"] if leader_ else []
    tabs = [("open", f"Open ({len(open_)})"), ("notes", "Notes for the next meeting"), ("done", "Done"), ("motions", "Motions")]
    if leader_:
        tabs.insert(1, ("add", "Add an item"))
    if leader_ and (pending or ai.configured(archive.club)):
        tabs.append(("suggestions", f"Suggestions ({len(pending)})"))
    if tab not in dict(tabs):
        tab = "open"
    return page(request, "business.html", tab=tab, tabs=tabs, open_items=open_, done=done, error=error, note=note, leader=leader_,
                my_notes=my_notes, all_notes=all_notes, next_meetings=upcoming_meetings(),
                suggestions=pending, ai_on=ai.configured(archive.club) and leader_, submitted=submitted,
                by_id={x["id"]: x for x in items}, results=business.RESULTS, result=result,
                motions=[x for x in business.motions(s) if not result or x["motion"]["result"] == result],
                members=members, today=today, fmt=lambda d: dt.date.fromisoformat(d).strftime("%b %-d, %Y"))


def run_followups(slug: str) -> str:
    """Ask the AI for follow-ups from a meeting's minutes and store them as pending suggestions."""
    m = archive.by_slug[slug]
    with business.locked():
        s = console.business_state(archive)
        open_items = [x for x in s["items"] if x["status"] == "open"]
    found = ai.suggest_followups(archive.club, m, open_items)  # outside the lock: it takes a few seconds
    with business.locked():
        s = console.business_state(archive)
        n = business.add_suggestions(s, slug, found, archive.club.ai.get("model", ""))
        business.save(s)
    _log.info("AI suggested %d follow-ups for %s", n, one_line(slug))
    return f"{n} suggestion{'s' if n != 1 else ''} from the {m.date:%B %-d} minutes." if n else \
        f"No follow-ups found in the {m.date:%B %-d} minutes."


def followups_after_submit(slug: str) -> None:
    """After Submit: AI follow-up suggestions, and fresh "ask about" prompts for next week's reports."""
    if ai.configured(archive.club):
        try:
            run_followups(slug)
        except Exception:
            _log.exception("AI follow-ups for %s failed", slug)
        try:
            console.refresh_ask_about(archive)
        except Exception:
            _log.exception("AI ask-about prompts after %s failed", slug)


@app.post("/business/suggest")
def business_suggest(request: Request, meeting: str = Form(...), mid: str = Depends(officer)):
    if not ai.configured(archive.club):
        raise HTTPException(404)
    get_meeting(meeting)
    try:
        msg = run_followups(meeting)
    except ai.AIError as e:
        return RedirectResponse("/business?tab=suggestions&error=" + quote(str(e)), status_code=303)
    return RedirectResponse("/business?tab=suggestions&note=" + quote(msg), status_code=303)


@app.post("/business/suggestions/{sid}/accept")
def suggestion_accept(request: Request, sid: str, text: str = Form(""), owner: str = Form(""), due: str = Form(""),
                      mid: str = Depends(officer)):
    return business_change(request, lambda s: business.accept_suggestion(s, sid, mid, text or None, owner_id(owner) or "", due),
                           back="/business?tab=suggestions")


@app.post("/business/suggestions/{sid}/dismiss")
def suggestion_dismiss(request: Request, sid: str, mid: str = Depends(officer)):
    return business_change(request, lambda s: business.dismiss_suggestion(s, sid, mid), back="/business?tab=suggestions")


@app.post("/business/add")
def business_add(request: Request, text: str = Form(...), owner: str = Form(""), due: str = Form(""),
                 agenda: str = Form(""), meeting: str = Form(""), mid: str = Depends(me)):
    if not manages_business(request, meeting):
        raise HTTPException(403, "Only officers, or the meeting's leader in the console, can add business items.")
    return business_change(request, lambda s: business.add(s, text, mid, owner_id(owner), due, agenda,
                                                           opened=meeting or None), meeting,
                           back="/business?note=Added." if not meeting else "/business")


@app.post("/business/motion")
def business_motion(request: Request, mover: str = Form(""), seconder: str = Form(""), what: str = Form(""),
                    result: str = Form(""), meeting: str = Form(""), date: str = Form(""), mid: str = Depends(me)):
    """A motion from the console (meeting set), or a past one an officer records from old minutes (date set)."""
    if not manages_business(request, meeting):
        raise HTTPException(403, "Only officers, or the meeting's leader in the console, can record motions.")
    return business_change(request, lambda s: business.add_motion(
        s, archive.club, mid, owner_id(mover), owner_id(seconder), what, result, opened=meeting or date or None,
        past=not meeting), meeting, back="/business?tab=motions&note=Recorded." if not meeting else "/business")


@app.post("/business/{iid}/motion")
def business_motion_edit(request: Request, iid: str, mover: str = Form(""), seconder: str = Form(""), what: str = Form(""),
                         result: str = Form(""), mid: str = Depends(officer)):
    return business_change(request, lambda s: business.set_motion(s, archive.club, iid, mid, owner_id(mover),
                                                                  owner_id(seconder), what, result),
                           back=f"/business?tab=motions#i{iid}")


@app.post("/business/{iid}/edit")
async def business_edit(request: Request, iid: str, mid: str = Depends(me)):
    form = await request.form()
    meeting = form.get("meeting", "")
    allowed = ("text", "owner", "due", "agenda") if manages_business(request, meeting) else ("text", "due")  # owners
    fields = {k: form.get(k) for k in allowed if k in form}
    def run(s):
        _may_edit_item(request, s, iid, meeting)
        if "owner" in fields:
            fields["owner"] = owner_id(fields["owner"])
        business.edit(s, iid, mid, meeting=form.get("meeting") or None, **fields)
    return await run_in_threadpool(business_change, request, run, form.get("meeting", ""))


@app.post("/business/{iid}/comment")
def business_comment(request: Request, iid: str, text: str = Form(...), mid: str = Depends(me)):
    """Any member can comment on an item: an update, a question, "I can help with this"."""
    return business_change(request, lambda s: business.comment(s, iid, mid, text), back=f"/business#i{iid}")


@app.post("/business/{iid}/comments/{cid}/delete")
def business_comment_delete(request: Request, iid: str, cid: str, mid: str = Depends(me)):
    return business_change(request, lambda s: business.delete_comment(s, iid, cid, mid, privileged(request)),
                           back=f"/business#i{iid}")


def upcoming_meetings(n: int = 4) -> list[dt.date]:
    today = dt.date.today()
    return chow.meeting_nights(archive.club, today, today + dt.timedelta(days=7 * n))[:n]


@app.post("/business/notes")
def business_note_add(request: Request, text: str = Form(...), meeting: str = Form(...), mid: str = Depends(me)):
    if meeting not in {d.isoformat() for d in upcoming_meetings()}:
        raise HTTPException(400, "Pick one of the next meetings.")
    return business_change(request, lambda s: business.add_note(s, mid, text, meeting), back="/business?tab=notes")


@app.post("/business/notes/{nid}/{status}")
def business_note_status(request: Request, nid: str, status: str, meeting: str = Form(""), mid: str = Depends(me)):
    if status not in ("read", "dismissed", "withdrawn"):
        raise HTTPException(404)
    return business_change(request, lambda s: business.note_status(s, nid, status, mid, can_lead_now(request)),
                           meeting, back="/business?tab=notes")


@app.post("/business/{iid}/done")
def business_done(request: Request, iid: str, done: int = Form(1), meeting: str = Form(""), mid: str = Depends(me)):
    def run(s):
        _may_edit_item(request, s, iid, meeting)
        business.close(s, iid, mid, meeting or None) if done else business.reopen(s, iid, mid)
    return business_change(request, run, meeting, back="/business?tab=done" if done else f"/business#i{iid}")


@app.post("/business/{iid}/delete")
def business_delete(request: Request, iid: str, meeting: str = Form(...), mid: str = Depends(leader)):
    m = archive.by_slug.get(meeting)
    if not m or m.status != "draft":  # deleting leaves no trace, so only while that meeting is still being written
        raise HTTPException(403, "Items can only be deleted at a meeting that's still being written.")
    return business_change(request, lambda s: business.remove(s, iid, meeting), meeting)


# ---------------- chow list ----------------
_chow_lock = threading.Lock()


def can_edit_chow(request: Request) -> bool:
    """Changing the schedule: officers (it's the Secretary's job day to day) and admins in admin mode.
    Minutes editors see the member view; they record chow on meeting night from the console."""
    return privileged(request)


def chow_change(request: Request, fn, back: str = "/chow"):
    """Run fn(state) under the lock and save. ValueError -> shown on the Chow page."""
    with _chow_lock:
        s = chow.load()
        try:
            res = fn(s)
        except ValueError as e:
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"error": str(e)}, status_code=409)
            return RedirectResponse(f"{back}{'&' if '?' in back else '?'}error=" + quote(str(e)), status_code=303)
        chow.save(s)
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"ok": True, **(res if isinstance(res, dict) else {})})
    return RedirectResponse(back, status_code=303)


def chow_leader(request: Request) -> str:
    if not can_edit_chow(request):
        raise HTTPException(403, "Only leaders can change the chow schedule.")
    return request.state.member


def chow_list_pdf() -> tuple[str, bytes]:
    today = dt.date.today()
    return (f"chow-list-{today:%Y-%m-%d}.pdf", html_to_pdf("chow_list.html", nights=chow.upcoming(chow.load(), today, n=10_000),
                                                           today=today))


@app.get("/chow/list.pdf")
def chow_pdf(mid: str = Depends(me)):
    name, data = chow_list_pdf()
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.post("/chow/email")
def chow_email(request: Request, mid: str = Depends(chow_leader)):
    """Send the chow list to every member who wants it (Email tab), e.g. after filling or changing the schedule."""
    to = sorted({m.email.strip() for m in prefs.emailable(archive.club) if prefs.wants(m.id, "chowlist") == "pdf"}, key=str.lower)
    pdf = chow_list_pdf()
    return chow_change(request, lambda s: chow.email_list(archive.club, s, pdf, to, mid, base_url(request)))


@app.get("/chow", response_class=HTMLResponse)
@app.get("/chow/{tab}", response_class=HTMLResponse)
def chow_page(request: Request, tab: str = "schedule", error: str = "", mid: str = Depends(me)):
    s, club = chow.load(), archive.club
    leader = can_edit_chow(request)
    if tab not in ("schedule", "rotation", "history") or (tab != "schedule" and not leader):
        raise HTTPException(404)
    members = sorted((m for m in club.members.values() if m.status.lower() not in ("inactive", "silent key")),
                     key=lambda m: (m.last, m.first))
    today = dt.date.today()
    return page(request, "chow.html", s=s, error=error, leader=leader, tab=tab, members=members,
                upcoming=chow.upcoming(s, n=16), past=chow.past(s, n=16) if leader else [],
                mine=[d for d, x in chow.upcoming(s, n=60) if x.get("member") == mid and x.get("status") != "none"],
                asked=[r for r in s["swaps"] if r["status"] == "pending" and mid in (r["with"], r["requester"])],
                next_up=chow.next_up(club, s), today=today.isoformat(),
                default_until=(today + dt.timedelta(days=180)).isoformat(),
                list_changed=chow.list_changed(s), emailed=s.get("emailed"),
                log=chow.history(30) if leader else [], fmt=lambda d: dt.date.fromisoformat(d).strftime("%a %b %-d"))


def is_treasurer(request: Request) -> bool:
    """Recording money and the settings: whoever holds Treasurer, and admins in admin mode. Other officers only look."""
    m = archive.club.members.get(request.state.member or "")
    return bool(m and m.role and "treasurer" in m.role.lower()) or admin_mode(request)


def treasurer_only(request: Request) -> str:
    if not is_treasurer(request):
        raise HTTPException(403, "Only the Treasurer can do that.")
    return request.state.member


@app.get("/treasurer", response_class=HTMLResponse)
def treasurer_page(request: Request, tab: str = "standing", error: str = "", everyone: int = 0, show: str = "waiting",
                   mid: str = Depends(me)):
    """Officers see who owes what; the Treasurer records payments, expenses and bills, and runs the settings. A fund's
    keepers (the drinks committee, say) see and record only their fund's account."""
    club, s = archive.club, treasurer.load()
    c, kept, full = treasurer.config(s), treasurer.kept_accounts(club, s, mid), privileged(request)
    if not (full or kept):
        raise HTTPException(403, "Only officers can do that.")
    tab = tab if full else "money"
    if tab not in ("standing", "money", "bills", "settings"):
        raise HTTPException(404)
    members = sorted((m for m in club.members.values() if m.status.lower() not in ("inactive", "silent key")),
                     key=lambda m: (m.last, m.first))
    edit, running = is_treasurer(request), treasurer.on(s)
    entries = [e for e in s["entries"] if full or e["account"] in kept]
    standings = treasurer.standings(club, s) if full and running else []
    return page(request, "treasurer.html", tab=tab, everyone=everyone, bond_note=treasurer.BOND_NOTE,
                owing=sum(1 for x in standings if x["owed"] > 0 or x["certificate_late"]), error=error, cfg=c, edit=edit, full=full, kept=kept, members=members, running=running,
                standings=standings,
                entries=sorted(entries, key=lambda e: (e["date"], e["at"]), reverse=True),
                bills=treasurer.bills(archive, s, treasurer.bills_from(s)) if full and running else [], bills_from=treasurer.bills_from(s),
                show=show if show in ("waiting", "paid", "dismissed", "all") else "waiting", regular=treasurer.regular_due(s, dt.date.today()),
                regular_all=s["regular"], every=treasurer.EVERY, cats=treasurer.categories(s), guess=lambda t: treasurer.guess(t, s), modes=treasurer.MODES, rates=treasurer.RATES, bond=treasurer.BOND,
                labels=treasurer.LABELS, kinds=treasurer.KINDS if edit else treasurer.NO_MEMBER, accounts=c["accounts"] if edit else kept,
                rate=treasurer.rate, admins=' or '.join(sorted(club.name(a) for a in club.admins)),
                money=treasurer.money, today=dt.date.today().isoformat(), fmt=lambda d: dt.date.fromisoformat(d).strftime("%a %b %-d, %Y"))


def money_change(fn, tab: str):
    with treasurer.locked():
        s = treasurer.load()
        try:
            fn(s)
        except ValueError as e:
            return RedirectResponse(f"/treasurer?tab={tab}&error=" + quote(str(e)), status_code=303)
        treasurer.save(s)
    return RedirectResponse(f"/treasurer?tab={tab}", status_code=303)


@app.post("/treasurer/record")
def treasurer_record(request: Request, kind: str = Form(...), amount: str = Form(...), date: str = Form(...), account: str = Form(...),
                     member: str = Form(""), note: str = Form(""), bill: str = Form(""), regular: str = Form(""),
                     category: str = Form(""), mid: str = Depends(me)):
    def run(s):
        if not is_treasurer(request) and not (kind in treasurer.NO_MEMBER and account in treasurer.kept_accounts(archive.club, s, mid)
                                              and not bill and not regular):
            raise HTTPException(403, "Only the Treasurer can do that.")
        treasurer.record(archive.club, s, mid, kind, amount, date, account, owner_id(member), note, bill or None, regular or None,
                         category or None)
    return money_change(run, "bills" if bill or regular else "money")


@app.post("/treasurer/entries/{eid}/delete")
def treasurer_delete(request: Request, eid: str, mid: str = Depends(me)):
    def run(s):
        e = next((x for x in s["entries"] if x["id"] == eid), None)
        if not (is_treasurer(request) or (e and e["account"] in treasurer.kept_accounts(archive.club, s, mid))):
            raise HTTPException(403, "Only the Treasurer can do that.")
        treasurer.delete(s, eid, mid)
    return money_change(run, "money")


@app.post("/treasurer/entries/{eid}/note")
def treasurer_note(eid: str, note: str = Form(""), mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.set_note(s, eid, note, mid), "money")


@app.post("/treasurer/entries/{eid}/category")
def treasurer_category(eid: str, category: str = Form(...), mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.set_category(s, eid, category, mid), "money")


def spending_visible(request: Request, s: dict) -> bool:
    """The club's spending (never anyone's dues): every member once tracking is on; officers during a preview."""
    return treasurer.members_see(s) or (treasurer.on(s) and privileged(request))


@app.get("/spending", response_class=HTMLResponse)
def spending_page(request: Request, mid: str = Depends(me)):
    s = treasurer.load()
    if not spending_visible(request, s):
        raise HTTPException(404)
    return page(request, "spending.html", sp=treasurer.spending(s, dt.date.today(), club=archive.club), colour=(c := treasurer.colours(s)),
                # in colour order, so the bands that touch in a bar are the neighbours the palette was checked for
                cats=dict(sorted(treasurer.categories(s).items(), key=lambda kv: c[kv[0]])), money=treasurer.money,
                preview=not treasurer.members_see(s), month=lambda k: dt.date.fromisoformat(k + "-01").strftime("%b %Y"))


@app.post("/treasurer/bills/dismiss")
def treasurer_dismiss(key: str = Form(...), undo: int = Form(0), mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.dismiss_bill(s, key, mid, bool(undo)), "bills")


@app.post("/treasurer/bills/from")
def treasurer_bills_from(date: str = Form(""), mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.set_bills_from(s, mid, date or None), "bills")


@app.post("/treasurer/regular")
def treasurer_regular(payee: str = Form(...), amount: str = Form(...), every: str = Form(...), month: int = Form(0),
                      mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.add_regular(s, mid, payee, amount, every, month or None), "bills")


@app.post("/treasurer/regular/{rid}/remove")
def treasurer_regular_remove(rid: str, mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.remove_regular(s, rid, mid), "bills")


@app.post("/treasurer/settings")
def treasurer_settings(mode: str = Form(...), start: str = Form(""), arrears_weeks: int = Form(4), certificate_years: int = Form(2),
                       alerts: int = Form(0), mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.set_general(s, mid, mode, start, arrears_weeks, certificate_years, bool(alerts)), "settings")


@app.post("/treasurer/settings/rate")
def treasurer_rate(kind: str = Form(...), start: str = Form(...), amount: str = Form(""), remove: int = Form(0),
                   mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.remove_rate(s, mid, kind, start) if remove else treasurer.add_rate(s, mid, kind, start, amount),
                        "settings")


@app.post("/treasurer/settings/account")
def treasurer_account(name: str = Form(...), remove: int = Form(0), mid: str = Depends(treasurer_only)):
    return money_change(lambda s: treasurer.remove_account(s, mid, name) if remove else treasurer.add_account(s, mid, name), "settings")


@app.post("/treasurer/settings/category")
def treasurer_category_settings(key: str = Form(""), label: str = Form(""), remove: int = Form(0), mid: str = Depends(treasurer_only)):
    def run(s):
        if remove:
            treasurer.remove_category(s, mid, key)
        elif key:
            treasurer.rename_category(s, mid, key, label)
        else:
            treasurer.add_category(s, mid, label)
    return money_change(run, "settings")


@app.post("/treasurer/settings/fund")
def treasurer_fund(name: str = Form(...), account: str = Form(""), keepers: str = Form(""), remove: int = Form(0),
                   mid: str = Depends(treasurer_only)):
    def run(s):
        if remove:
            return treasurer.remove_fund(s, mid, name)
        ids = [owner_id(k) for k in keepers.split(",") if k.strip()]
        if None in ids or not ids:
            raise ValueError("List the keepers by name or callsign, separated by commas.")
        treasurer.set_fund(s, mid, name, account, ids)
    return money_change(run, "settings")


def _form_date(v: str) -> None:
    try:
        dt.date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, "That isn't a date.")


@app.post("/chow/request")
def chow_request(request: Request, mine: str = Form(...), theirs: str = Form(...), mid: str = Depends(me)):
    return chow_change(request, lambda s: chow.request_swap(archive.club, s, mid, mine, theirs, base_url(request)))


@app.post("/chow/requests/{rid}/answer")
def chow_answer(request: Request, rid: str, approve: int = Form(0), mid: str = Depends(me)):
    return chow_change(request, lambda s: chow.decide_swap(archive.club, s, rid, mid, bool(approve), base_url(request)))


@app.post("/chow/assign")
def chow_assign(request: Request, date: str = Form(...), member: str = Form(""), mid: str = Depends(chow_leader)):
    _form_date(date)
    return chow_change(request, lambda s: chow.assign(archive.club, s, date, owner_id(member), mid, base_url(request)))


@app.post("/chow/none")
def chow_none(request: Request, date: str = Form(...), note: str = Form(""), mid: str = Depends(chow_leader)):
    _form_date(date)
    return chow_change(request, lambda s: chow.set_none(s, date, note, mid))


@app.post("/chow/swap")
def chow_swap(request: Request, a: str = Form(...), b: str = Form(...), mid: str = Depends(chow_leader)):
    return chow_change(request, lambda s: chow.swap(archive.club, s, a, b, mid, base_url(request)))


@app.post("/chow/record")
def chow_record(request: Request, date: str = Form(...), status: str = Form(...), covered_by: str = Form(""),
                note: str = Form(""), next: str = Form("/chow/history"), mid: str = Depends(leader)):
    def run(s):
        chow.record(archive.club, s, date, status, mid, owner_id(covered_by), note)
        return {"section": chow.minutes_section(archive.club, s, chow.history(), date)}  # for the console
    return chow_change(request, run, back=_safe_next(next))


@app.post("/chow/generate")
def chow_generate(request: Request, until: str = Form(...), next_member: str = Form(""), mid: str = Depends(chow_leader)):
    def run(s):
        if next_member:
            s["next"] = next_member
        if not chow.generate(archive.club, s, dt.date.fromisoformat(until), mid):
            raise ValueError("No open meeting nights to fill before that date.")
    return chow_change(request, run, back="/chow/rotation")


@app.post("/chow/order")
def chow_order(request: Request, member: str = Form(...), op: str = Form(...), mid: str = Depends(chow_leader)):
    """Rotation order: move a member up or down, add a waiting member at the bottom, or take one out."""
    def run(s):
        order = list(s["order"])
        if op == "add" and member in archive.club.members and member not in order:
            order.append(member)
        elif op == "remove" and member in order:
            order.remove(member)
            s["pending"].append(member)
        elif op in ("up", "down") and member in order:
            i = order.index(member)
            j = i - 1 if op == "up" else i + 1
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
        chow.set_order(s, order, mid)
    return chow_change(request, run, back="/chow/rotation")


@app.post("/chow/constraints")
async def chow_constraints(request: Request, mid: str = Depends(chow_leader)):
    form = await request.form()
    who = form.get("member", "")
    if who not in archive.club.members:
        raise HTTPException(404)
    weeks = [int(w) for w in form.getlist("not_weeks") if w.isdigit()]
    away = [[form.get("away_from", ""), form.get("away_to", "")]] if form.get("away_from") else []
    skip = int(form.get("skip") or 0) if str(form.get("skip") or "0").isdigit() else 0
    return await run_in_threadpool(chow_change, request, lambda s: chow.set_constraints(s, who, weeks, away, form.get("note", ""), mid, skip),
                                   "/chow/rotation")


# ---------------- admin mode ----------------
ADMIN_MINUTES = 60


def _admin_log(request: Request, **entry) -> None:
    entry = {"at": int(dt.datetime.now().timestamp()), "member": request.state.real_member,
             "ip": request.client.host if request.client else None, **entry}
    p = DATA / "admin-log.jsonl"
    with p.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    p.chmod(0o600)


def _admin(mid: str) -> str:
    if not archive.club.is_admin(mid):
        raise HTTPException(403, "You aren't listed as an admin.")
    return mid


def _admin_on(request: Request, mid: str, how: str) -> None:
    until = authn.set_admin(request.cookies.get(COOKIE), ADMIN_MINUTES)
    _admin_log(request, on=True, until=until, confirmed_by=how)
    _log.info("admin mode on for %s (confirmed by %s)", one_line(mid), one_line(how))


@app.post("/admin/mode")
def admin_mode_switch(request: Request, on: int = Form(0), next: str = Form("/"), mid: str = Depends(real_me)):
    """Admins turn admin mode off at once; turning it on first asks them to confirm it's really them."""
    _admin(mid)
    if on:
        return RedirectResponse("/admin/confirm?next=" + quote(_safe_next(next)), status_code=303)
    until = authn.set_admin(request.cookies.get(COOKIE), 0)
    _admin_log(request, on=False, until=until)
    _log.info("admin mode off for %s", one_line(mid))
    return RedirectResponse(_safe_next(next), status_code=303)


@app.get("/admin/confirm", response_class=HTMLResponse)
def admin_confirm(request: Request, next: str = "/", sent: int = 0, error: str = "", mid: str = Depends(real_me)):
    """Step-up check before admin mode: a passkey where we can, else a one-time link emailed to the admin."""
    _admin(mid)
    passkey = request.url.hostname == authn.rp_id and bool(authn.passkeys(mid))
    return page(request, "admin_confirm.html", next=_safe_next(next), passkey=passkey, sent=sent, error=error,
                email=archive.club.members[mid].email)


@app.post("/admin/confirm/options")
def admin_confirm_options(mid: str = Depends(real_me)):
    _admin(mid)
    cid, options = authn.step_up_options(mid)
    response = Response(options, media_type="application/json")
    response.set_cookie(CHALLENGE_COOKIE, cid, max_age=300, httponly=True, samesite="strict", secure=True)
    return response


@app.post("/admin/confirm/verify")
async def admin_confirm_verify(request: Request, mid: str = Depends(real_me)):
    _admin(mid)
    body = await _json(request)
    try:
        authn.authenticate(request.cookies.get(CHALLENGE_COOKIE), body["credential"], kind="step-up", member=mid)
    except Exception as e:
        raise HTTPException(400, str(e))
    _admin_on(request, mid, "passkey")
    return {"ok": True, "next": _safe_next(body.get("next", "/"))}


@app.post("/admin/confirm/email")
def admin_confirm_email(request: Request, next: str = Form("/"), mid: str = Depends(real_me)):
    _admin(mid)
    m = archive.club.members[mid]
    if not m.email:
        raise HTTPException(400, "There's no email address for you on the roster.")
    url = f"{base_url(request)}/admin/confirm/link?t={authn.step_up_link(mid)}&next={quote(_safe_next(next))}"
    if PREVIEW:  # no email leaves a preview, and its email log is behind admin mode: go straight through
        return RedirectResponse(url, status_code=303)
    # Only ever to the admin themself, so test mode (email.only_to) doesn't apply.
    authmod.send_email(m.email, "Turn on admin mode", f"Hi {m.first},\n\nSomeone signed in as you asked to turn on admin mode "
               f"in W8LBZ Minutes. If that was you, open this link in the same browser within 5 minutes:\n\n{url}\n\n"
               f"If it wasn't you, don't open it, and sign out your other sessions on your profile page.\n\n73,\nW8LBZ")
    _admin_log(request, step_up_email=True)
    return RedirectResponse("/admin/confirm?sent=1&next=" + quote(_safe_next(next)), status_code=303)


@app.get("/admin/confirm/link")
def admin_confirm_link(request: Request, t: str = "", next: str = "/", mid: str = Depends(real_me)):
    _admin(mid)
    if not authn.use_step_up_link(t, mid):
        return RedirectResponse("/admin/confirm?error=" + quote("That link has expired or was already used.")
                                + "&next=" + quote(_safe_next(next)), status_code=303)
    _admin_on(request, mid, "email")
    return RedirectResponse(_safe_next(next), status_code=303)


# ---------------- updating the app (admins, admin mode) ----------------

@app.get("/admin/updates", response_class=HTMLResponse)
def admin_updates(request: Request, error: str = "", note: str = "", mid: str = Depends(real_me)):
    _admin(mid)
    if not admin_mode(request):
        return RedirectResponse("/admin/confirm?next=/admin/updates", status_code=303)
    return page(request, "updates.html", running=updates.running(), rel=updates.latest(), st=updates.status(),
                newer=updates.available(), error=error, note=note,
                backup_ready=len(os.environ.get("W8LBZ_BACKUP_PASSPHRASE", "")) >= 12)


@app.post("/admin/updates/check")
def admin_updates_check(request: Request, mid: str = Depends(real_me)):
    _admin(mid)
    rel = updates.latest(force=True)
    msg = "Couldn't reach GitHub." if rel is None else (
        f"Version {rel['version']} is available." if updates.newer(rel["version"], updates.running()) else "You're up to date.")
    return RedirectResponse("/admin/updates?note=" + quote(msg), status_code=303)


@app.post("/admin/updates/run")
def admin_updates_run(request: Request, target: str = Form(""), notes_done: int = Form(0), backup_first: int = Form(0),
                      mid: str = Depends(real_me)):
    _admin(mid)
    if not admin_mode(request):
        raise HTTPException(403, "Turn on admin mode first.")
    if backup_first:  # a way back if the update goes wrong; a few seconds at our size
        st = backup.run(archive.club, base_url(request))
        if not st["ok"]:
            return RedirectResponse("/admin/updates?error=" + quote(f"The backup before the update failed, so the update "
                                                                   f"didn't start: {st['error']}"), status_code=303)
        _admin_log(request, backup="before the update")
    try:
        updates.request(mid, target, bool(notes_done))
    except ValueError as e:
        return RedirectResponse("/admin/updates?error=" + quote(str(e)), status_code=303)
    _admin_log(request, update_requested=target)
    _log.info("update to %s requested by %s", one_line(target), one_line(mid))
    return RedirectResponse("/admin/updates?note=" + quote("Update started. The app restarts in a minute or two; "
                                                           "this page shows the result when it's done."), status_code=303)


@app.post("/admin/updates/cancel")
def admin_updates_cancel(request: Request, mid: str = Depends(real_me)):
    _admin(mid)
    if not admin_mode(request):
        raise HTTPException(403, "Turn on admin mode first.")
    updates.cancel()
    _admin_log(request, update_cancelled=True)
    return RedirectResponse("/admin/updates?note=" + quote("Request withdrawn."), status_code=303)


@app.post("/admin/view-as")
def admin_view_as(request: Request, member: str = Form(""), next: str = Form("/"), mid: str = Depends(real_me)):
    """Start (member=id) or stop (member empty) viewing the app as another member. Admin mode only; logged."""
    if not authn.admin_until(request.cookies.get(COOKIE)):
        raise HTTPException(403, "Turn on admin mode first.")
    if member and (member not in archive.club.members or member == mid):
        raise HTTPException(404)
    authn.set_view_as(request.cookies.get(COOKIE), member or None)
    _admin_log(request, view_as=member or None)
    _log.info("%s %s", one_line(mid), one_line(f"viewing as {member}" if member else "stopped viewing as another member"))
    return RedirectResponse(_safe_next(next), status_code=303)


# ---------------- profile (own contact details and sharing) ----------------

def clean_address(text: str) -> str:
    """Keep the member's line breaks (the sheet stores addresses on several lines); tidy spaces and blank lines."""
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)[:200]


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, saved: int = 0, email_sent: str = "", error: str = "", photo: int = 0,
                 mid: str = Depends(me)):
    return page(request, "profile.html", m=archive.club.members[mid], shareable=SHAREABLE,
                saved=saved, email_sent=email_sent, error=error, photo=photo)


@app.get("/profile/email", response_class=HTMLResponse)
def email_prefs_page(request: Request, saved: int = 0, mid: str = Depends(me)):
    kinds = {k: v for k, v in prefs.KINDS.items() if k != "roster" or mid in archive.club.secretaries}
    return page(request, "email_prefs.html", p=prefs.get(mid), kinds=kinds, help=prefs.HELP, chow_days=prefs.CHOW_DAYS,
                m=archive.club.members[mid], saved=saved)


@app.post("/profile/email")
async def email_prefs_save(request: Request, mid: str = Depends(me)):
    form = await request.form()
    prefs.save(mid, {**{k: form.get(k) for k in prefs.KINDS}, "chow_days": form.getlist("chow_days")})
    return RedirectResponse("/profile/email?saved=1", status_code=303)


@app.get("/profile/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, mid: str = Depends(me)):
    return page(request, "settings.html", s=prefs.settings(mid), options=prefs.SETTINGS, saved=saved)


@app.post("/profile/settings")
async def settings_save(request: Request, mid: str = Depends(me)):
    form = await request.form()
    prefs.save_settings(mid, {k: form.get(k) for k in prefs.SETTINGS})
    return RedirectResponse("/profile/settings?saved=1", status_code=303)


@app.post("/profile/photo")
async def profile_photo(request: Request, mid: str = Depends(me)):
    """Upload (or replace) your own profile picture."""
    form = await request.form()
    f = form.get("photo")
    data = await f.read(limits.max_upload() + 1) if f is not None and hasattr(f, "read") else b""
    try:
        await run_in_threadpool(avatars.save, mid, data)
    except ValueError as e:
        return RedirectResponse("/profile?error=" + quote(str(e)), status_code=303)
    return RedirectResponse("/profile?photo=1", status_code=303)


@app.post("/profile/photo/remove")
def profile_photo_remove(mid: str = Depends(me)):
    avatars.remove(mid)
    return RedirectResponse("/profile", status_code=303)


@app.post("/members/{who}/photo/remove")
def member_photo_remove(request: Request, who: str, mid: str = Depends(officer)):
    """Officers (and admins in admin mode) can take down someone's picture."""
    if avatars.remove(who):
        _log.info("%s removed the profile picture of %s", one_line(mid), one_line(who))
    return RedirectResponse(f"/members/{who}", status_code=303)


@app.get("/avatars/{name}")
def avatar_file(name: str, mid: str = Depends(me)):
    """Pictures are for signed-in members only; the address changes with the picture, so cache it."""
    who = name.rsplit(".", 1)[0]
    f = avatars.path(who) if who in archive.club.members else None
    if not f or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="image/png" if f.suffix == ".png" else "image/jpeg",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


@app.post("/profile")
async def profile_save(request: Request, mid: str = Depends(me)):
    form = await request.form()
    m = archive.club.members[mid]
    address = clean_address(form.get("address", ""))
    if address == clean_address(m.address or ""):  # untouched: don't rewrite the sheet over spacing
        address = m.address or ""
    try:
        bm, bd = int(form.get("bmonth") or 0), int(form.get("bday") or 0)
        if bd and not bm:
            raise ValueError
        birthday = f"{bm:02d}-{bd:02d}" if bm and bd and dt.date(2000, bm, bd) else f"{bm:02d}" if bm and dt.date(2000, bm, 1) else ""
    except ValueError:
        return RedirectResponse("/profile?error=" + quote("That birthday isn't a real date. Pick a month, or a month and a day."), status_code=303)
    new = {"phone": form.get("phone", "").strip()[:40], "address": address, "birthday": birthday,
           "share": ", ".join(f for f in SHAREABLE if f in form.getlist("share"))}
    try:
        await run_in_threadpool(profile.change, archive.club, mid, new, by=mid, url=base_url(request))
    except SheetError as e:
        return RedirectResponse("/profile?error=" + quote(str(e)), status_code=303)
    email = form.get("email", "").strip()
    if email and email.lower() != (m.email or "").lower():
        err = await run_in_threadpool(authn.start_email_change, mid, email, base_url(request))
        if err:
            return RedirectResponse("/profile?saved=1&error=" + quote(err), status_code=303)
        return RedirectResponse("/profile?saved=1&email_sent=" + quote(email), status_code=303)
    return RedirectResponse("/profile?saved=1", status_code=303)


@app.get("/profile/email")
def profile_email_confirm(request: Request, t: str = "", mid: str = Depends(me)):
    got = authn.confirm_email_change(t)
    if not got or got[0] != mid:
        return RedirectResponse("/profile?error=" + quote("That confirmation link is invalid or expired."), status_code=303)
    try:
        profile.change(archive.club, mid, {"email": got[1]}, by=mid, url=base_url(request))
    except SheetError as e:
        return RedirectResponse("/profile?error=" + quote(str(e)), status_code=303)
    return RedirectResponse("/profile?saved=1", status_code=303)


def roster_keeper(request: Request) -> str:
    """Adding members: the offices in roster_keepers (Secretary and Treasurer by default), or admin mode."""
    if not (archive.club.keeps_roster(request.state.member) or admin_mode(request)):
        raise HTTPException(403, "Only the Secretary or Treasurer can add members.")
    return request.state.member


@app.get("/roster/add", response_class=HTMLResponse)
def roster_add_form(request: Request, guest: str = "", error: str = "", mid: str = Depends(roster_keeper)):
    ready = membership.ready_guests(archive.guests(), int(archive.club.info.get("invite_after_visits", 3)))
    g = next((x for x in ready if x["name"] == guest), None)
    first, last = membership.split_name(g["name"]) if g else ("", "")
    f = {"first": first, "last": last, "callsign": (g or {}).get("callsign") or ""}
    return page(request, "roster_add.html", ready=ready, guest=g, f=f, error=error, classes=membership.CLASSES,
                today=dt.date.today().isoformat(), invite_after=archive.club.info.get("invite_after_visits", 3))


@app.post("/roster/add", response_class=HTMLResponse)
async def roster_add(request: Request, mid: str = Depends(roster_keeper)):
    form = dict(await request.form())
    try:
        new = await run_in_threadpool(membership.add_member, archive.club, form, mid, url=base_url(request))
    except (ValueError, SheetError) as e:
        ready = membership.ready_guests(archive.guests(), int(archive.club.info.get("invite_after_visits", 3)))
        return page(request, "roster_add.html", ready=ready, guest=None, f=form, error=str(e),
                    classes=membership.CLASSES, today=dt.date.today().isoformat(),
                    invite_after=archive.club.info.get("invite_after_visits", 3))
    return RedirectResponse(f"/members/{new}?added=1", status_code=303)


# ---------------- offices: who holds each, who held them when, and changing a holder ----------------

def office_admin(request: Request) -> str:
    """Changing offices: admins, in admin mode."""
    if not admin_mode(request):
        raise HTTPException(403, "Only an admin, in admin mode, can change who holds an office.")
    return request.state.member


@app.get("/offices", response_class=HTMLResponse)
def offices_page(request: Request, error: str = "", note: str = "", mid: str = Depends(me)):
    club = archive.club
    rows = [{**o, "holders": offices.holders(club, o["title"]), "terms": offices.terms(club, o["title"])}
            for o in offices.offices(club)]
    members = sorted((m for m in club.members.values() if m.status.lower() not in ("inactive", "silent key")),
                     key=lambda m: (m.last, m.first))
    return page(request, "offices.html", rows=rows, members=members, error=error, note=note,
                can_change=admin_mode(request), today=dt.date.today().isoformat())


def _office_change(request: Request, fn, note: str):
    try:
        fn()
    except (ValueError, SheetError) as e:
        return RedirectResponse("/offices?error=" + quote(str(e)), status_code=303)
    return RedirectResponse("/offices?note=" + quote(note), status_code=303)


@app.post("/offices/change")
def offices_change(request: Request, title: str = Form(...), out: str = Form(""), new: str = Form(""),
                   date: str = Form(""), mid: str = Depends(office_admin)):
    """Hand an office over (after an election), fill a vacancy, or record a resignation."""
    def run():
        offices.change(archive.club, title, out or None, owner_id(new), date, mid, url=base_url(request))
        _admin_log(request, office=title, left=out or None, took=owner_id(new), date=date)
    return _office_change(request, run, f"{title} updated.")


@app.post("/offices/term")
def offices_term(request: Request, title: str = Form(...), member: str = Form(...), start: str = Form(""),
                 end: str = Form(""), mid: str = Depends(office_admin)):
    """A past term, from before the app kept the history."""
    def run():
        offices.record_term(archive.club, title, owner_id(member) or "", start, end, mid)
        _admin_log(request, office=title, past_term=owner_id(member), date=f"{start}..{end}")
    return _office_change(request, run, "Past term recorded.")


@app.get("/roster/changes", response_class=HTMLResponse)
def roster_changes(request: Request, error: str = "", mid: str = Depends(officer)):
    return page(request, "changes.html", changes=profile.history(), labels=profile.LABELS, error=error)


@app.post("/roster/changes/{cid}/undo")
def roster_change_undo(request: Request, cid: str, mid: str = Depends(officer)):
    try:
        profile.undo(archive.club, cid, by=mid, url=base_url(request))
    except (ValueError, SheetError) as e:
        return RedirectResponse("/roster/changes?error=" + quote(str(e)), status_code=303)
    return RedirectResponse("/roster/changes", status_code=303)


@app.post("/account/passkeys/{pid}/rename")
def rename_passkey(pid: str, label: str = Form(""), mid: str = Depends(me)):
    authn.rename_passkey(mid, pid, label)
    return RedirectResponse("/account", status_code=303)


@app.post("/account/passkeys/{pid}/remove")
def remove_passkey(pid: str, mid: str = Depends(me)):
    authn.remove_passkey(mid, pid)
    return RedirectResponse("/account", status_code=303)


@app.post("/account/sessions/end-all")
def end_all_sessions(mid: str = Depends(me)):
    authn.end_all_sessions(mid)
    return RedirectResponse("/login", status_code=303)


@app.post("/auth/passkey/register/options")
def passkey_register_options(request: Request, mid: str = Depends(me)):
    cid, options = authn.registration_options(mid)
    response = Response(options, media_type="application/json")
    response.set_cookie(CHALLENGE_COOKIE, cid, max_age=300, httponly=True, samesite="strict", secure=True)
    return response


@app.post("/auth/passkey/register/verify")
async def passkey_register_verify(request: Request, mid: str = Depends(me)):
    body = await _json(request)
    try:
        authn.register(mid, request.cookies.get(CHALLENGE_COOKIE), body["credential"], body.get("label", ""))
    except Exception as e:  # verification errors come from several exception types
        raise HTTPException(400, f"Couldn't add the passkey: {e}")
    return {"ok": True}


@app.post("/auth/passkey/login/options")
def passkey_login_options():
    cid, options = authn.authentication_options()
    response = Response(options, media_type="application/json")
    response.set_cookie(CHALLENGE_COOKIE, cid, max_age=300, httponly=True, samesite="strict", secure=True)
    return response


@app.post("/auth/passkey/login/verify")
async def passkey_login_verify(request: Request):
    body = await _json(request)
    try:
        member = authn.authenticate(request.cookies.get(CHALLENGE_COOKIE), body["credential"])
    except Exception as e:
        authn.event("passkey_failed", client_ip(request), detail=str(e)[:120])
        raise HTTPException(400, str(e))
    if not authn.is_active(member):
        raise HTTPException(403, "Your roster entry isn't active.")
    return start_session(request, JSONResponse({"ok": True, "next": _safe_next(body.get("next", "/"))}), member, "passkey")


# ---------------- archive ----------------

PER_OPTIONS = (10, 25, 50, 0)  # list page sizes; 0 = all


def paginate(items: list, p: str, per: str, default: int = 25) -> dict:
    """One page of a long list. p and per come from the query string as text (junk falls back to defaults)."""
    n = int(per) if per.isdigit() and int(per) in PER_OPTIONS else default
    size = n or max(len(items), 1)
    pages = max(1, -(-len(items) // size))
    p = min(max(int(p) if p.isdigit() else 1, 1), pages)
    rows = items[(p - 1) * size:p * size]
    return {"rows": rows, "p": p, "pages": pages, "per": n, "options": PER_OPTIONS, "total": len(items),
            "first": (p - 1) * size + 1 if rows else 0, "last": (p - 1) * size + len(rows)}


def years() -> list[int]:
    """Years with minutes, and always this year (the default period, even before its first meeting)."""
    return sorted({m.date.year for m in archive.meetings} | {dt.date.today().year}, reverse=True)


def by_period(period: str) -> tuple[list, str, str]:
    """Meetings for a period choice: this year (default), 6m, 12m, all, or a year. -> (meetings, period, label)."""
    today = dt.date.today()
    if period in ("6m", "12m"):
        cutoff = today - dt.timedelta(days=182 if period == "6m" else 365)
        label = "in the last 6 months" if period == "6m" else "in the last 12 months"
        return [m for m in archive.meetings if m.date > cutoff], period, label
    if period == "all":
        return list(archive.meetings), "all", "on record"
    year = int(period) if period.isdigit() else today.year
    return [m for m in archive.meetings if m.date.year == year], str(year), f"in {year}"


@app.get("/", response_class=HTMLResponse)
def home(request: Request, mid: str = Depends(me)):
    """What's next for this member: the next meeting, their chow night, their business items, the latest minutes."""
    if request.url.query:  # old bookmarks of the archive (/?period=...)
        return RedirectResponse(f"/minutes?{request.url.query}", status_code=301)
    club, today = archive.club, dt.date.today()
    nights = chow.meeting_nights(club, today, today + dt.timedelta(days=7))
    s = chow.load()
    upcoming = chow.upcoming(s, n=60)
    latest = next((m for m in archive.meetings if m.status != "draft"), None)  # newest first
    items = [business.view(club, x, today.isoformat()) for x in business.load()["items"]
             if x["status"] == "open" and x.get("owner") == mid]
    # the calendar's next two weeks, without what Home already shows (chow nights; the weekly meeting the app makes up)
    coming, _ = calview.entries(club, archive, mid, today, today + dt.timedelta(days=13))
    coming = [e for e in coming if e.kind != "chow"][:8]
    # Upcoming Events the minutes give without a date (all of them before events had dates) still show, as written
    undated = [i.text for i in (latest.items("Upcoming Events") if latest else []) if not i.when]
    return page(request, "home.html", dues=treasurer.standing(club, ts, mid) if treasurer.members_see(ts := treasurer.load()) else None, bond_note=treasurer.BOND_NOTE, money=treasurer.money, next_meeting=nights[0] if nights else None, chow_next=upcoming[0] if upcoming else None,
                my_chow=next(((d, x) for d, x in upcoming if x.get("member") == mid and x.get("status") != "none"), None),
                swaps=[r for r in s["swaps"] if r["status"] == "pending" and r["with"] == mid],
                items=sorted(items, key=lambda x: x["due"] or "9999"), latest=latest, coming=coming, undated=undated,
                today=today, teams=next((ln for ln in club.links if "teams" in (ln.get("url", "") + ln.get("label", "")).lower()), None),
                fmt=lambda d: dt.date.fromisoformat(d).strftime("%a %b %-d"))


@app.get("/minutes", response_class=HTMLResponse)
def index(request: Request, period: str = "", p: str = Query("1", alias="page"), per: str = ""):
    """Meetings newest first, a page at a time, for a period (this year by default)."""
    ms, period, label = by_period(period)
    return page(request, "index.html", pg=paginate(ms, p, per), years=years(), period=period, label=label)


@app.get("/meetings/{slug}", response_class=HTMLResponse)
def view(request: Request, slug: str):
    m = get_meeting(slug)
    has_original = bool(m.source) and (archive.root / slug / "original.pdf").exists()  # only minutes imported from before the app
    return page(request, "meeting.html", m=m, has_original=has_original,
                emailed=console.email_status(archive, slug))


def html_to_pdf(template: str, **ctx) -> bytes:
    try:
        from weasyprint import HTML
    except OSError as e:  # missing system libraries (pango)
        raise HTTPException(503, f"PDF rendering unavailable on this server: {e}")
    html = templates.get_template(template).render(club=archive.club, pdf=True, **ctx)
    return HTML(string=html, base_url=str(HERE)).write_pdf()


def render_pdf(m) -> tuple[str, bytes]:
    """(file name, PDF bytes) for submitted minutes."""
    return f"{m.date.day}-{m.date:%b}-{m.date:%y}-minutes.pdf".lower(), html_to_pdf("minutes_doc.html", m=m)


@app.get("/meetings/{slug}/minutes.pdf")
def pdf(request: Request, slug: str):
    m = get_meeting(slug)
    if m.status == "draft":
        raise HTTPException(409, "This meeting is still in progress; the PDF is available once the minutes are submitted.")
    fname, data = render_pdf(m)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{fname}"'})


@app.get("/meetings/{slug}/original.pdf")
def original(slug: str):
    get_meeting(slug)  # only a real meeting's folder: no "..", nothing else in the data folder
    p = archive.root / slug / "original.pdf"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/pdf")


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", kind: str = "all", p: str = Query("1", alias="page"), per: str = ""):
    """One search box; results by kind. All: the first few of each kind; a kind's tab: all of them, a page at a time."""
    minutes = archive.search(q, limit=1000) if q else []
    for r in minutes:
        r["date"] = archive.by_slug[r["slug"]].date
    found = {**_search_lists(q), "calendar": _search_calendar(q), "minutes": minutes}
    kind = kind if kind in SEARCH_KINDS else "all"
    return page(request, "search.html", q=q, kind=kind, found=found, kinds=SEARCH_KINDS,
                pg=paginate(found[kind], p, per) if kind != "all" else None)


SEARCH_KINDS = {"members": "Members", "guests": "Guests", "items": "Business", "nights": "Chow", "calendar": "Calendar", "minutes": "Minutes"}


def _has(q: str, *texts) -> bool:
    """Every word of q (or another spelling of a member's name, from the roster) is in one of the texts (any case)."""
    text, names = " ".join(t or "" for t in texts).lower(), spellings(archive.club)
    return all(any(v in text for v in names.get(w, {w})) for w in q.lower().split())


def _search_lists(q: str) -> dict:
    """Members (name, callsign), guests not yet members, business items (text, owner) and upcoming chow nights
    (who's bringing it) that match q. Only what the roster, Guests, Business and Chow pages already show every member."""
    if not q.split():
        return {"members": [], "guests": [], "items": [], "nights": []}
    club = archive.club
    gone = ("inactive", "silent key")
    members = sorted((m for m in club.members.values() if _has(q, m.name, m.short, m.callsign, *m.aliases)),
                     key=lambda m: (m.status.lower() in gone, m.last, m.first))
    guests = [g for g in archive.guests() if not g["member"] and _has(q, g["name"], g["callsign"])]
    items = [business.view(club, x, dt.date.today().isoformat()) for x in console.business_state(archive)["items"]]
    items = sorted((x for x in items if _has(q, x["text"], x["owner_name"])), key=lambda x: (x["status"] != "open", x["opened"]))
    nights = [(dt.date.fromisoformat(d), club.name(x["member"])) for d, x in chow.upcoming(chow.load(), n=60)
              if x.get("member") and x.get("status") != "none" and _has(q, club.name(x["member"]))]
    return {"members": members, "guests": guests, "items": items, "nights": nights}


def _search_calendar(q: str) -> list[dict]:
    """Calendar events whose title, place or notes have every word of q, each with its next date (or its last one)."""
    if not q.split():
        return []
    today, out = dt.date.today(), []
    for e in events.load():
        if _has(q, e["title"], e.get("where"), e.get("notes")):
            ahead = events.dates(e, today, today + dt.timedelta(days=400))
            out.append({"title": e["title"], "where": e.get("where", ""), "cal": events.CALS[e["cal"]],
                        "date": ahead[0] if ahead else dt.date.fromisoformat(e["start"][:10]), "repeat": events.repeat_label(e["start"], e.get("repeat"))})
    return sorted(out, key=lambda r: (r["date"] < today, abs((r["date"] - today).days)))


@app.get("/members/{mid}", response_class=HTMLResponse)
def member(request: Request, mid: str):
    if mid not in archive.club.members:
        raise HTTPException(404)
    m, viewer, club = archive.club.members[mid], request.state.member, archive.club
    values = {"email": (m.email, f"mailto:{m.email}"), "phone": (m.phone, f"tel:{m.phone}"),
              "address": (m.address, None), "birthday": (monthday(m.birthday), None)}
    # (field, value, link), with the value blank unless this viewer may see it (club.can_see)
    elevated = admin_mode(request)
    contact = [(f, v if v and club.can_see(viewer, mid, f, elevated) else None, href) for f, (v, href) in values.items()]
    return page(request, "member.html", member=m, h=archive.member_history(mid), total=len(archive.meetings),
                contact=contact, is_me=viewer == mid, is_officer=privileged(request), is_officer_or_admin=privileged(request),
                can_view_as=elevated and mid != request.state.real_member,
                can_edit_roster=archive.club.keeps_roster(viewer) or elevated)


STATUSES = ("Active", "Suspended", "Inactive", "Silent Key", "Prospect")


@app.get("/members/{who}/edit", response_class=HTMLResponse)
def member_edit_page(request: Request, who: str, error: str = "", saved: int = 0, mid: str = Depends(roster_keeper)):
    """Roster keepers (and admins in admin mode) edit what the roster sheet holds about a member."""
    if who not in archive.club.members:
        raise HTTPException(404)
    m = archive.club.members[who]
    return page(request, "member_edit.html", member=m, error=error, saved=saved, classes=membership.CLASSES,
                statuses=list(STATUSES) + ([m.status] if m.status not in STATUSES else []),
                events=membership.EVENTS, today=dt.date.today().isoformat())


@app.post("/members/{who}/edit")
async def member_edit(request: Request, who: str, mid: str = Depends(roster_keeper)):
    if who not in archive.club.members:
        raise HTTPException(404)
    m, f = archive.club.members[who], await request.form()
    back = f"/members/{quote(who, safe='')}/edit"
    first, last, email = " ".join(f.get("first", "").split()), " ".join(f.get("last", "").split()), f.get("email", "").strip()
    try:
        if not first or not last or " " in first:
            raise ValueError("First and last name are both needed; put a middle name with the last name.")
        if f.get("license_class") not in ("", *membership.CLASSES) or f.get("status") not in (*STATUSES, m.status):
            raise ValueError("Pick a license class and status from the lists.")
        if email and not valid_email(email):
            raise ValueError(f"{email} doesn't look like an email address.")
        # An address is a way to sign in (and an admin's, to confirm admin mode): only an admin changes an admin's.
        if email.lower() != (m.email or "").lower() and archive.club.is_admin(who) and not admin_mode(request):
            raise ValueError("Only an admin, in admin mode, can change an admin's email.")
        new = {"name": f"{first} {last}", "license_class": f.get("license_class", ""), "status": f.get("status", ""),
               "role": ", ".join(t.strip() for t in f.get("role", "").split(",") if t.strip()),
               "aliases": ", ".join(a.strip() for a in f.get("aliases", "").split(",") if a.strip()),
               "email": email, "phone": f.get("phone", "").strip()[:40], "address": clean_address(f.get("address", "")),
               **{k: "TRUE" if f.get(k) else "FALSE" for k in ("officer", "board", "minutes_editor", "calendar_editor")}}
        if new["address"] == clean_address(m.address or ""):  # untouched: keep the sheet's own spacing
            new["address"] = m.address or ""
        await run_in_threadpool(profile.change, archive.club, who, new, by=mid, url=base_url(request), allowed=KEEPER_FIELDS)
    except (ValueError, SheetError) as e:
        return RedirectResponse(_safe_next(back + "?error=" + quote(str(e))), status_code=303)
    return RedirectResponse(_safe_next(back + "?saved=1"), status_code=303)


@app.post("/members/{who}/callsign")
def member_callsign(request: Request, who: str, callsign: str = Form(""), date: str = Form(""),
                    mid: str = Depends(roster_keeper)):
    """A new callsign: the roster sheet, and the member's id in every record (callsign.py). Backed up first."""
    if who not in archive.club.members:
        raise HTTPException(404)
    try:
        with business.locked(), events.locked(), _chow_lock:  # nothing else writes those files meanwhile
            new = callsign_mod.change(archive.club, who, callsign, date, mid)
        archive.reload()
    except (ValueError, SheetError, backup.BackupError) as e:
        return RedirectResponse(_safe_next(f"/members/{quote(who, safe='')}/edit?error=" + quote(str(e)) + "#callsign"), status_code=303)
    if admin_mode(request):
        _admin_log(request, callsign_change=f"{who} to {new}")
    return RedirectResponse(_safe_next(f"/members/{quote(new, safe='')}/edit?saved=1#callsign"), status_code=303)


@app.post("/members/{who}/log")
def member_log_event(request: Request, who: str, event: str = Form(""), date: str = Form(""), note: str = Form(""),
                     mid: str = Depends(roster_keeper)):
    if who not in archive.club.members:
        raise HTTPException(404)
    try:
        membership.add_event(archive.club, who, event, date, note, mid)
    except (ValueError, SheetError) as e:
        return RedirectResponse(_safe_next(f"/members/{quote(who, safe='')}/edit?error=" + quote(str(e)) + "#log"), status_code=303)
    return RedirectResponse(_safe_next(f"/members/{quote(who, safe='')}/edit?saved=1#log"), status_code=303)


@app.get("/roster", response_class=HTMLResponse)
def roster(request: Request, status: str = "Active", p: str = Query("1", alias="page"), per: str = "", view: str = ""):
    """Everyone on the roster, as a list or as cards (remembered in a cookie); contact details are on member pages."""
    phone = "Mobi" in request.headers.get("user-agent", "")  # iPhone and Android phones say "Mobile"
    view = view if view in ("list", "cards") else request.cookies.get("roster_view", "cards" if phone else "list")
    if view not in ("list", "cards"):
        view = "list"
    def since(m):
        try:
            return dt.date.fromisoformat(m.member_since) if m.member_since else None
        except ValueError:
            return None
    gone = ("inactive", "silent key")
    ms = sorted(archive.club.members.values(), key=lambda m: (m.status.lower() in gone, m.last, m.first))
    statuses = sorted({m.status for m in ms}, key=lambda x: (x != "Active", x))
    shown = [m for m in ms if status == "all" or m.status == status]
    response = page(request, "roster.html", pg=paginate([(m, since(m)) for m in shown], p, per, 50),
                    active=sum(m.status.lower() not in gone for m in ms), statuses=statuses, status=status,
                    is_officer=privileged(request), view=view,
                    can_add=archive.club.keeps_roster(request.state.member) or admin_mode(request))
    response.set_cookie("roster_view", view, max_age=365 * 86400, samesite="lax", httponly=True)
    return response


@app.get("/bylaws", response_class=HTMLResponse)
def bylaws(request: Request):
    """The club's constitution and bylaws, from bylaws.md in the data directory (not in this public repo)."""
    import markdown
    p = DATA / "bylaws.md"
    html = Markup(markdown.markdown(p.read_text())) if p.exists() else None
    return page(request, "bylaws.html", html=html)


LINK_ICONS = (("teams.", "video"), ("zoom.", "video"), ("meet.", "video"), ("discord.", "chat"), ("mailto:", "mail"),
              ("tel:", "phone"), ("youtube.", "play"), ("youtu.be", "play"), ("github.", "code"), ("maps.", "map"),
              ("/maps", "map"), ("qrz.com", "antenna"), ("repeaterbook.", "antenna"), ("arrl.org", "radio"),
              ("facebook.", "users"), ("groups.io", "users"), ("amazon.", "cart"), ("forms.", "form"),
              ("/bylaws", "document"), ("docs.google.", "document"), (".pdf", "document"), ("calendar", "calendar"))


def link_icon(url: str) -> str:
    """Icon name for a club link, guessed from its address ("Auto" on the Links editor): a known site's icon,
    else a globe for a bare website address, else a chain link. A link can set `icon:` to any name in ICONS."""
    u = url.lower()
    return next((icon for key, icon in LINK_ICONS if key in u), "globe" if u.count("/") <= 3 else "link")


templates.env.filters["link_icon"] = link_icon


def link_host(url: str) -> str:
    host = re.sub(r"^(https?://(www\.)?|mailto:|tel:)", "", url).split("/")[0]
    return "on this site" if host == authn.rp_id else host


templates.env.filters["link_host"] = link_host


def _license(dist: str) -> str:
    md = metadata(dist)
    lic = md.get("License-Expression") or ""
    if not lic:
        lic = next((c.split(" :: ")[-1] for c in md.get_all("Classifier") or [] if c.startswith("License ::")), "")
    if not lic and len(md.get("License") or "") <= 40:
        lic = md.get("License") or ""
    return lic.replace(" Software License", "").replace(" License", "") or "see project"


def dependencies() -> list[dict]:
    """The app's direct dependencies, as installed: name, version, license and home page."""
    out = []
    for req in requires("w8lbz-minutes") or []:
        if "extra ==" in req:
            continue
        name = re.split(r"[\s\[<>=!~;]", req, maxsplit=1)[0]
        md = metadata(name)
        urls = dict(u.split(", ", 1) for u in md.get_all("Project-URL") or [] if ", " in u)
        home = md.get("Home-page") or urls.get("Homepage") or urls.get("Source") or next(iter(urls.values()), None)
        out.append({"name": md["Name"], "version": version(name), "license": _license(name), "url": home})
    return sorted(out, key=lambda d: d["name"].lower())


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    import markdown
    changes = (Path(__file__).resolve().parents[2] / "CHANGELOG.md")
    changelog = None
    if changes.exists():  # show the versions, without the file's own title and intro
        text = re.sub(r"## Unreleased\s*(?=## )", "", changes.read_text())  # hide an empty Unreleased section
        # one (version, notes) per release, newest first; the page folds all but the latest
        changelog = [(head.strip(), Markup(markdown.markdown(body)))
                     for head, _, body in (s.partition("\n") for s in text.split("\n## ")[1:])]
    return page(request, "about.html", deps=dependencies(), changelog=changelog, license=_license("w8lbz-minutes"))


@app.get("/admin/email", response_class=HTMLResponse)
def email_log(request: Request, mid: str = Depends(real_me)):
    """Admins: every email the app sent, or held back in test mode, newest first. On a preview, its outbox."""
    if not archive.club.is_admin(mid):
        raise HTTPException(404)
    if not admin_mode(request):  # bodies can hold members' contact details
        return RedirectResponse("/admin/confirm?next=/admin/email", status_code=303)
    p = DATA / ("preview-outbox.jsonl" if PREVIEW else "email-log.jsonl")
    mail = [json.loads(line) for line in p.read_text().splitlines() if line.strip()][::-1][:300] if p.exists() else []
    return page(request, "outbox.html", mail=mail, test_mode=archive.club.email_only_to() is not None)


# ---------------- security (admins, in admin mode) ----------------

def device(agent: str | None) -> str:
    """'Chrome on Windows' from a browser's user-agent string, for the Security page."""
    a = agent or ""
    os_ = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                               ("Mac OS", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux")) if k in a), "")
    br = next((n for k, n in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if k in a), "")
    return " on ".join(x for x in (br, os_) if x) or (a[:40] or "unknown")


def where(ip: str | None) -> str:
    """LAN, tailnet or internet, so an admin can tell at a glance."""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip or "")
    except ValueError:
        return ""
    return "this server" if a.is_loopback else "tailnet" if a in ipaddress.ip_network("100.64.0.0/10") else \
        "LAN" if a.is_private else "internet"


templates.env.filters["device"] = device
templates.env.filters["where"] = where


def _security_admin(request: Request) -> str:
    mid = request.state.real_member
    if not archive.club.is_admin(mid or ""):
        raise HTTPException(404)
    if not admin_mode(request):
        raise HTTPException(403, "Turn on admin mode first.")
    return mid


SECURITY_TABS = (("signins", "Sign-ins"), ("devices", "Signed-in devices"), ("failed", "Failed attempts"),
                 ("bans", "Banned addresses"), ("admin", "Admin actions"), ("backups", "Backups"), ("server", "Server"))


def admin_action(e: dict) -> str:
    """One line of the admin log in plain words ("Turned on admin mode until 9:22 AM (passkey)")."""
    clock, name = templates.env.filters["clock"], archive.club.name
    if "on" in e:
        return f"Turned on admin mode until {clock(e['until'])} ({e.get('confirmed_by')})" if e["on"] and e.get("until") else "Turned off admin mode"
    if "view_as" in e:
        return f"Viewed the site as {name(e['view_as'])}" if e["view_as"] else "Stopped viewing as another member"
    if "ban" in e:
        return f"Banned {e['ban']} " + (f"for {e['hours']} hours" if e.get("hours") else "until lifted")
    words = {"step_up_email": lambda v: "Asked for an admin sign-in email", "update_requested": lambda v: f"Started the update to {v}",
             "update_cancelled": lambda v: "Cancelled the update", "unban": lambda v: f"Lifted the ban on {v}",
             "signed_out": lambda v: f"Signed out a device of {name(v) if v else 'someone'}",
             "links": lambda v: f"Saved the club links ({len(v)})"}
    rest = {k: v for k, v in e.items() if k not in ("at", "member", "ip")}
    return "; ".join(words[k](v) if k in words else f"{k.replace('_', ' ')}: {v}" for k, v in rest.items())


templates.env.filters["admin_action"] = admin_action


@app.get("/admin/security", response_class=HTMLResponse)
@app.get("/admin/security/{tab}", response_class=HTMLResponse)
def security_page(request: Request, tab: str = "signins", member: str = "", error: str = "", note: str = "",
                  mid: str = Depends(real_me)):
    """Sign-ins, signed-in devices, failed attempts, banned addresses, admin actions and server health."""
    if not archive.club.is_admin(mid):
        raise HTTPException(404)
    if tab not in dict(SECURITY_TABS):  # before the redirect, so only a known tab goes into its address
        raise HTTPException(404)
    if not admin_mode(request):
        return RedirectResponse(f"/admin/confirm?next=/admin/security/{tab}", status_code=303)
    p = DATA / "admin-log.jsonl"
    admin_log = [json.loads(x) for x in p.read_text().splitlines() if x.strip()][::-1][:50] if p.exists() else []
    roster = DATA / "roster.json"
    events = authn.events(100)
    bs = backup.settings()
    bs["s3"] = {k: v for k, v in bs["s3"].items() if k != "secret_enc"} | {"secret": bool(bs["s3"].get("secret_enc"))}  # never sent back
    return page(request, "security.html", tab=tab, tabs=SECURITY_TABS, backup_status=backup.status(), bs=bs, error=error, note=note,
                local_backups=[(p.name, p.stat().st_size) for p in backup.local_copies()], env_s3=bool(os.environ.get("W8LBZ_S3_SECRET")),
                passphrase_set=len(os.environ.get("W8LBZ_BACKUP_PASSPHRASE", "")) >= 12, drive_key=KEY_FILE.exists(),
                data_size=backup.data_size() if tab == "backups" else 0, rec=backup.RECOMMENDED,
                suggestion=backup.suggest() if tab == "backups" else "",
                failed_today=sum(e["at"] > time.time() - 86400 for e in events), signins=authn.signins(100), events=events, bans=authn.bans(),
                sessions=authn.all_sessions(), who=member, admin_log=admin_log, event_names=authmod.EVENT_NAMES,
                my_ip=client_ip(request), roster_synced=int(roster.stat().st_mtime) if roster.exists() else None,
                test_mode=archive.club.email_only_to() is not None, calendar=gcal.status(), calendars=archive.club.info.get("calendars"), session_days=authn.session_days, ban_after=authmod.BAN_AFTER, ban_hours=authmod.BAN_HOURS,
                upload_mb=limits.upload_mb(), upload_range=(limits.MIN_MB, limits.MAX_MB))


@app.post("/admin/security/limits")
def security_limits(request: Request, upload_mb: int = Form(...)):
    _security_admin(request)
    try:
        limits.set_upload_mb(upload_mb)
    except ValueError as e:
        return RedirectResponse("/admin/security/server?error=" + quote(str(e)), status_code=303)
    _admin_log(request, upload_mb=upload_mb)
    return RedirectResponse("/admin/security/server?note=" + quote(f"Uploads up to {upload_mb} MB."), status_code=303)


@app.post("/admin/security/ban")
def security_ban(request: Request, ip: str = Form(...), hours: int = Form(0), reason: str = Form("")):
    mid = _security_admin(request)
    import ipaddress
    try:
        ip = str(ipaddress.ip_address(ip.strip()))
    except ValueError:
        raise HTTPException(400, "That isn't an IP address.")
    if ip == client_ip(request) or ipaddress.ip_address(ip).is_loopback:
        raise HTTPException(400, "That's the address you're using (or this server); banning it would lock you out.")
    authn.ban(ip, hours or None, reason or "banned by an admin", mid)
    _admin_log(request, ban=ip, hours=hours or None)
    return RedirectResponse("/admin/security/bans", status_code=303)


@app.post("/admin/security/backups/settings")
async def security_backup_settings(request: Request):
    _security_admin(request)
    form = dict(await request.form())
    try:
        s = await run_in_threadpool(backup.save_settings, form)
    except backup.BackupError as e:
        return RedirectResponse("/admin/security/backups?error=" + quote(str(e)), status_code=303)
    _admin_log(request, backup_settings=", ".join(k for k in ("s3", "drive") if s[k]["on"]) or "server only")
    return RedirectResponse("/admin/security/backups?note=" + quote("Saved. Back up now to try it."), status_code=303)


@app.post("/admin/security/backups/check")
def security_backup_check(request: Request, passphrase: str = Form("")):
    """Is this the server's passphrase, is it strong, has it leaked? The passphrase itself is never stored or logged."""
    _security_admin(request)
    ok, msg = backup.check(passphrase)
    _admin_log(request, backup="checked a passphrase")
    return RedirectResponse("/admin/security/backups?" + ("note=" if ok else "error=") + quote(msg) + "#passphrase", status_code=303)


@app.post("/admin/security/backups/download")
def security_backup_download_now(request: Request):
    """A fresh backup straight to the admin's computer: compressed and encrypted, like the others."""
    _security_admin(request)
    try:
        name, blob, _ = backup.make()
    except backup.BackupError as e:
        return RedirectResponse("/admin/security/backups?error=" + quote(str(e)), status_code=303)
    _admin_log(request, backup_downloaded=name)
    return Response(blob, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/admin/security/backup")
def security_backup_now(request: Request, background: BackgroundTasks):
    """Run tonight's backup now (in the background; the page shows the result when it's done)."""
    _security_admin(request)
    _admin_log(request, backup="started")
    background.add_task(backup.run, archive.club, base_url(request))
    return RedirectResponse("/admin/security/backups", status_code=303)


@app.get("/admin/security/backups/{name}")
def security_backup_download(request: Request, name: str):
    """A local copy, still encrypted: safe to keep on your own computer."""
    _security_admin(request)
    p = next((p for p in backup.local_copies() if p.name == name), None)
    if not p:
        raise HTTPException(404)
    _admin_log(request, backup_downloaded=name)
    return FileResponse(p, media_type="application/octet-stream", filename=name)


@app.post("/admin/security/unban")
def security_unban(request: Request, ip: str = Form(...)):
    _security_admin(request)
    authn.unban(ip)
    _admin_log(request, unban=ip)
    return RedirectResponse("/admin/security/bans", status_code=303)


@app.post("/admin/security/signout")
def security_signout(request: Request, session: list[int] = Form(...), member: str = Form("")):
    """Sign out one device, or every ticked one."""
    _security_admin(request)
    for sid in session:
        _admin_log(request, signed_out=authn.end_session_id(sid))
    return RedirectResponse("/admin/security/devices" + (f"?member={quote(member)}" if member else ""), status_code=303)


@app.get("/preview/outbox")
def preview_outbox():
    return RedirectResponse("/admin/email", status_code=303)


ICONS = ("link", "globe", "video", "chat", "mail", "phone", "document", "form", "book", "news", "calendar", "map",
         "users", "radio", "antenna", "play", "code", "cart", "money", "info", "star")  # all drawn in _icons.html
@functools.cache
def brands() -> dict[str, list[str]]:
    """Simple Icons brand logos (slug -> [title, svg path]), stored in the package (deploy/update-simple-icons.py)."""
    return json.loads((Path(__file__).parent / "simple_icons.json").read_text())


def brand_path(name: str) -> str:
    """The svg path of a brand icon ("si:homeassistant"), or "" if there's no such logo."""
    return brands().get(name[3:], ["", ""])[1] if name.startswith("si:") else ""


templates.env.globals["brand_path"] = brand_path
templates.env.globals["icon_label"] = lambda name: brands().get(name[3:], [name])[0] if name.startswith("si:") else name


def valid_icon(icon: str) -> bool:
    return icon in ICONS or bool(brand_path(icon))


@app.get("/links/icons")
def link_icons_search(request: Request, q: str = "", mid: str = Depends(me)):
    """Brand logos matching q (name or slug), best first, for the Links editor's icon search."""
    if not admin_mode(request):
        raise HTTPException(403)
    q = " ".join(q.lower().split())
    key = re.sub(r"[^a-z0-9]", "", q)
    if len(key) < 2:
        return []
    hits = [(slug, t, p) for slug, (t, p) in brands().items() if key in slug or q in t.lower()]
    hits.sort(key=lambda h: (h[1].lower() != q and h[0] != key, not h[0].startswith(key), len(h[0]), h[0]))
    return [{"name": f"si:{slug}", "title": t, "path": p} for slug, t, p in hits[:40]]


SHOW = {"both": (True, True), "page": (True, False), "minutes": (False, True), "hidden": (False, False)}


# ---------------- calendar ----------------

@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, view: str = "agenda", start: str = "", month: str = "", weeks: str = "8", mid: str = Depends(me)):
    """Agenda (from a day, 8 weeks or more) or month grid: club calendars, chow nights and birthdays."""
    return _calendar(request, view, start, month, mid, weeks)


@app.get("/calendar/public", response_class=HTMLResponse)
def calendar_public(request: Request, view: str = "agenda", start: str = "", month: str = "", weeks: str = "8"):
    """The calendars an admin made public, for anyone (no chow, no birthdays)."""
    if not calview.public_kinds(archive.club):
        raise HTTPException(404)
    return _calendar(request, view, start, month, None, weeks)


AGENDA_WEEKS = {8: "8 weeks", 13: "3 months", 26: "6 months", 52: "a year"}


def _calendar(request: Request, view: str, start: str, month: str, mid: str | None, weeks: str = "8") -> HTMLResponse:
    weeks = int(weeks) if weeks.isdigit() and int(weeks) in AGENDA_WEEKS else 8
    today = dt.date.today()
    try:
        first = dt.date.fromisoformat(month + "-01") if month else today.replace(day=1)
        start_day = dt.date.fromisoformat(start) if start else today
    except ValueError:
        raise HTTPException(404)
    if not (1900 <= first.year <= 2200 and 1900 <= start_day.year <= 2200):  # past the ends of what dates can hold
        raise HTTPException(404)
    ws = 0 if prefs.settings(mid)["week_start"] == "monday" else 6  # the weekday a week starts on (Monday is 0)
    if view == "month":
        lo = first - dt.timedelta(days=(first.weekday() - ws) % 7)
        last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        hi = last + dt.timedelta(days=(ws + 6 - last.weekday()) % 7)
    else:
        view, lo, hi = "agenda", start_day, start_day + dt.timedelta(days=7 * weeks - 1)
    public = mid is None
    items, note = calview.entries(archive.club, archive, mid, lo, hi, public=public)
    by_day: dict[dt.date, list] = {}
    for e in items:
        for d in e.days(lo, hi):
            by_day.setdefault(d, []).append(e)
    kinds = {k: v for k, v in calview.KINDS.items() if not public or k in calview.public_kinds(archive.club)}
    repeats = {e["id"]: events.repeat_label(e["start"], e["repeat"]) for e in events.load() if e.get("repeat")}
    return page(request, "calendar.html", view=view, lo=lo, hi=hi, first=first, today=today, by_day=by_day, note=note, public=public,
                items=items, repeats=repeats, weekdays=[dt.date(2024, 1, 1 + (ws + i) % 7).strftime("%a") for i in range(7)],
                kinds=kinds, base="/calendar/public" if public else "/calendar",
                can_edit=not public and can_edit_calendar_now(request), is_officer=not public and privileged(request),
                prev_month=(first - dt.timedelta(days=1)).strftime("%Y-%m"), next_month=(first + dt.timedelta(days=32)).strftime("%Y-%m"),
                timedelta=dt.timedelta, weeks=weeks, agenda_weeks=AGENDA_WEEKS)


def _feed_url(request: Request, path: str) -> str:
    """The feed's address on the app's HTTPS name, which calendar services (Google, Outlook) can reach."""
    return f"https://{authn.rp_id}{path}" if authn.rp_id and not PREVIEW else str(request.base_url).rstrip("/") + path


@app.get("/calendar/changes", response_class=HTMLResponse)
def calendar_changes(request: Request, p: str = Query("1", alias="page"), per: str = "", mid: str = Depends(officer)):
    """Who changed what on the club calendars (calendar-log.jsonl), newest first."""
    return page(request, "calendar_changes.html", pg=paginate(events.history(5000), p, per, 50), cals=events.CALS)


@app.get("/calendar/subscribe", response_class=HTMLResponse)
def calendar_subscribe(request: Request, mid: str = Depends(me)):
    kinds = calview.KINDS
    return page(request, "calendar_subscribe.html", private=_feed_url(request, f"/calendar/feed/{calview.feed_token(mid)}.ics"), kinds=kinds,
                public=_feed_url(request, "/calendar/public.ics") if calview.public_kinds(archive.club) else None,
                public_page=_feed_url(request, "/calendar/public") if calview.public_kinds(archive.club) else None)


@app.post("/calendar/subscribe/reset")
def calendar_subscribe_reset(mid: str = Depends(me)):
    calview.feed_token(mid, reset=True)
    return RedirectResponse("/calendar/subscribe", status_code=303)


def _ics(items: list, name: str) -> Response:
    return Response(calview.ics(items, name, str(archive.club.info.get("timezone", "America/New_York"))),
                    media_type="text/calendar; charset=utf-8", headers={"Cache-Control": "private, max-age=900"})


@app.get("/calendar/feed/{token}.ics")
def calendar_feed(token: str, show: str = ""):
    """A member's private feed: the club calendars, chow nights and shared birthdays, a month back and a year ahead.
    show=events,chow (any of calview.KINDS) narrows it; empty means everything."""
    mid = calview.member_for_feed(token)
    if not mid or not authn.is_active(mid):
        raise HTTPException(404)
    today = dt.date.today()
    items, _ = calview.entries(archive.club, archive, mid, today - dt.timedelta(days=31), today + dt.timedelta(days=365))
    if kinds := [k for k in show.split(",") if k in calview.KINDS]:
        items = [e for e in items if e.kind in kinds]
    name = " & ".join(calview.KINDS[k] for k in kinds) if 0 < len(kinds) < 3 else "calendar"
    return _ics(items, f"{archive.club.info.get('callsign', 'Club')} {name}")


@app.get("/calendar/public.ics")
def calendar_public_feed():
    if not calview.public_kinds(archive.club):
        raise HTTPException(404)
    today = dt.date.today()
    items, _ = calview.entries(archive.club, archive, None, today - dt.timedelta(days=31), today + dt.timedelta(days=365), public=True)
    return _ics(items, f"{archive.club.info.get('callsign', 'Club')} events")


def _club_tz() -> str:
    return str(archive.club.info.get("timezone", "America/New_York"))


def can_edit_calendar_now(request: Request) -> bool:
    return archive.club.can_edit_calendar(request.state.member) or admin_mode(request)


def _calendar_editor(request: Request) -> None:
    if not can_edit_calendar_now(request):
        raise HTTPException(403, "Only leaders, minutes editors and calendar editors can change the club calendars.")


def _event_form(request: Request, f: dict, id: str = "", d: str = "", error: str = "", repeats: bool = False, status: int = 200):
    r = page(request, "calendar_edit.html", f=f, id=id, d=d, cals=events.CALS, error=error, repeats=repeats, scopes=events.SCOPES,
             repeat_choices=_repeat_choices(f.get("date") or dt.date.today().isoformat()), zones=events.ZONES, club_tz=_club_tz())
    r.status_code = status
    return r


def _repeat_choices(day: str) -> list[tuple[str, str]]:
    """The Repeats menu, worded for the start day ("Monthly on the 2nd Tuesday")."""
    start = day[:10]
    lab = lambda freq, **k: events.repeat_label(start, {"freq": freq, "interval": 1, "last": False, "until": None, **k})  # noqa: E731
    return [("", "Doesn't repeat"), ("daily", lab("daily")), ("weekly", lab("weekly")), ("biweekly", lab("weekly", interval=2)),
            ("monthly_weekday", lab("monthly_weekday")), ("monthly_last", lab("monthly_weekday", last=True)),
            ("monthly_weeks", f"Monthly on some {dt.date.fromisoformat(start):%A}s (pick the weeks)"),
            ("monthly", lab("monthly")), ("yearly", lab("yearly")), ("yearly_weekday", lab("yearly_weekday"))]


@app.get("/calendar/edit", response_class=HTMLResponse)
def calendar_edit(request: Request, id: str = "", d: str = "", date: str = "", cal: str = "events", mid: str = Depends(me)):
    """Add an event (no id), or change one (on date d, for a repeating event)."""
    _calendar_editor(request)
    if not id:
        return _event_form(request, {"cal": cal if cal in events.CALS else "events", "date": date or dt.date.today().isoformat()})
    try:
        e = events.get(events.load(), id)
    except KeyError:
        raise HTTPException(404, "That event isn't on the calendar any more.")
    try:
        return _event_form(request, events.form_values(e, d or None), id, d, repeats=bool(e.get("repeat")))
    except ValueError:  # d isn't a date
        raise HTTPException(404)


@app.post("/calendar/save")
async def calendar_save(request: Request, mid: str = Depends(me)):
    _calendar_editor(request)
    f = dict((await request.form()).items())
    eid, d, cal, scope = f.get("id", ""), f.get("d", ""), f.get("cal", ""), f.get("scope") or "all"
    if cal not in events.CALS or scope not in events.SCOPES:
        raise HTTPException(404)
    try:
        v = events.from_form(f, _club_tz())
        with events.locked():
            evs = events.load()
            if eid:
                events.change(evs, eid, d or None, cal, v, scope)
                action = "changed" if not d else {"this": "changed one date of", "following": "changed this and following dates of",
                                                  "all": "changed every date of"}[scope]
            else:
                events.add(evs, cal, v, mid)
                action = "added"
            events.save(evs)
    except ValueError as x:
        return _event_form(request, f, eid, d, str(x), repeats=bool(f.get("was_repeating")), status=400)
    except KeyError:
        raise HTTPException(404, "That event isn't on the calendar any more.")
    events.log(mid, action, {"cal": cal, **v})
    return RedirectResponse(f"/calendar?start={v['start'][:10]}", status_code=303)


@app.post("/calendar/delete")
def calendar_delete(request: Request, id: str = Form(...), d: str = Form(""), scope: str = Form("all"), mid: str = Depends(me)):
    _calendar_editor(request)
    if scope not in events.SCOPES:
        raise HTTPException(404)
    with events.locked():
        evs = events.load()
        try:
            e = events.delete(evs, id, d or None, scope)
        except KeyError:
            raise HTTPException(404, "That event isn't on the calendar any more.")
        events.save(evs)
    events.log(mid, "deleted" if not d else {"this": "deleted one date of", "following": "deleted this and following dates of",
                                             "all": "deleted every date of"}[scope], e)
    return RedirectResponse("/calendar", status_code=303)


@app.get("/calendar/import", response_class=HTMLResponse)
def calendar_import_form(request: Request, mid: str = Depends(me)):
    _calendar_editor(request)
    return page(request, "calendar_import.html", cals=events.CALS, report=None,
                since=(dt.date.today() - dt.timedelta(days=365)).isoformat())


@app.post("/calendar/import", response_class=HTMLResponse)
async def calendar_import(request: Request, mid: str = Depends(me)):
    """Add the events of an .ics file (exported from Google, Outlook, Apple...) to one of the club calendars."""
    _calendar_editor(request)
    form = await request.form()
    cal, f = form.get("cal", ""), form.get("file")
    if cal not in events.CALS or f is None or not hasattr(f, "read"):
        raise HTTPException(400, "Pick a calendar and an .ics file.")
    raw = await f.read(limits.max_upload() + 1)
    try:
        if len(raw) > limits.max_upload():
            raise ValueError(f"That file is over {limits.upload_mb()} MB.")
        text = raw.decode("utf-8-sig")
        if "BEGIN:VCALENDAR" not in text[:2000].upper():
            raise ValueError("That isn't an .ics calendar file.")
        since = dt.date.fromisoformat(form.get("since") or "1900-01-01")
    except (UnicodeDecodeError, ValueError) as x:
        return page(request, "calendar_import.html", cals=events.CALS, report=None, error=str(x) if isinstance(x, ValueError) else
                    "That file isn't text; is it an .ics file?", since=form.get("since", ""))
    with events.locked():
        evs = events.load()
        report = events.import_ics(evs, text, cal, mid, str(archive.club.info.get("timezone", "America/New_York")), since)
        events.save(evs)
    events.log(mid, f"imported {len(report['added'])} new and {len(report['updated'])} updated events into", {"cal": cal, "title": f.filename})
    gcal.soon(lambda: archive.club, lambda: archive)
    return page(request, "calendar_import.html", cals=events.CALS, report=report, cal=cal, since=form.get("since", ""))


@app.get("/links", response_class=HTMLResponse)
def links(request: Request, edit: int = 0, error: str = ""):
    return page(request, "links.html", edit=bool(edit and admin_mode(request)), error=error, icons=ICONS, guesses=LINK_ICONS)


@app.post("/links")
async def links_save(request: Request, mid: str = Depends(me)):
    """Admins (in admin mode) choose the links, their icons, their order, and where each shows."""
    if not admin_mode(request):
        raise HTTPException(403, "Turn on admin mode to edit the club links.")
    form = await request.form()
    rows = zip(form.getlist("label"), form.getlist("url"), form.getlist("icon"), form.getlist("show"))
    out = []
    for label, url, icon, show in rows:
        label, url = label.strip(), url.strip()
        if not label and not url:
            continue
        if not label or not re.match(r"^(https?://\S+|mailto:\S+@\S+|tel:[+\d][\d ()-]*)$", url):
            return RedirectResponse("/links?edit=1&error=" + quote(f"Each link needs a name and an address starting with https://, mailto: or tel: ({label or url})."), status_code=303)
        page_, minutes_ = SHOW.get(show, (True, True))
        out.append({"label": label[:80], "url": url[:500], **({"icon": icon} if valid_icon(icon) else {}), "page": page_, "minutes": minutes_})
    archive.club.save_links(out[:40])
    _admin_log(request, links=[x["label"] for x in out])
    return RedirectResponse("/links", status_code=303)


@app.get("/stats", response_class=HTMLResponse)
def stats(request: Request, period: str = "", p: str = Query("1", alias="page"), per: str = ""):
    """Attendance for a period: this year (default), the last 6 or 12 months, all time, or one year."""
    ms, period, label = by_period(period)
    st = archive.stats(ms)
    return page(request, "stats.html", s=st, pg=paginate(st["rows"], p, per, 10),
                period=period, label=label, years=years())


@app.get("/guests", response_class=HTMLResponse)
def guests(request: Request):
    return page(request, "guests.html", guests=archive.guests())


@app.get("/api/roster")
def roster_api(request: Request):
    """Active members' callsign, name, email, officer tick and former callsigns, for the club's other tools (the Discord bot, in its
    own container on the same Proxmox host). Only from this server itself or the addresses in
    W8LBZ_ROSTER_API_ALLOW (comma-separated), never through the web proxy, with the token in W8LBZ_ROSTER_API_TOKEN
    (both in /etc/w8lbz-minutes/env). Off (404) without that token. Every call is logged in api-log.jsonl."""
    import hmac
    import os
    want = os.environ.get("W8LBZ_ROSTER_API_TOKEN", "")
    host = request.client.host if request.client else ""
    allowed = {"127.0.0.1", "::1"} | {a.strip() for a in os.environ.get("W8LBZ_ROSTER_API_ALLOW", "").split(",") if a.strip()}
    local = host in allowed and not any(h in request.headers for h in ("x-forwarded-for", "x-forwarded-proto"))
    got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    ok = bool(want) and local and hmac.compare_digest(got.encode(), want.encode())
    former: dict[str, list[str]] = {}  # earlier callsigns (kept as aliases), so the bot can follow a callsign change
    for old, mid in archive.club.former_ids.items():
        former.setdefault(mid, []).append(old.upper())
    rows = [{"callsign": m.callsign, "name": m.name, "email": m.email, "officer": m.officer, "former": sorted(former.get(m.id, []))}
            for m in sorted(archive.club.members.values(), key=lambda m: (m.last, m.first)) if m.status.lower() == "active"] if ok else []
    p = DATA / "api-log.jsonl"
    with p.open("a") as f:
        f.write(json.dumps({"at": int(time.time()), "path": "/api/roster", "ip": host, "local": local, "ok": ok,
                            "agent": request.headers.get("user-agent", "")[:120], "members": len(rows)}) + "\n")
    p.chmod(0o600)
    if not want:
        raise HTTPException(404)
    if not ok:
        raise HTTPException(403, "Not allowed.")
    return rows


@app.get("/api/meetings/{slug}/live", response_class=HTMLResponse)
def live_fragment(request: Request, slug: str):
    """The rendered minutes, for members following along (polled)."""
    return templates.get_template("_minutes.html").render(m=get_meeting(slug), pdf=False, club=archive.club,
                                                          SECTIONS=SECTIONS)


# ---------------- leader console (officers + minutes editors) ----------------

@app.get("/console", response_class=HTMLResponse)
def console_home(request: Request, mid: str = Depends(leader)):
    drafts = [m for m in archive.meetings if m.status == "draft"]
    leaders = sorted(archive.club.leaders, key=archive.club.name)
    return page(request, "console_start.html", drafts=drafts, leaders=leaders, today=dt.date.today())


@app.post("/console/start")
def console_start(date: str = Form(...), leader_id: str = Form("", alias="leader"), mid: str = Depends(leader)):
    m = console.start(archive, dt.date.fromisoformat(date), leader_id or mid)
    return RedirectResponse(f"/console/{m.slug}", status_code=303)


@app.get("/console/{slug}", response_class=HTMLResponse)
def console_page(request: Request, slug: str, mid: str = Depends(leader)):
    m = get_meeting(slug)
    if m.status == "published":
        return RedirectResponse(f"/meetings/{slug}", status_code=303)
    ctx = console.context(archive, m)
    return page(request, "console.html", m=m, data=json.dumps(ctx).replace("<", "\\u003c"),  # "</script>" in any text can't end the script
                leaders=sorted(archive.club.leaders, key=archive.club.name))


@app.put("/api/meetings/{slug}")
async def console_save(request: Request, slug: str, mid: str = Depends(leader)):
    get_meeting(slug)
    try:
        console.apply(archive, slug, await _json(request))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


def email_minutes(slug: str, url: str) -> None:
    console.email_minutes(archive, slug, lambda: render_pdf(archive.by_slug[slug]), url)


@app.post("/console/{slug}/submit")
def console_submit(request: Request, background: BackgroundTasks, slug: str, mid: str = Depends(leader)):
    get_meeting(slug)
    console.submit(archive, slug)
    background.add_task(email_minutes, slug, f"{base_url(request)}/meetings/{slug}")
    background.add_task(followups_after_submit, slug)
    return RedirectResponse(f"/meetings/{slug}", status_code=303)


@app.post("/console/{slug}/discard")
def console_discard(slug: str, mid: str = Depends(leader)):
    get_meeting(slug)
    try:
        console.discard(archive, slug)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return RedirectResponse("/console", status_code=303)
