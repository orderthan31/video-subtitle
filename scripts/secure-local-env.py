"""Move a locally entered example credential without printing its value."""
from pathlib import Path
from dotenv import dotenv_values, set_key

root = Path(__file__).resolve().parents[1]
example = root / ".env.example"
local = root / ".env"
values = dotenv_values(example)
key = values.get("GEMINI_API_KEY")
if key:
    if not local.exists():
        local.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    set_key(str(local), "GEMINI_API_KEY", key)
    set_key(str(example), "GEMINI_API_KEY", "")
for path in (example, local):
    set_key(str(path), "GEMINI_TRANSCRIPTION_MODEL", "gemini-3.5-transcribe")
    set_key(str(path), "GEMINI_TRANSLATION_MODEL", "gemini-3.8-flash")
print("Local credential configured:", bool(dotenv_values(local).get("GEMINI_API_KEY")))
print("Example credential empty:", not bool(dotenv_values(example).get("GEMINI_API_KEY")))
