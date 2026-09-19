from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from .models import Session, to_dict


class SessionStore(Protocol):
    def put(self, session: Session) -> None: ...
    def get(self, session_id: str) -> Session | None: ...
    def update(self, session: Session) -> None: ...


class FileStore:
    """JSON file store for local FastAPI development."""

    def __init__(self, path: str | None = None) -> None:
        default = Path(__file__).resolve().parent.parent / ".sessions.json"
        self.path = Path(path or os.environ.get("SESSION_FILE", str(default)))

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def put(self, session: Session) -> None:
        data = self._load()
        data[session.session_id] = to_dict(session)
        self._save(data)

    def get(self, session_id: str) -> Session | None:
        data = self._load()
        return Session.from_dict(data.get(session_id))

    def update(self, session: Session) -> None:
        self.put(session)


class DynamoStore:
    def __init__(self, table_name: str | None = None) -> None:
        import boto3

        self.table_name = table_name or os.environ["SESSIONS_TABLE"]
        self.table = boto3.resource("dynamodb").Table(self.table_name)

    def put(self, session: Session) -> None:
        item = _to_dynamo(to_dict(session))
        item["sessionId"] = session.session_id
        self.table.put_item(Item=item)

    def get(self, session_id: str) -> Session | None:
        resp = self.table.get_item(Key={"sessionId": session_id})
        item = resp.get("Item")
        if not item:
            return None
        item.pop("sessionId", None)
        return Session.from_dict(_from_dynamo(item))

    def update(self, session: Session) -> None:
        self.put(session)


def _to_dynamo(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        from decimal import Decimal

        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    from decimal import Decimal

    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def get_store() -> SessionStore:
    if os.environ.get("SESSIONS_TABLE"):
        return DynamoStore()
    return FileStore()
