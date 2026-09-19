from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.http_util import parse_body, path_param, response  # noqa: E402
from shared.models import to_dict  # noqa: E402
from shared.service import (  # noqa: E402
    agent_heartbeat,
    agent_status,
    api_base_from_event,
    create_local_intent,
    hydrate_local_session,
    list_pending_sessions,
    request_agent_stop,
)


def _qs(event: dict) -> dict:
    raw = event.get("rawQueryString") or ""
    if not raw and event.get("queryStringParameters"):
        return {k: [v] for k, v in (event.get("queryStringParameters") or {}).items() if v is not None}
    return parse_qs(raw)


def _bootstrap_cmd(api: str, return_url: str) -> str:
    # Double-clickable Windows launcher: opens Amplify with ?code= and starts agent.
    return f"""@echo off
setlocal EnableExtensions
title RepoReady — laptop agent
set "API={api}"
set "RETURN={return_url}"
echo.
echo === RepoReady — connecting this laptop ===
echo.
where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python is not on PATH. Install Python 3, then re-run this file.
  pause
  exit /b 1
)
for /f "usebackq delims=" %%i in (`python -c "import secrets; print(secrets.token_hex(4))"`) do set "CODE=%%i"
echo Linked. Keep this window open.
echo You can stop the agent anytime from the RepoReady website.
echo.
start "" "%RETURN%?code=%CODE%"
powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing -Uri '%API%/agent.py' -OutFile ($env:TEMP + '\\setup_check.py')"
if errorlevel 1 (
  echo Failed to download agent.py
  pause
  exit /b 1
)
python "%TEMP%\\setup_check.py" serve --api "%API%" --code %CODE%
pause
"""


def handler(event, _context):
    method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or "GET"
    )
    if method == "OPTIONS":
        return response(200, {})

    path = event.get("rawPath") or event.get("path") or ""
    qs = _qs(event)
    code = (qs.get("code") or [None])[0] or ""
    api = api_base_from_event(event)

    if path.endswith("/agent/connect.cmd") and method == "GET":
        ret = unquote((qs.get("return") or [""])[0] or "https://main.d3qwc7ge49pla9.amplifyapp.com")
        body = _bootstrap_cmd(api, ret.rstrip("/"))
        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="RepoReady-Connect.cmd"',
            },
            "body": body,
        }

    if path.endswith("/agent/pending") and method == "GET":
        if not code:
            return response(400, {"error": "code query param required"})
        return response(200, {"ok": True, "sessions": list_pending_sessions(code)})

    if path.endswith("/agent/status") and method == "GET":
        return response(200, agent_status(code))

    if path.endswith("/agent/heartbeat") and method == "POST":
        body = parse_body(event)
        code = (body.get("code") or code or "").strip()
        fp = body.get("fingerprint") if isinstance(body.get("fingerprint"), dict) else None
        try:
            return response(200, agent_heartbeat(code, fingerprint=fp))
        except ValueError as exc:
            return response(400, {"error": str(exc)})

    if path.endswith("/agent/stop") and method == "POST":
        body = parse_body(event)
        code = (body.get("code") or code or "").strip()
        try:
            return response(200, request_agent_stop(code))
        except ValueError as exc:
            return response(400, {"error": str(exc)})

    if path.endswith("/sessions/local-intent") and method == "POST":
        body = parse_body(event)
        try:
            session = create_local_intent(
                (body.get("local_path") or body.get("path") or "").strip(),
                agent_code=(body.get("agent_code") or body.get("agentCode") or "").strip() or None,
                api_base=api,
            )
        except ValueError as exc:
            return response(400, {"error": str(exc)})
        return response(201, to_dict(session))

    # /sessions/{id}/hydrate
    if path.rstrip("/").endswith("/hydrate") and method == "POST":
        sid = path_param(event, "id")
        if not sid:
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3 and parts[-1] == "hydrate":
                sid = parts[-2]
        if not sid:
            return response(400, {"error": "bad hydrate path"})
        body = parse_body(event)
        try:
            session = hydrate_local_session(
                sid,
                files=body.get("files") or {},
                tree_paths=body.get("tree_paths") or body.get("treePaths"),
            )
        except KeyError:
            return response(404, {"error": "session not found"})
        return response(200, to_dict(session))

    return response(404, {"error": f"unknown agent route: {path}"})
