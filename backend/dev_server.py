from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.models import to_dict  # noqa: E402
from shared.service import (  # noqa: E402
    agent_heartbeat,
    agent_status,
    create_local_session,
    create_session,
    get_session,
    ingest_results,
    list_pending_sessions,
    public_api_base,
)

AGENT_FILE = ROOT.parent / "agent" / "setup_check.py"
AGENT_PORT = int(os.environ.get("AGENT_PORT", "9877"))

app = FastAPI(title="Setup Readiness Checker", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateSessionBody(BaseModel):
    repoUrl: str | None = None
    repo_url: str | None = None
    agent_code: str | None = None
    agentCode: str | None = None


class CreateLocalSessionBody(BaseModel):
    local_path: str
    files: dict[str, str] | None = None
    tree_paths: list[str] | None = None
    agent_code: str | None = None
    agentCode: str | None = None


class AgentHeartbeatBody(BaseModel):
    code: str


class IngestBody(BaseModel):
    fingerprint: dict | None = None
    install: dict | None = None
    boot: dict | None = None


@app.get("/health")
def health() -> dict:
    from shared.bedrock import last_error, llm_status

    status = llm_status()
    return {
        "service": "setup-readiness",
        "ok": True,
        "agent_port": AGENT_PORT,
        "bedrock": {
            "enabled": status.get("provider") == "bedrock" and status.get("enabled"),
            "model": status.get("model") if status.get("provider") == "bedrock" else None,
            "last_error": last_error(),
        },
        "llm": status,
    }


@app.get("/agent.py")
def agent_script(request: Request) -> PlainTextResponse:
    if not AGENT_FILE.exists():
        raise HTTPException(status_code=404, detail="agent script not packaged")
    text = AGENT_FILE.read_text(encoding="utf-8")
    api = str(request.base_url).rstrip("/")
    text = text.replace("__DEFAULT_API__", api)
    text = text.replace("http://127.0.0.1:8787", api)
    return PlainTextResponse(text, media_type="text/x-python; charset=utf-8")


@app.get("/sessions")
def sessions_hint() -> dict:
    return {"ok": True, "hint": "POST /sessions with {\"repoUrl\": \"https://github.com/owner/repo\"}"}


@app.post("/sessions")
def post_session(body: CreateSessionBody) -> JSONResponse:
    repo_url = (body.repoUrl or body.repo_url or "").strip()
    if not repo_url:
        raise HTTPException(status_code=400, detail="repoUrl is required")
    code = (body.agent_code or body.agentCode or "").strip() or None
    session = create_session(repo_url, agent_code=code)
    status = 201 if session.status != "error" else 400
    # Local-only: kick sidecar on same machine. Amplify uses agent poll instead.
    if session.status == "awaiting_agent" and not code:
        _try_trigger_agent(session.session_id)
    return JSONResponse(to_dict(session), status_code=status)


@app.post("/sessions/local")
def post_local_session(body: CreateLocalSessionBody) -> JSONResponse:
    try:
        session = create_local_session(
            body.local_path,
            files=body.files,
            tree_paths=body.tree_paths,
            agent_code=(body.agent_code or body.agentCode or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    status = 201 if session.status != "error" else 400
    return JSONResponse(to_dict(session), status_code=status)


@app.get("/agent/pending")
def agent_pending(code: str = "") -> dict:
    return {"ok": True, "sessions": list_pending_sessions(code)}


@app.get("/agent/status")
def agent_status_route(code: str = "") -> dict:
    return agent_status(code)


@app.post("/agent/heartbeat")
def agent_heartbeat_route(body: AgentHeartbeatBody) -> dict:
    try:
        return agent_heartbeat(body.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sessions/{session_id}")
def read_session(session_id: str) -> dict:
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return to_dict(session)


@app.post("/sessions/{session_id}/results")
def post_results(session_id: str, body: IngestBody) -> dict:
    try:
        session = ingest_results(
            session_id,
            {
                "fingerprint": body.fingerprint,
                "install": body.install,
                "boot": body.boot,
            },
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")
    return to_dict(session)


def _try_trigger_agent(session_id: str) -> None:
    """Best-effort: ask the local sidecar to run this session (same machine as this API)."""

    def _run() -> None:
        try:
            import json
            import urllib.request

            api = public_api_base()
            payload = json.dumps(
                {
                    "session_id": session_id,
                    "api": api,
                    "skip_install": True,
                    "skip_boot": True,
                }
            ).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{AGENT_PORT}/run",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def _ensure_agent_sidecar() -> None:
    if os.environ.get("AUTO_START_AGENT", "1") not in {"1", "true", "True", "yes"}:
        return
    if not AGENT_FILE.exists():
        return

    def _serve() -> None:
        try:
            subprocess.Popen(
                [sys.executable, str(AGENT_FILE), "serve", "--port", str(AGENT_PORT)],
                cwd=str(ROOT.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    threading.Thread(target=_serve, daemon=True).start()


@app.on_event("startup")
def _on_startup() -> None:
    _ensure_agent_sidecar()


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    os.environ.setdefault("PUBLIC_API_BASE", f"http://{host}:{port}")
    # Local demo: heuristics unless Groq/OpenAI/Gemini/Ollama or Bedrock is set.
    from shared.bedrock import groq_or_openai_configured

    if groq_or_openai_configured():
        os.environ["BEDROCK_DISABLED"] = "1"
    elif not os.environ.get("BEDROCK_MODEL_ID"):
        os.environ.setdefault("BEDROCK_DISABLED", "1")
    os.chdir(ROOT)
    _ensure_agent_sidecar()
    from shared.bedrock import llm_status

    st = llm_status()
    print(
        f"API http://{host}:{port}  agent :{AGENT_PORT}  "
        f"llm={st.get('provider') or 'off'} model={st.get('model') or '-'}"
    )
    uvicorn.run("dev_server:app", host=host, port=port, reload=False)
