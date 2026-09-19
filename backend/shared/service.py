from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from .github import GitHubError, get_file_text, get_repo, get_tree, parse_repo_url
from . import agents
from .crash_preview import build_crash_preview, extract_packages_from_snippets
from .infer import (
    _defaults,
    _heuristic_from_paths,
    _heuristic_from_snippets,
    _pick_source_files,
    infer_requirements,
    needs_inference,
)
from .models import (
    AgentCommand,
    BootResult,
    Fingerprint,
    InstallResult,
    Requirements,
    Session,
)
from .parse import parse_manifests
from .store import SessionStore, get_store

MANIFEST_CANDIDATES = [
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".python-version",
    ".nvmrc",
    ".node-version",
    ".env.example",
    ".env.sample",
    ".env.template",
    "go.mod",
    "Cargo.toml",
    "environment.yml",
    "environment.yaml",
    "setup.py",
    "setup.cfg",
    "poetry.lock",
    "uv.lock",
    "README.md",
    "readme.md",
    "README.rst",
    "manage.py",
]


def public_api_base() -> str:
    return (os.environ.get("PUBLIC_API_BASE") or "http://127.0.0.1:8787").rstrip("/")


def api_base_from_event(event: dict | None) -> str:
    """Resolve the public API URL from API Gateway without CloudFormation circular deps."""
    if not event:
        return public_api_base()
    ctx = event.get("requestContext") or {}
    domain = ctx.get("domainName") or ""
    if domain:
        return f"https://{domain}".rstrip("/")
    headers = event.get("headers") or {}
    # HTTP API may lowercase headers
    host = headers.get("host") or headers.get("Host") or ""
    if host and "localhost" not in host and "127.0.0.1" not in host:
        proto = headers.get("x-forwarded-proto") or headers.get("X-Forwarded-Proto") or "https"
        return f"{proto}://{host}".rstrip("/")
    return public_api_base()


def make_agent_command(session_id: str, api_base: str | None = None) -> AgentCommand:
    base = (api_base or public_api_base()).rstrip("/")
    return AgentCommand(
        posix=f"curl -fsSL {base}/agent.py | python3 - {session_id}",
        windows=f"irm {base}/agent.py | python - {session_id}",
        local=f"python agent/setup_check.py {session_id} --api {base}",
    )


def create_session(
    repo_url: str,
    store: SessionStore | None = None,
    api_base: str | None = None,
    agent_code: str | None = None,
) -> Session:
    store = store or get_store()
    owner, repo = parse_repo_url(repo_url)
    session_id = uuid4().hex
    now = _now()
    code = (agent_code or "").strip().lower() or None
    session = Session(
        session_id=session_id,
        repo_url=f"https://github.com/{owner}/{repo}",
        status="analyzing",
        owner=owner,
        repo=repo,
        source="github",
        install=InstallResult(),
        boot=BootResult(),
        agent_command=make_agent_command(session_id, api_base=api_base),
        agent_code=code,
        created_at=now,
        updated_at=now,
    )
    store.put(session)
    try:
        meta = get_repo(owner, repo)
        branch = meta.get("default_branch") or "main"
        tree = get_tree(owner, repo, branch)
        path_set = {t.get("path") for t in tree if t.get("type") == "blob"}
        files: dict[str, str] = {}
        for name in MANIFEST_CANDIDATES:
            if name in path_set:
                text = get_file_text(owner, repo, name, ref=branch)
                if text is not None:
                    files[name] = text
        req = parse_manifests(files) if files else Requirements()
        paths = sorted(p for p in path_set if p)
        snippets: dict[str, str] = {}
        if needs_inference(req):
            req = infer_requirements(owner, repo, branch, tree, existing=req)
            if files:
                req.manifests_found = sorted(set(req.manifests_found) | set(files.keys()))
        for rel in _pick_source_files(paths):
            try:
                text = files.get(rel) or get_file_text(owner, repo, rel, ref=branch)
            except GitHubError:
                continue
            if text:
                snippets[rel] = text[:4000]
        req = agents.refine_requirements(
            req,
            paths,
            snippets,
            origin=f"github:{owner}/{repo}@{branch}",
        )
        pkgs = extract_packages_from_snippets(snippets, req.runtime)
        if pkgs:
            req.packages = sorted(set(req.packages or []) | set(pkgs))
        session.requirements = req
        session.analysis_snippets = {k: v[:2000] for k, v in list(snippets.items())[:8]}
        session.status = "awaiting_agent"
        session.updated_at = _now()
        store.update(session)
        return session
    except GitHubError as exc:
        session.status = "error"
        session.error = str(exc)
        session.updated_at = _now()
        store.update(session)
        return session
    except Exception as exc:
        session.status = "error"
        session.error = f"Failed to analyze repository: {exc}"
        session.updated_at = _now()
        store.update(session)
        return session


