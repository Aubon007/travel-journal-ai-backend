"""Authenticated external API for the existing Travel Journal tables.

This module deliberately uses the caller's Supabase access token for every data
operation.  Supabase Row Level Security therefore remains the authorization
boundary; no service-role key is used or required.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1", tags=["journal-notes"])
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,35}$", re.I)
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS: dict[str, list[float]] = {}
_RATE_LIMIT = 60
_RATE_WINDOW_SECONDS = 60


class CreateNoteRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    date: date
    tags: list[str] = Field(default_factory=list, max_length=12)


def _configuration() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not anon_key:
        raise HTTPException(status_code=503, detail="Journal API is not configured.")
    return url, anon_key


def _request_json(method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return (json.loads(raw) if raw else None), dict(response.headers.items())
    except urllib.error.HTTPError as error:
        # Do not return upstream bodies: they can contain implementation details.
        if error.code in (401, 403):
            raise HTTPException(status_code=401, detail="Unauthorized.")
        raise HTTPException(status_code=502, detail="Journal data service is unavailable.")
    except (urllib.error.URLError, TimeoutError):
        raise HTTPException(status_code=503, detail="Journal data service is unavailable.")


def require_user(authorization: str | None = Header(default=None)) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer authentication is required.")
    token = authorization[7:].strip()
    if len(token) < 20:
        raise HTTPException(status_code=401, detail="Bearer authentication is required.")
    url, anon_key = _configuration()
    user, _ = _request_json("GET", f"{url}/auth/v1/user", {"apikey": anon_key, "Authorization": f"Bearer {token}"})
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    _check_rate_limit(str(user["id"]))
    return {"id": str(user["id"]), "token": token, "url": url, "anon_key": anon_key}


def _check_rate_limit(user_id: str) -> None:
    now = time.monotonic()
    with _RATE_LOCK:
        events = [stamp for stamp in _RATE_BUCKETS.get(user_id, []) if now - stamp < _RATE_WINDOW_SECONDS]
        if len(events) >= _RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many requests. Try again shortly.")
        events.append(now)
        _RATE_BUCKETS[user_id] = events


def _safe_tag(tag: str) -> str:
    value = " ".join(tag.strip().split())
    if not value or len(value) > 50 or not re.fullmatch(r"[\w.-]+", value, flags=re.UNICODE):
        raise HTTPException(status_code=422, detail="Each tag must contain only letters, numbers, dots, hyphens, or underscores.")
    return value


def compose_body(title: str, content: str, tags: list[str]) -> str:
    """Map title/tags into the existing body field without a schema change."""
    title = title.strip()
    content = content.strip()
    checked_tags = [_safe_tag(tag) for tag in tags]
    parts = [part for part in (title, " ".join(f"#{tag}" for tag in checked_tags), content) if part]
    return "\n\n".join(parts)


class JournalStore:
    def __init__(self, user: dict[str, str]):
        self.user = user
        self.base = user["url"] + "/rest/v1/"
        self.headers = {
            "apikey": user["anon_key"],
            "Authorization": "Bearer " + user["token"],
            "Content-Type": "application/json",
        }

    def _get(self, table: str, query: dict[str, str]) -> list[dict[str, Any]]:
        endpoint = self.base + table + "?" + urllib.parse.urlencode(query, safe="(),.*")
        result, _ = _request_json("GET", endpoint, self.headers)
        return result if isinstance(result, list) else []

    def _post(self, table: str, payload: dict[str, Any], query: str = "") -> list[dict[str, Any]]:
        headers = dict(self.headers)
        headers["Prefer"] = "resolution=ignore-duplicates,return=representation"
        result, _ = _request_json("POST", self.base + table + query, headers, payload)
        return result if isinstance(result, list) else []

    def get_or_create_day(self, journal_date: str) -> dict[str, Any]:
        found = self._get("journal_days", {"select": "*", "user_id": "eq." + self.user["id"], "journal_date": "eq." + journal_date, "deleted_at": "is.null"})
        if found:
            return found[0]
        created = self._post("journal_days", {"user_id": self.user["id"], "journal_date": journal_date}, "?on_conflict=user_id,journal_date")
        if created:
            return created[0]
        found = self._get("journal_days", {"select": "*", "user_id": "eq." + self.user["id"], "journal_date": "eq." + journal_date, "deleted_at": "is.null"})
        if not found:
            raise HTTPException(status_code=502, detail="Could not create the journal day.")
        return found[0]

    def by_client_id(self, client_id: str) -> dict[str, Any] | None:
        rows = self._get("journal_entries", {"select": "*", "user_id": "eq." + self.user["id"], "client_id": "eq." + client_id, "deleted_at": "is.null"})
        return rows[0] if rows else None

    def create(self, day: dict[str, Any], client_id: str, body: str, journal_date: str) -> dict[str, Any]:
        existing = self.by_client_id(client_id)
        if existing:
            return existing
        now = time.time()
        payload = {
            "user_id": self.user["id"], "day_id": day["id"], "client_id": client_id,
            "body": body, "occurred_at": journal_date + "T12:00:00Z",
            "sort_order": now, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        created = self._post("journal_entries", payload, "?on_conflict=user_id,client_id")
        if created:
            return created[0]
        existing = self.by_client_id(client_id)
        if existing:
            return existing
        raise HTTPException(status_code=502, detail="Could not create the journal entry.")

    def read(self, entry_id: str) -> dict[str, Any] | None:
        rows = self._get("journal_entries", {"select": "id,body,occurred_at,sort_order,updated_at,day_id", "id": "eq." + entry_id, "user_id": "eq." + self.user["id"], "deleted_at": "is.null"})
        return rows[0] if rows else None

    def search(self, keywords: str, date_from: str | None, date_to: str | None, limit: int, offset: int) -> list[dict[str, Any]]:
        escaped = keywords.replace("%", "\\%").replace("_", "\\_").replace("*", "\\*")
        query = {"select": "id,body,occurred_at,sort_order,updated_at,day_id", "user_id": "eq." + self.user["id"], "body": "ilike.*" + escaped + "*", "deleted_at": "is.null", "order": "occurred_at.desc", "limit": str(limit), "offset": str(offset)}
        bounds = []
        if date_from:
            bounds.append("occurred_at.gte." + date_from + "T00:00:00Z")
        if date_to:
            bounds.append("occurred_at.lte." + date_to + "T23:59:59Z")
        if bounds:
            query["and"] = "(" + ",".join(bounds) + ")"
        return self._get("journal_entries", query)


def _client_id(user_id: str, key: str) -> str:
    if not key or len(key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required and must be at most 200 characters.")
    return "external_" + hashlib.sha256((user_id + ":" + key).encode("utf-8")).hexdigest()


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: entry.get(key) for key in ("id", "body", "occurred_at", "sort_order", "updated_at", "day_id")}


@router.post("/notes", status_code=201)
def create_note(payload: CreateNoteRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: dict[str, str] = Depends(require_user)):
    store = JournalStore(user)
    client_id = _client_id(user["id"], idempotency_key or "")
    existing = store.by_client_id(client_id)
    if existing:
        return {"ok": True, "duplicate": True, "note": _public_entry(existing)}
    day = store.get_or_create_day(payload.date.isoformat())
    note = store.create(day, client_id, compose_body(payload.title, payload.content, payload.tags), payload.date.isoformat())
    return {"ok": True, "duplicate": False, "note": _public_entry(note)}


@router.get("/notes/{entry_id}")
def read_note(entry_id: str, user: dict[str, str] = Depends(require_user)):
    if not _UUID.fullmatch(entry_id):
        raise HTTPException(status_code=422, detail="Invalid note ID.")
    note = JournalStore(user).read(entry_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    return {"ok": True, "note": _public_entry(note)}


@router.get("/notes")
def search_notes(
    q: str = Query(min_length=1, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=25, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=10_000),
    user: dict[str, str] = Depends(require_user),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to.")
    notes = JournalStore(user).search(q.strip(), date_from.isoformat() if date_from else None, date_to.isoformat() if date_to else None, limit, offset)
    return {"ok": True, "items": [_public_entry(note) for note in notes], "limit": limit, "offset": offset, "next_offset": offset + len(notes) if len(notes) == limit else None}
