from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

from .models import Session, to_dict


class SessionStore(Protocol):
    def put(self, session: Session) -> None: ...
    def get(self, session_id: str) -> Session | None: ...
    def update(self, session: Session) -> None: ...
    def list_pending_for_agent(self, agent_code: str) -> list[Session]: ...
    def put_agent_heartbeat(self, agent_code: str, *, stop: bool | None = None, fingerprint: dict | None = None) -> None: ...
    def request_agent_stop(self, agent_code: str) -> None: ...
    def get_agent_heartbeat(self, agent_code: str) -> dict | None: ...


class FileStore:
    """JSON file store for local FastAPI development."""

    def __init__(self, path: str | None = None) -> None:
        default = Path(__file__).resolve().parent.parent / ".sessions.json"
        self.path = Path(path or os.environ.get("SESSION_FILE", str(default)))
        self.agents_path = self.path.with_name(".agents.json")

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

    def list_pending_for_agent(self, agent_code: str) -> list[Session]:
        code = (agent_code or "").strip().lower()
        if not code:
            return []
        out: list[Session] = []
        for raw in self._load().values():
            if not isinstance(raw, dict):
                continue
            if (raw.get("agent_code") or "").strip().lower() != code:
                continue
            if raw.get("status") != "awaiting_agent":
                continue
            sess = Session.from_dict(raw)
            if sess:
                out.append(sess)
        return out

    def put_agent_heartbeat(self, agent_code: str, *, stop: bool | None = None, fingerprint: dict | None = None) -> None:
        code = (agent_code or "").strip().lower()
        if not code:
            return
        data = {}
        if self.agents_path.exists():
            try:
                data = json.loads(self.agents_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        prev = data.get(code) if isinstance(data.get(code), dict) else {}
        row = {
            "code": code,
            "last_seen": time.time(),
            "ok": True,
            "stop": bool(prev.get("stop")) if stop is None else bool(stop),
            "fingerprint": fingerprint if fingerprint is not None else prev.get("fingerprint"),
        }
        if stop is False:
            row["stop"] = False
        data[code] = row
        self.agents_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def request_agent_stop(self, agent_code: str) -> None:
        self.put_agent_heartbeat(agent_code, stop=True)

    def get_agent_heartbeat(self, agent_code: str) -> dict | None:
        code = (agent_code or "").strip().lower()
        if not code or not self.agents_path.exists():
            return None
        try:
            data = json.loads(self.agents_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        row = data.get(code)
        return row if isinstance(row, dict) else None


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

    def list_pending_for_agent(self, agent_code: str) -> list[Session]:
        code = (agent_code or "").strip().lower()
        if not code:
            return []
        # Low-volume hackathon scan — fine for Ship It demos.
        resp = self.table.scan(
            FilterExpression="agent_code = :c AND #s = :st",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":c": code, ":st": "awaiting_agent"},
        )
        out: list[Session] = []
        for item in resp.get("Items") or []:
            item = dict(item)
            item.pop("sessionId", None)
            sess = Session.from_dict(_from_dynamo(item))
            if sess:
                out.append(sess)
        return out

    def put_agent_heartbeat(self, agent_code: str, *, stop: bool | None = None, fingerprint: dict | None = None) -> None:
        code = (agent_code or "").strip().lower()
        if not code:
            return
        prev = self.get_agent_heartbeat(code) or {}
        stop_flag = bool(prev.get("stop")) if stop is None else bool(stop)
        if stop is False:
            stop_flag = False
        fp = fingerprint if fingerprint is not None else prev.get("fingerprint")
        item = {
            "sessionId": f"_agent_{code}",
            "kind": "agent_heartbeat",
            "agent_code": code,
            "last_seen": _to_dynamo(time.time()),
            "ok": True,
            "stop": stop_flag,
        }
        if isinstance(fp, dict) and fp:
            item["fingerprint"] = _to_dynamo(fp)
        self.table.put_item(Item=item)

    def request_agent_stop(self, agent_code: str) -> None:
        self.put_agent_heartbeat(agent_code, stop=True)

    def get_agent_heartbeat(self, agent_code: str) -> dict | None:
        code = (agent_code or "").strip().lower()
        if not code:
            return None
        resp = self.table.get_item(Key={"sessionId": f"_agent_{code}"})
        item = resp.get("Item")
        if not item:
            return None
        return _from_dynamo(item)


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
