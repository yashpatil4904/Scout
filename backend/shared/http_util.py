from __future__ import annotations

import base64
import json
from typing import Any


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Content-Type": "application/json",
}


def response(status: int, body: dict | list | str) -> dict:
    if not isinstance(body, str):
        body = json.dumps(body)
    return {"statusCode": status, "headers": CORS_HEADERS, "body": body}


def parse_body(event: dict) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def path_param(event: dict, name: str) -> str | None:
    params = event.get("pathParameters") or {}
    return params.get(name)