def create_local_session(
    local_path: str,
    files: dict[str, str] | None = None,
    tree_paths: list[str] | None = None,
    store: SessionStore | None = None,
    api_base: str | None = None,
    agent_code: str | None = None,
) -> Session:
    """Create a session from a folder on the user's machine (manifests uploaded by the agent)."""
    store = store or get_store()
    path = (local_path or "").strip()
    if not path:
        raise ValueError("local_path is required")
    files = files or {}
    tree_paths = tree_paths or list(files.keys())
    session_id = uuid4().hex
    now = _now()
    name = path.replace("\\", "/").rstrip("/").split("/")[-1] or "local-project"
    code = (agent_code or "").strip().lower() or None
    session = Session(
        session_id=session_id,
        repo_url=f"local://{path}",
        status="analyzing",
        owner="local",
        repo=name,
        source="local",
        local_path=path,
        install=InstallResult(),
        boot=BootResult(),
        agent_command=make_agent_command(session_id, api_base=api_base),
        agent_code=code,
        created_at=now,
        updated_at=now,
    )
    store.put(session)
    try:
        req = parse_manifests(files) if files else Requirements()
        snippets = {
            k: v
            for k, v in files.items()
            if k.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".md"))
        }
        if needs_inference(req):
            req = _heuristic_from_paths(req, tree_paths)
            if snippets:
                _heuristic_from_snippets(req, snippets)
            req.inferred = True
            if not req.manifests_found:
                req.notes.append("No dependency manifest found — requirements inferred from local source")
            _defaults(req)
            req.env_vars = sorted(set(req.env_vars))
            req.services = sorted(set(req.services))
        req = agents.refine_requirements(
            req,
            tree_paths,
            snippets,
            origin=f"local:{path}",
        )
        pkgs = extract_packages_from_snippets(snippets, req.runtime)
        if pkgs:
            req.packages = sorted(set(req.packages or []) | set(pkgs))
        session.requirements = req
        session.analysis_snippets = {k: v[:2000] for k, v in list(snippets.items())[:8]}
        session.status = "awaiting_agent"
        session.updated_at = _now()
        store.update(session)
        return session
    except Exception as exc:
        session.status = "error"
        session.error = f"Failed to analyze local folder: {exc}"
        session.updated_at = _now()
        store.update(session)
        return session


def get_session(session_id: str, store: SessionStore | None = None) -> Session | None:
    store = store or get_store()
    return store.get(session_id)


def ingest_results(session_id: str, payload: dict, store: SessionStore | None = None) -> Session:
    from .diagnose import diagnose_install

    store = store or get_store()
    session = store.get(session_id)
    if not session:
        raise KeyError(session_id)

    raw_fp = payload.get("fingerprint") if isinstance(payload, dict) else None
    if not isinstance(raw_fp, dict) or not _fingerprint_looks_real(raw_fp):
        raise ValueError(
            "Laptop fingerprint missing or empty — agent did not report PC tools. "
            "Keep the agent window open and re-run the check."
        )

    session.status = "scoring"
    session.updated_at = _now()
    store.update(session)

    session.fingerprint = Fingerprint.from_dict(raw_fp)
    session.install = InstallResult.from_dict(payload.get("install"))
    session.boot = BootResult.from_dict(payload.get("boot"))

    if session.install.attempted and session.install.ok is False and not session.install.diagnosis:
        session.install.diagnosis = diagnose_install(
            session.install.logs,
            session.requirements,
            session.fingerprint,
        )

    session.score = agents.score_machine(
        session.requirements,
        session.fingerprint,
        session.install,
        session.boot,
    )
    preview = build_crash_preview(
        session.requirements,
        session.fingerprint,
        session.install,
        session.boot,
        snippets=session.analysis_snippets or {},
        probe=payload.get("probe") if isinstance(payload.get("probe"), dict) else None,
        source=session.source or "github",
    )
    session.crash_preview = preview.to_dict()
    session.status = "complete"
    session.updated_at = _now()
    store.update(session)
    return session


def _fingerprint_looks_real(fp: dict) -> bool:
    """Reject empty payloads that would falsely claim Python/git are missing."""
    if not fp:
        return False
    os_name = str(fp.get("os") or "").strip().lower()
    if os_name and os_name not in {"unknown", "none", "null"}:
        return True
    return bool(fp.get("python") or fp.get("node") or fp.get("git") or fp.get("tools"))


