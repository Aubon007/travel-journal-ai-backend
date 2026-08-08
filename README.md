# Travel Journal AI backend

Deploy this folder as a Render Blueprint or Web Service. In Render, set `OPENAI_API_KEY` as a secret environment variable. Do not add it to this folder or to the Android APK.

The service exposes `POST /summarize-day` and `POST /translate-entries`. It also supports an optional `X-Journal-Token` header set from `JOURNAL_CLIENT_TOKEN`.
