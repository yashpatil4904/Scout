from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.http_util import parse_body, path_param, response  # noqa: E402
from shared.models import to_dict  # noqa: E402
from shared.service import ingest_results  # noqa: E402


def handler(event, _context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    if method == "OPTIONS":
        return response(200, {})
    session_id = path_param(event, "id") or path_param(event, "sessionId")
    if not session_id:
        return response(400, {"error": "session id is required"})
    payload = parse_body(event)
    try:
        session = ingest_results(session_id, payload)
    except KeyError:
        return response(404, {"error": "session not found"})
    return response(200, to_dict(session))
