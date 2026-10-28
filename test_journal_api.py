import os
import unittest
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-not-used")

from fastapi import HTTPException
from pydantic import ValidationError
from journal_api import CreateNoteRequest, create_note, read_note, require_user, search_notes

USER = {"id": "11111111-1111-4111-8111-111111111111", "token": "test-token", "url": "https://example.supabase.co", "anon_key": "public-anon"}


class FakeStore:
    notes = {}
    day = {"id": "22222222-2222-4222-8222-222222222222"}

    def __init__(self, user):
        self.user = user

    def by_client_id(self, client_id):
        return self.notes.get(client_id)

    def get_or_create_day(self, journal_date):
        return self.day

    def create(self, day, client_id, body, journal_date):
        note = {"id": "33333333-3333-4333-8333-333333333333", "body": body, "occurred_at": journal_date + "T12:00:00Z", "entry_time": "12:00", "sort_order": 1, "updated_at": "2026-01-01T00:00:00Z", "text_color": "default", "day_id": day["id"]}
        self.notes[client_id] = note
        return note

    def read(self, entry_id):
        for note in self.notes.values():
            if note["id"] == entry_id and self.user["id"] == USER["id"]:
                return note
        return None

    def search(self, keywords, date_from, date_to, limit, offset):
        hits = [note for note in self.notes.values() if keywords.lower() in note["body"].lower()]
        return hits[offset:offset + limit]


class JournalApiTests(unittest.TestCase):
    def setUp(self):
        FakeStore.notes = {}
        self.store = patch("journal_api.JournalStore", FakeStore)
        self.store.start()

    def tearDown(self):
        self.store.stop()

    def test_create_note_and_idempotency(self):
        body = CreateNoteRequest(title="Rome", content="Book tickets", date="2026-10-27", tags=["travel"])
        first = create_note(body, "chat-abc", USER)
        second = create_note(body, "chat-abc", USER)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertIn("#travel", first["note"]["body"])

    def test_read_and_search(self):
        create_note(CreateNoteRequest(content="Quiet street near the market", date="2026-10-27"), "read-search", USER)
        note_id = next(iter(FakeStore.notes.values()))["id"]
        self.assertTrue(read_note(note_id, USER)["ok"])
        result = search_notes("market", None, None, 25, 0, USER)
        self.assertEqual(len(result["items"]), 1)

    def test_unauthorized_and_invalid_input(self):
        with self.assertRaises(HTTPException) as unauthorized:
            require_user(None)
        self.assertEqual(unauthorized.exception.status_code, 401)
        with self.assertRaises(ValidationError):
            CreateNoteRequest(content="", date="not-a-date")

    def test_cross_user_note_is_not_readable(self):
        create_note(CreateNoteRequest(content="Private note", date="2026-10-27"), "private", USER)
        note_id = next(iter(FakeStore.notes.values()))["id"]
        with self.assertRaises(HTTPException) as missing:
            read_note(note_id, {**USER, "id": "44444444-4444-4444-8444-444444444444"})
        self.assertEqual(missing.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
