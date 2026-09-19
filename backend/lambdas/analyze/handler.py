from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.http_util import parse_body, response  # noqa: E402
from shared.models import to_dict  # noqa: E402
from shared.service import create_session  # noqa: E402


def handler(event, _context):
    if (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method")) == "OPTIONS":
        return response(200, {})
    body = parse_body(event)
    repo_url = (body.get("repoUrl") or body.get("repo_url") or "").strip()
    if not repo_url:
        return response(400, {"error": "repoUrl is required"})
    session = create_session(repo_url)
    status = 201 if session.status != "error" else 502
    return response(status, to_dict(session))


if __name__ == "__main__":
    print(json.dumps(handler({"body": json.dumps({"repoUrl": sys.argv[1]})}, None)))
