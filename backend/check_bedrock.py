"""Verify Amazon Bedrock from this machine. Run from the repo root:

    python backend/check_bedrock.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.pop("BEDROCK_DISABLED", None)
os.environ["BEDROCK_MODEL_ID"] = "amazon.nova-lite-v1:0"
os.environ.setdefault("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")

from shared import bedrock  # noqa: E402

print("region ", os.environ.get("AWS_REGION"))
print("model  ", bedrock.model_id())
text = bedrock.invoke_text(
    "Return ONLY JSON.",
    '{"ok": true, "service": "setup-readiness"}',
)
if text:
    print("ok     Bedrock responded:")
    print(text[:500])
    sys.exit(0)
print("FAIL   no response")
print("error ", bedrock.last_error() or "model access not enabled, wrong region, or missing AWS credentials")
sys.exit(1)
