from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.http_util import CORS_HEADERS  # noqa: E402
from shared.service import public_api_base  # noqa: E402


def _agent_path() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.with_name("setup_check.py"),
        ROOT.parent / "agent" / "setup_check.py",
        ROOT / "agent" / "setup_check.py",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def handler(event, _context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    if method == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": {**CORS_HEADERS, "Content-Type": "text/plain"},
            "body": "",
        }
    path = _agent_path()
    text = path.read_text(encoding="utf-8")
    api = public_api_base()
    text = text.replace("__DEFAULT_API__", api)
    return {
        "statusCode": 200,
        "headers": {
            **CORS_HEADERS,
            "Content-Type": "text/x-python; charset=utf-8",
            "Content-Disposition": "inline; filename=setup_check.py",
        },
        "body": text,
    }
