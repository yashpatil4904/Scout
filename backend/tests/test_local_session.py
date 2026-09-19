from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.service import create_local_session


def test_create_local_session_from_package_json(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions.json"
    monkeypatch.setenv("SESSION_FILE", str(sessions))
    monkeypatch.delenv("SESSIONS_TABLE", raising=False)

    session = create_local_session(
        str(tmp_path / "my-app"),
        files={
            "package.json": '{"engines":{"node":">=18"},"scripts":{"start":"node server.js"},"dependencies":{"express":"4.18.0"}}'
        },
        tree_paths=["package.json", "server.js"],
    )
    assert session.source == "local"
    assert session.status == "awaiting_agent"
    assert session.requirements is not None
    assert session.requirements.runtime == "node"
    assert session.local_path.endswith("my-app")
