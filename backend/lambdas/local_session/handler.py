from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.http_util import parse_body, response  # noqa: E402
from shared.models import to_dict  # noqa: E402
from shared.service import api_base_from_event, create_local_session  # noqa: E402


def handler(event, _context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")
    if method == "OPTIONS":
        return response(200, {})
    body = parse_body(event)
    local_path = (body.get("local_path") or body.get("localPath") or "").strip()
    if not local_path:
        return response(400, {"error": "local_path is required"})
    try:
        session = create_local_session(
            local_path,
            files=body.get("files") or {},
            tree_paths=body.get("tree_paths") or body.get("treePaths"),
            api_base=api_base_from_event(event),
            agent_code=(body.get("agent_code") or body.get("agentCode") or "").strip() or None,
        )
    except ValueError as exc:
        return response(400, {"error": str(exc)})
    status = 201 if session.status != "error" else 502
    return response(status, to_dict(session))
