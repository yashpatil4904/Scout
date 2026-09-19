from __future__ import annotations

import re

from .github import GitHubError, get_file_text
from .models import Requirements
from .parse import SERVICE_PACKAGES

SKIP_DIR_PARTS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "coverage",
    "target",
}

SOURCE_EXTS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
}

PRIORITY_FILES = [
    "main.py",
    "app.py",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "server.py",
    "index.js",
    "index.ts",
    "server.js",
    "server.ts",
    "app.js",
    "app.ts",
    "src/index.js",
    "src/index.ts",
    "src/main.py",
    "src/app.py",
    "src/server.ts",
    "src/server.js",
]

ENV_FROM_CODE = [
    re.compile(r"os\.environ(?:\.get)?\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"os\.getenv\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]+)"),
    re.compile(r"process\.env\[['\"]([A-Z][A-Z0-9_]+)['\"]\]"),
]


def infer_requirements(
    owner: str,
    repo: str,
    branch: str,
    tree: list[dict],
    existing: Requirements | None = None,
) -> Requirements:
    """Infer setup needs from the source tree when manifests are missing or incomplete."""
    paths = [
        t.get("path", "")
        for t in tree
        if t.get("type") == "blob" and t.get("path") and not _skipped(t["path"])
    ]
    req = existing or Requirements()
    req.source_hints = _summarize_tree(paths)

    if not req.language:
        req = _heuristic_from_paths(req, paths)

    snippets: dict[str, str] = {}
    for rel in _pick_source_files(paths):
        try:
            text = get_file_text(owner, repo, rel, ref=branch)
        except GitHubError:
            continue
        if text:
            snippets[rel] = text[:4000]

    _heuristic_from_snippets(req, snippets)

    req.inferred = True
    if not req.manifests_found:
        req.notes.append("No dependency manifest found — requirements inferred from source")
    _defaults(req)
    req.env_vars = sorted(set(req.env_vars))
    req.services = sorted(set(req.services))
    return req


def needs_inference(req: Requirements) -> bool:
    if not req.manifests_found:
        return True
    docs_only = set(req.manifests_found) <= {"README.md", "readme.md", "README.rst", "Dockerfile"}
    if docs_only and not req.runtime:
        return True
    if not req.language or not req.runtime:
        return True
    return False


