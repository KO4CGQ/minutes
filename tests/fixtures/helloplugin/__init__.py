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


def calendar_links(api):
    return [("Hello room", "https://example.org/hello")]


@router.post("/admin/secret")
def new_secret(request: Request):
    if again := api.fresh(request, "/hello/admin"):
        return again
    return {"secret": "new"}
