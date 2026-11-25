"""A tiny fictional plugin for tests/test_plugins.py."""

from fastapi import APIRouter, HTTPException, Request

API = 1
NAME = "hello"
TITLE = "Hello board"
DESCRIPTION = "Says hello to Jane Doe."
PUBLIC = {"/open", "/door/{room}"}
PERMISSIONS = {"edit": "edit the hello board"}
NAV = [("Hello", "/", None), ("Hello editor", "/gated", "edit")]
GUIDE = "guide.md"
api = None  # the app puts the plugin's api here; handlers read it when a request comes
router = APIRouter()


@router.get("/")
def home(request: Request):
    return api.page(request, "hello.html", who=api.who(request))


@router.get("/open")
def open_page():
    return {"open": True}


@router.get("/door/{room}")
def door(room: str):
    return {"door": room}


@router.get("/gated")
def gated(request: Request):
    if not api.may(api.who(request), "edit"):
        raise HTTPException(403, "no")
    return {"edit": True}


@router.get("/admin")
def admin():
    return {"settings": True}


@router.get("/note/{text}")
def note(text: str):
    api.storage.save({"note": text})
    return api.storage.load()


@router.get("/admin/page")
def admin_page(request: Request):
    return api.page(request, "hello.html", who="the admin")


@router.get("/whoami")
def whoami(request: Request):
    return {"librarian": api.allowed(request, "edit"), "members": api.members(), "today": str(api.today())}


def calendar_links(api):
    return [("Hello room", "https://example.org/hello"), ("Hello page", "/")]  # relative to /hello, like NAV: the app adds /hello and its own address


def form_action(api):
    return ["https://forms.example.org:8443", "http://plain.example.org", "https://bad.example.org; script-src *", 5]


@router.get("/roles")
def roles(request: Request):
    return {"roles": api.roles(api.who(request)), "labels": api.role_labels(), "base": api.base_url(request)}


@router.post("/admin/secret")
def new_secret(request: Request):
    if again := api.fresh(request, "/hello/admin"):
        return again
    return {"secret": "new"}


READY = [True]  # tests flip it


def nav_ready(api):
    return READY[0]


def console_attendance(api):
    if id := api.storage.load().get("mark"):  # marks that member online, as a video plugin would
        api.mark_present(id)
    return ["Pat Guest"]
