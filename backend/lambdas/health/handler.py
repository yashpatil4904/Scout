from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.bedrock import llm_status  # noqa: E402
from shared.http_util import response  # noqa: E402


def handler(event, _context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    if method == "OPTIONS":
        return response(200, {})
    status = llm_status()
    return response(
        200,
        {
            "service": "setup-readiness",
            "ok": True,
            "llm": status,
            "bedrock": {
                "enabled": status.get("provider") == "bedrock" and status.get("enabled"),
                "model": status.get("model") if status.get("provider") == "bedrock" else None,
            },
        },
    )
