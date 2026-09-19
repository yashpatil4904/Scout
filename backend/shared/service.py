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
    return os.environ.get("PUBLIC_API_BASE", "http://127.0.0.1:8787").rstrip("/")


def make_agent_command(session_id: str, api_base: str | None = None) -> AgentCommand:
    base = (api_base or public_api_base()).rstrip("/")
    return AgentCommand(
        posix=f"curl -fsSL {base}/agent.py | python3 - {session_id}",
        windows=f"irm {base}/agent.py | python - {session_id}",
        local=f"python agent/setup_check.py {session_id} --api {base}",
    )


def create_session(repo_url: str, store: SessionStore | None = None) -> Session:
    store = store or get_store()
    owner, repo = parse_repo_url(repo_url)
    session_id = uuid4().hex
    now = _now()
    session = Session(
        session_id=session_id,
        repo_url=f"https://github.com/{owner}/{repo}",
        status="analyzing",
        owner=owner,
        repo=repo,
        source="github",
        install=InstallResult(),
        boot=BootResult(),
        agent_command=make_agent_command(session_id),
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
        agent_command=make_agent_command(session_id),
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
    session.status = "scoring"
    session.updated_at = _now()
    store.update(session)

    session.fingerprint = Fingerprint.from_dict(payload.get("fingerprint"))
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
    )
    session.crash_preview = preview.to_dict()
    session.status = "complete"
    session.updated_at = _now()
    store.update(session)
    return session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
