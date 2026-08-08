import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field

app = FastAPI(title="Travel Journal AI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST", "GET"], allow_headers=["*"])

class JournalRequest(BaseModel):
    entries: list[str] = Field(min_length=1, max_length=200)
    language: str = "English"

def check_token(token: str | None):
    expected = os.environ.get("JOURNAL_CLIENT_TOKEN")
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/summarize-day")
def summarize_day(payload: JournalRequest, x_journal_token: str | None = Header(default=None)):
    check_token(x_journal_token)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    entries = "\n".join(f"- {entry}" for entry in payload.entries)
    instructions = f"""You are helping create an editable travel-journal reflection in {payload.language}.
Use only the supplied entries. Do not invent facts, dates, places, tasks, or feelings.
Preserve the user's meaning and voice. Keep original entries unchanged; return only a new reflection.
Do not mix ideas or work thoughts into the travel narrative.
Use these headings only when relevant:
Today’s Journal
Brainstorm & Ideas
Reminders & To-Dos
Loose Notes

Entries:
{entries}"""
    response = client.responses.create(model="gpt-5-mini", input=instructions)
    return {"reflection": response.output_text}

@app.post("/translate-entries")
def translate_entries(payload: JournalRequest, x_journal_token: str | None = Header(default=None)):
    check_token(x_journal_token)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    entries = "\n".join(f"- {entry}" for entry in payload.entries)
    response = client.responses.create(
        model="gpt-5-mini",
        input=f"Translate every journal entry below into {payload.language}. Preserve meaning, tone, dates, names, and entry boundaries. Return one translated entry per line.\n\n{entries}",
    )
    return {"translation": response.output_text}