def agent_heartbeat(agent_code: str, store: SessionStore | None = None, fingerprint: dict | None = None) -> dict:
    store = store or get_store()
    code = (agent_code or "").strip().lower()
    if not code:
        raise ValueError("code is required")
    prev = store.get_agent_heartbeat(code) or {}
    should_stop = bool(prev.get("stop"))
    # Heartbeat clears the stop latch after the agent observes it.
    store.put_agent_heartbeat(code, stop=False, fingerprint=fingerprint)
    return {"ok": True, "code": code, "stop": should_stop}


def request_agent_stop(agent_code: str, store: SessionStore | None = None) -> dict:
    store = store or get_store()
    code = (agent_code or "").strip().lower()
    if not code:
        raise ValueError("code is required")
    store.request_agent_stop(code)
    return {"ok": True, "code": code, "stopping": True}


def agent_status(agent_code: str, store: SessionStore | None = None) -> dict:
    """UI polls this over HTTPS — never touches localhost."""
    import time

    store = store or get_store()
    code = (agent_code or "").strip().lower()
    if not code:
        return {"ok": False, "online": False, "error": "code required"}
    row = store.get_agent_heartbeat(code)
    if not row:
        return {"ok": True, "online": False, "code": code}
    last = float(row.get("last_seen") or 0)
    online = (time.time() - last) < 12  # heartbeat every ~3s
    fp = row.get("fingerprint") if isinstance(row.get("fingerprint"), dict) else None
    return {
        "ok": True,
        "online": online,
        "code": code,
        "last_seen": last,
        "fingerprint": fp,
    }


def list_pending_sessions(agent_code: str, store: SessionStore | None = None) -> list[dict]:
    store = store or get_store()
    code = (agent_code or "").strip().lower()
    sessions = store.list_pending_for_agent(code)
    return [
        {
            "session_id": s.session_id,
            "repo_url": s.repo_url,
            "status": s.status,
            "source": s.source,
            "local_path": s.local_path,
            "needs_hydrate": s.source == "local"
            and not (s.requirements and s.requirements.runtime),
        }
        for s in sessions
    ]


def create_local_intent(
    local_path: str,
    agent_code: str | None = None,
    api_base: str | None = None,
    store: SessionStore | None = None,
) -> Session:
    """Cloud-only: queue a local-folder scan for the laptop agent (no browser→localhost)."""
    store = store or get_store()
    path = (local_path or "").strip()
    if not path:
        raise ValueError("local_path is required")
    code = (agent_code or "").strip().lower() or None
    if not code:
        raise ValueError("agent_code is required — connect your laptop first")
    session_id = uuid4().hex
    now = _now()
    name = path.replace("\\", "/").rstrip("/").split("/")[-1] or "local-project"
    session = Session(
        session_id=session_id,
        repo_url=f"local://{path}",
        status="awaiting_agent",
        owner="local",
        repo=name,
        source="local",
        local_path=path,
        install=InstallResult(),
        boot=BootResult(),
        agent_command=make_agent_command(session_id, api_base=api_base),
        agent_code=code,
        requirements=Requirements(
            notes=["Waiting for laptop agent to read this folder"],
            inferred=True,
        ),
        created_at=now,
        updated_at=now,
    )
    store.put(session)
    return session


def hydrate_local_session(
    session_id: str,
    files: dict[str, str] | None = None,
    tree_paths: list[str] | None = None,
    store: SessionStore | None = None,
) -> Session:
    """Agent uploads folder manifests/snippets; we analyze then leave awaiting fingerprint."""
    store = store or get_store()
    session = store.get(session_id)
    if not session:
        raise KeyError(session_id)
    path = session.local_path or ""
    files = files or {}
    tree_paths = tree_paths or list(files.keys())
    req = parse_manifests(files) if files else Requirements()
    snippets = {
        k: v
        for k, v in files.items()
        if k.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".md"))
    }
    if needs_inference(req):
        req = _heuristic_from_paths(req, tree_paths)
        if snippets:
            _heuristic_from_snippets(req, snippets)
        req.inferred = True
        if not req.manifests_found:
            req.notes.append("No dependency manifest found — requirements inferred from local source")
        _defaults(req)
        req.env_vars = sorted(set(req.env_vars))
        req.services = sorted(set(req.services))
    req = agents.refine_requirements(
        req,
        tree_paths,
        snippets,
        origin=f"local:{path}",
    )
    pkgs = extract_packages_from_snippets(snippets, req.runtime)
    if pkgs:
        req.packages = sorted(set(req.packages or []) | set(pkgs))
    session.requirements = req
    session.analysis_snippets = {k: v[:2000] for k, v in list(snippets.items())[:8]}
    session.status = "awaiting_agent"
    session.updated_at = _now()
    store.update(session)
    return session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
