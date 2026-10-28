# Travel Journal AI backend

Deploy this folder as a Render Blueprint or Web Service. In Render, set `OPENAI_API_KEY` as a secret environment variable. Do not add it to this folder or to the Android APK.

The service exposes `POST /summarize-day` and `POST /translate-entries`. It also supports an optional `X-Journal-Token` header set from `JOURNAL_CLIENT_TOKEN`.

## External Journal API (not deployed yet)

The API routes are `POST /v1/notes`, `GET /v1/notes/{id}`, and `GET /v1/notes?q=keyword`.
Set `SUPABASE_URL` and `SUPABASE_ANON_KEY` as deployment environment variables, plus an optional comma-separated `API_ALLOWED_ORIGINS` list for browser clients. Every request must use `Authorization: Bearer <Supabase user access token>`; the API writes through that user token so existing Supabase Row Level Security remains active. `POST /v1/notes` also requires an `Idempotency-Key` request header.

Run local API tests with `python -m unittest -v test_journal_api.py`. Do not set a Supabase service-role key for this API.

Example create request (placeholder token only):

```bash
curl -X POST https://YOUR-SERVICE.example.com/v1/notes \
  -H "Authorization: Bearer USER_SUPABASE_ACCESS_TOKEN" \
  -H "Idempotency-Key: chatgpt-conversation-123-note-1" \
  -H "Content-Type: application/json" \
  -d '{"title":"Rome planning","content":"Book museum tickets.","date":"2026-10-27","tags":["Rome","planning"]}'
```

The response contains `ok`, `duplicate`, and the existing journal entry's `id`, `body`, and timestamps. Read one entry with `GET /v1/notes/{id}` and search with `GET /v1/notes?q=museum&date_from=2026-10-01&date_to=2026-10-31`.

### Deployment checklist

1. Create/update the Render service from this folder, but do not expose it publicly without authentication.
2. Set `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and a restricted `API_ALLOWED_ORIGINS` value in Render's secret environment settings. The anon key is used together with the caller's user access token; do **not** set a service-role key.
3. Keep the existing `OPENAI_API_KEY` and `JOURNAL_CLIENT_TOKEN` settings unchanged if the existing AI routes are used.
4. Confirm the service HTTPS URL, then test with a newly created non-production note and a regular Supabase user access token.
5. For a ChatGPT/MCP connection, implement OAuth authorization that obtains a short-lived Supabase user token per user; never ask ChatGPT to hold a Supabase password or a long-lived personal token.
