"""Verify the LLM used for scoring (Groq / OpenAI / Gemini / Ollama).

    $env:GROQ_API_KEY="gsk_..."
    python backend/check_llm.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["BEDROCK_DISABLED"] = "1"

from shared import bedrock  # noqa: E402

print("status", bedrock.llm_status())
text = bedrock.invoke_text("Return ONLY JSON.", '{"ok": true}')
if text:
    print("ok    LLM responded:")
    print(text[:500])
    sys.exit(0)
print("FAIL  no response")
print("error", bedrock.last_error() or "Set GROQ_API_KEY (https://console.groq.com/keys)")
sys.exit(1)
