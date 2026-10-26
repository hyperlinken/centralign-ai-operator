"""One-shot smoke call to confirm the LLM provider works end-to-end."""
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiops.llm import build_llm, LLMMessage

llm = build_llm()
print("provider:", llm.name, "model:", llm.model)
out = llm.chat_json(
    "Return a JSON object with a key named 'result' whose value is the integer 42.",
    [LLMMessage("user", "go")],
    schema_hint='{"result": <integer>}',
)
print("response:", out)
assert out.get("result") == 42, out
print("OK")