def _skipped(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return any(p in SKIP_DIR_PARTS for p in parts)


def _summarize_tree(paths: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for p in paths:
        ext = "." + p.rsplit(".", 1)[-1].lower() if "." in p.rsplit("/", 1)[-1] else ""
        if ext in SOURCE_EXTS:
            counts[ext] = counts.get(ext, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [f"{ext} x{n}" for ext, n in ranked[:8]]


def _heuristic_from_paths(req: Requirements, paths: list[str]) -> Requirements:
    names = {p.replace("\\", "/").split("/")[-1].lower() for p in paths}
    joined = "\n".join(paths).lower()
    py = sum(1 for p in paths if p.endswith(".py"))
    js = sum(1 for p in paths if p.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs")))
    if py >= js and py > 0:
        req.language = "python"
        req.runtime = "python"
        req.package_manager = "pip"
        if "manage.py" in names:
            req.start_command = req.start_command or "python manage.py runserver"
            req.health_port = req.health_port or 8000
            req.services.append("postgres")
        elif "app.py" in names:
            req.start_command = req.start_command or "flask run"
            req.health_port = req.health_port or 5000
        elif any(p.endswith("main.py") for p in paths):
            req.start_command = req.start_command or "python main.py"
    elif js > 0:
        req.language = "javascript"
        req.runtime = "node"
        req.package_manager = "npm"
        req.install_command = req.install_command or "npm install"
        req.start_command = req.start_command or "npm start"
        req.health_port = req.health_port or 3000
    if "docker-compose.yml" in names or "docker-compose.yaml" in names:
        pass
    if "go.mod" in names:
        req.language = req.language or "go"
        req.runtime = req.runtime or "go"
    if "cargo.toml" in names:
        req.language = req.language or "rust"
        req.runtime = req.runtime or "rustc"
    if "pom.xml" in names or "build.gradle" in names:
        req.language = req.language or "java"
        req.runtime = req.runtime or "java"
    _ = joined
    return req


def _heuristic_from_snippets(req: Requirements, snippets: dict[str, str]) -> None:
    blob = "\n".join(snippets.values())
    for rx in ENV_FROM_CODE:
        for name in rx.findall(blob):
            if name not in {"PATH", "HOME", "USER", "NODE_ENV"}:
                req.env_vars.append(name)
    lower = blob.lower()
    if "fastapi" in lower or "from fastapi" in lower:
        req.language = "python"
        req.runtime = "python"
        req.start_command = req.start_command or "uvicorn main:app --port 8000"
        req.health_port = req.health_port or 8000
        req.health_path = req.health_path or "/docs"
    if "flask" in lower:
        req.start_command = req.start_command or "flask run"
        req.health_port = req.health_port or 5000
    if "django" in lower:
        req.start_command = req.start_command or "python manage.py runserver"
        req.health_port = req.health_port or 8000
    if "express(" in lower or "from 'express'" in lower or 'from "express"' in lower:
        req.health_port = req.health_port or 3000
    for pkg, service in SERVICE_PACKAGES.items():
        if re.search(rf"\b{re.escape(pkg)}\b", blob):
            req.services.append(service)
    for name, port in (("redis", 6379), ("postgres", 5432), ("mongodb", 27017), ("mysql", 3306)):
        if name in lower:
            req.services.append(name)


def _pick_source_files(paths: list[str]) -> list[str]:
    chosen: list[str] = []
    lower_map = {p.replace("\\", "/"): p for p in paths}
    for rel in PRIORITY_FILES:
        if rel in lower_map:
            chosen.append(rel)
    for p in paths:
        if len(chosen) >= 10:
            break
        if p in chosen:
            continue
        ext = "." + p.rsplit(".", 1)[-1].lower() if "." in p.rsplit("/", 1)[-1] else ""
        if ext in SOURCE_EXTS and p.count("/") <= 2:
            chosen.append(p)
    return chosen[:10]


def _merge_inferred(req: Requirements, data: dict) -> Requirements:
    def take(field: str) -> None:
        val = data.get(field)
        if val in (None, "", []):
            return
        setattr(req, field, val)

    for field in (
        "language",
        "runtime",
        "runtime_version",
        "runtime_constraint",
        "package_manager",
        "start_command",
        "install_command",
        "health_path",
    ):
        if not getattr(req, field):
            take(field)
    if data.get("health_port") and not req.health_port:
        try:
            req.health_port = int(data["health_port"])
        except (TypeError, ValueError):
            pass
    if isinstance(data.get("env_vars"), list):
        req.env_vars.extend(str(x) for x in data["env_vars"] if x)
    if isinstance(data.get("services"), list):
        req.services.extend(str(x) for x in data["services"] if x)
    if isinstance(data.get("notes"), list):
        req.notes.extend(str(x) for x in data["notes"] if x)
    return req


def _defaults(req: Requirements) -> None:
    if req.runtime == "python":
        req.package_manager = req.package_manager or "pip"
        if not req.install_command and any(
            m in {p.replace("\\", "/").split("/")[-1] for p in req.manifests_found}
            for m in ("requirements.txt",)
        ):
            req.install_command = "pip install -r requirements.txt"
        elif not req.install_command and "pyproject.toml" in {p.replace("\\", "/").split("/")[-1] for p in req.manifests_found}:
            req.install_command = "pip install -e ."
    if req.runtime == "node":
        req.package_manager = req.package_manager or "npm"
        req.install_command = req.install_command or "npm install"


def _req_brief(req: Requirements) -> str:
    from .models import to_dict
    import json

    return json.dumps(to_dict(req), indent=2)
