from __future__ import annotations

import json
import re

from .models import Requirements

SERVICE_PACKAGES = {
    "psycopg2": "postgres",
    "psycopg": "postgres",
    "asyncpg": "postgres",
    "sqlalchemy": "postgres",
    "django": "postgres",
    "redis": "redis",
    "pymongo": "mongodb",
    "motor": "mongodb",
    "mysqlclient": "mysql",
    "pymysql": "mysql",
    "boto3": "aws",
    "celery": "redis",
    "sqlite3": "sqlite",
}

NODE_SERVICE_PACKAGES = {
    "pg": "postgres",
    "postgres": "postgres",
    "ioredis": "redis",
    "redis": "redis",
    "mongoose": "mongodb",
    "mongodb": "mongodb",
    "mysql": "mysql",
    "mysql2": "mysql",
    "sqlite3": "sqlite",
    "aws-sdk": "aws",
    "@aws-sdk/client-s3": "aws",
}

ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]+)\s*=", re.MULTILINE)
PY_VERSION = re.compile(r"python\s*([<>=!~^]*\s*3(?:\.\d+){1,2})", re.I)
NODE_VERSION = re.compile(r"node(?:\.js)?\s*([<>=!~^]*\s*\d+(?:\.\d+)*)", re.I)
FROM_PY = re.compile(r"FROM\s+python:(\d+(?:\.\d+)?)", re.I)
FROM_NODE = re.compile(r"FROM\s+node:(\d+(?:\.\d+)?)", re.I)
PORT_HINT = re.compile(r"(?:PORT|port)\s*[=:]\s*(\d{2,5})")
EXPOSE = re.compile(r"EXPOSE\s+(\d{2,5})", re.I)

ROOT_PYTHON = {
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "setup.py",
    "setup.cfg",
    "manage.py",
    ".python-version",
    "environment.yml",
    "environment.yaml",
    "poetry.lock",
    "uv.lock",
}
ROOT_NODE = {
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    ".nvmrc",
    ".node-version",
}


def parse_manifests(files: dict[str, str]) -> Requirements:
    """Parse repo manifests. Prefer root-level signals so nested frontend/package.json
    does not redefine a Python (or other) project as Node."""
    by_base = _index_by_basename(files)
    root_files = {p.replace("\\", "/"): t for p, t in files.items() if "/" not in p.replace("\\", "/")}
    primary = _detect_primary_stack(root_files, by_base, files)

    req = Requirements(manifests_found=sorted(files.keys()), inferred=False)
    notes: list[str] = [f"Primary stack: {primary}"]

    if primary == "python":
        if "requirements.txt" in by_base:
            _apply_requirements_txt(req, by_base["requirements.txt"], notes)
        if "pyproject.toml" in by_base:
            _apply_pyproject(req, by_base["pyproject.toml"], notes)
        if "Pipfile" in by_base:
            _apply_pipfile(req, by_base["Pipfile"], notes)
        env_yml = by_base.get("environment.yml") or by_base.get("environment.yaml")
        if env_yml:
            _apply_conda(req, env_yml, notes)
        if ".python-version" in by_base:
            ver = by_base[".python-version"].strip().splitlines()[0].strip()
            req.runtime = "python"
            req.language = "python"
            req.runtime_version = req.runtime_version or _normalize_python(ver)
            req.runtime_constraint = req.runtime_constraint or ver
        # Nested package.json is incidental (docs site, etc.) — do not switch stack to Node.
        if "package.json" in by_base and "package.json" not in root_files:
            notes.append("Ignored nested package.json (not the primary stack)")
    elif primary == "node":
        pkg = by_base.get("package.json")
        if pkg:
            _apply_package_json(req, pkg, notes)
        if ".nvmrc" in by_base or ".node-version" in by_base:
            ver = (by_base.get(".nvmrc") or by_base.get(".node-version") or "").strip().splitlines()[0]
            ver = ver.lstrip("v")
            req.runtime = "node"
            req.language = req.language or "javascript"
            # Soft minimum only — never pin exact major as a hard requirement from nvmrc alone
            req.runtime_constraint = req.runtime_constraint or f">={ver.split('.')[0]}"
            req.runtime_version = req.runtime_version or ver.split(".")[0]
    else:
        # Unknown / mixed — apply whatever root files exist, Python first then Node
        if any(k in root_files for k in ROOT_PYTHON) or "requirements.txt" in by_base:
            if "requirements.txt" in by_base:
                _apply_requirements_txt(req, by_base["requirements.txt"], notes)
            if "pyproject.toml" in by_base:
                _apply_pyproject(req, by_base["pyproject.toml"], notes)
        if "package.json" in root_files or (primary == "unknown" and "package.json" in by_base):
            # Only root package.json for unknown, else nearest
            raw = root_files.get("package.json") or by_base.get("package.json")
            if raw:
                _apply_package_json(req, raw, notes)

    if "Dockerfile" in by_base:
        _apply_dockerfile(req, by_base["Dockerfile"], notes, primary=primary)
    compose = by_base.get("docker-compose.yml") or by_base.get("docker-compose.yaml")
    if compose:
        _apply_compose(req, compose, notes)
    env_example = (
        by_base.get(".env.example") or by_base.get(".env.sample") or by_base.get(".env.template")
    )
    if env_example:
        for name in ENV_LINE.findall(env_example):
            if name not in req.env_vars and name not in {"NODE_ENV", "PYTHONUNBUFFERED"}:
                req.env_vars.append(name)
    readme = by_base.get("README.md") or by_base.get("readme.md") or by_base.get("README.rst")
    if readme:
        _apply_readme(req, readme, notes, primary=primary)

    _finalize_commands(req, {**by_base, **root_files}, primary=primary)
    req.notes = notes
    req.env_vars = sorted(set(req.env_vars))
    req.services = sorted(set(req.services))
    return req


def _index_by_basename(files: dict[str, str]) -> dict[str, str]:
    """Map basename -> content, preferring shallower paths."""
    best: dict[str, tuple[int, str]] = {}
    for path, text in files.items():
        norm = path.replace("\\", "/")
        base = norm.split("/")[-1]
        depth = norm.count("/")
        prev = best.get(base)
        if prev is None or depth < prev[0]:
            best[base] = (depth, text)
    return {k: v[1] for k, v in best.items()}


def _detect_primary_stack(
    root_files: dict[str, str],
    by_base: dict[str, str],
    all_files: dict[str, str],
) -> str:
    root_names = set(root_files.keys())
    py_root = len(root_names & ROOT_PYTHON)
    node_root = len(root_names & ROOT_NODE)
    if py_root and not node_root:
        return "python"
    if node_root and not py_root:
        return "node"
    if py_root and node_root:
        # Both at root — prefer explicit lock/manifest strength
        if "requirements.txt" in root_names or "pyproject.toml" in root_names:
            return "python"
        if "package.json" in root_names:
            return "node"
    # Fall back to counts across tree
    paths = [p.replace("\\", "/").lower() for p in all_files]
    py_hits = sum(1 for p in paths if p.endswith((".py", "requirements.txt", "pyproject.toml")))
    node_hits = sum(1 for p in paths if p.endswith(("package.json", ".ts", ".tsx", ".js", ".jsx")))
    if py_hits > node_hits and py_hits > 0:
        return "python"
    if node_hits > 0:
        return "node"
    if "requirements.txt" in by_base or "pyproject.toml" in by_base:
        return "python"
    if "package.json" in by_base:
        return "node"
    return "unknown"


def _apply_package_json(req: Requirements, raw: str, notes: list[str]) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        notes.append("package.json could not be parsed")
        return
    req.language = req.language or "javascript"
    req.runtime = req.runtime or "node"
    req.package_manager = req.package_manager or "npm"
    engines = data.get("engines") or {}
    node_eng = engines.get("node")
    if node_eng:
        # Always treat engines.node as a minimum, not an exact pin.
        # "20" / "20.x" / ">=20" all mean "Node 20+" for readiness.
        num = _first_number(str(node_eng))
        req.runtime_constraint = str(node_eng)
        req.runtime_version = num
    scripts = data.get("scripts") or {}
    if scripts.get("start"):
        req.start_command = "npm start"
    elif scripts.get("dev"):
        req.start_command = "npm run dev"
    elif scripts.get("start") or scripts.get("dev"):
        req.start_command = req.start_command or scripts.get("start") or scripts.get("dev")
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    for pkg, service in NODE_SERVICE_PACKAGES.items():
        if pkg in deps:
            req.services.append(service)
    if "next" in deps:
        req.health_port = req.health_port or 3000
        req.health_path = req.health_path or "/"
    elif "vite" in deps:
        req.health_port = req.health_port or 5173
        req.health_path = req.health_path or "/"
    elif "express" in deps or "fastify" in deps or "koa" in deps:
        req.health_port = req.health_port or 3000
        req.health_path = req.health_path or "/"
    # Library packages often have no start script — don't invent a boot target
    if not scripts.get("start") and not scripts.get("dev"):
        notes.append("No start/dev script — boot check not required")
    notes.append("Parsed package.json for Node runtime, scripts, and services")


def _apply_requirements_txt(req: Requirements, raw: str, notes: list[str]) -> None:
    req.language = "python"
    req.runtime = "python"
    req.package_manager = req.package_manager or "pip"
    for line in raw.splitlines():
        name = re.split(r"[=<>!~\[]", line.strip().lower())[0].strip()
        if name in SERVICE_PACKAGES:
            req.services.append(SERVICE_PACKAGES[name])
        if name == "django":
            req.health_port = req.health_port or 8000
            req.start_command = req.start_command or "python manage.py runserver"
        elif name == "flask":
            req.start_command = req.start_command or "flask run"
            req.health_port = req.health_port or 5000
        elif name in {"fastapi", "uvicorn", "gunicorn"}:
            req.start_command = req.start_command or "uvicorn main:app --port 8000"
            req.health_port = req.health_port or 8000
    notes.append("Parsed requirements.txt")


def _apply_pyproject(req: Requirements, raw: str, notes: list[str]) -> None:
    req.language = req.language or "python"
    req.runtime = req.runtime or "python"
    if "poetry" in raw.lower() or "[tool.poetry]" in raw:
        req.package_manager = req.package_manager or "poetry"
    else:
        req.package_manager = req.package_manager or "pip"
    m = re.search(r'requires-python\s*=\s*["\']([^"\']+)["\']', raw)
    if m:
        req.runtime_constraint = m.group(1)
        req.runtime_version = _normalize_python(_first_number(m.group(1)) or "")
    for pkg, service in SERVICE_PACKAGES.items():
        if re.search(rf'["\']{re.escape(pkg)}(?:[="\']|$)', raw, re.I):
            req.services.append(service)
    notes.append("Parsed pyproject.toml")


def _apply_pipfile(req: Requirements, raw: str, notes: list[str]) -> None:
    req.language = "python"
    req.runtime = "python"
    req.package_manager = req.package_manager or "pipenv"
    m = re.search(r'python_version\s*=\s*["\']([^"\']+)["\']', raw)
    if m:
        req.runtime_version = _normalize_python(m.group(1))
        req.runtime_constraint = m.group(1)
    notes.append("Parsed Pipfile")


def _apply_conda(req: Requirements, raw: str, notes: list[str]) -> None:
    req.language = req.language or "python"
    req.runtime = req.runtime or "python"
    req.package_manager = req.package_manager or "conda"
    m = re.search(r"python\s*=\s*([0-9.]+)", raw, re.I)
    if m:
        req.runtime_version = _normalize_python(m.group(1))
    notes.append("Parsed conda environment file")


def _apply_dockerfile(req: Requirements, raw: str, notes: list[str], primary: str = "unknown") -> None:
    py = FROM_PY.search(raw)
    node = FROM_NODE.search(raw)
    if py and primary != "node":
        req.language = req.language or "python"
        req.runtime = req.runtime or "python"
        req.runtime_version = req.runtime_version or _normalize_python(py.group(1))
        req.runtime_constraint = req.runtime_constraint or f">={_normalize_python(py.group(1))}"
    if node and primary != "python":
        req.language = req.language or "javascript"
        req.runtime = req.runtime or "node"
        # Soft minimum from Docker tag
        ver = node.group(1).split(".")[0]
        req.runtime_version = req.runtime_version or ver
        req.runtime_constraint = req.runtime_constraint or f">={ver}"
    m = EXPOSE.search(raw)
    if m:
        req.health_port = req.health_port or int(m.group(1))
    notes.append("Parsed Dockerfile base image / EXPOSE")


def _apply_compose(req: Requirements, raw: str, notes: list[str]) -> None:
    lower = raw.lower()
    for name in ("postgres", "redis", "mongodb", "mongo", "mysql", "rabbitmq"):
        if re.search(rf"image:\s*.*{name}|^\s+{name}:", lower, re.M):
            service = "mongodb" if name == "mongo" else name
            req.services.append(service)
    notes.append("Parsed docker-compose services")


def _apply_readme(req: Requirements, raw: str, notes: list[str], primary: str = "unknown") -> None:
    # Only fill gaps — never override a detected stack from manifests
    if primary != "node":
        py = PY_VERSION.search(raw)
        if py and not req.runtime_version and (not req.runtime or req.runtime == "python"):
            req.language = req.language or "python"
            req.runtime = req.runtime or "python"
            req.runtime_constraint = py.group(1).strip()
            req.runtime_version = _normalize_python(_first_number(py.group(1)) or "")
    if primary != "python":
        node = NODE_VERSION.search(raw)
        if node and not req.runtime_version and (not req.runtime or req.runtime == "node"):
            req.language = req.language or "javascript"
            req.runtime = req.runtime or "node"
            num = _first_number(node.group(1))
            req.runtime_constraint = f">={num}" if num else node.group(1).strip()
            req.runtime_version = num
    port = PORT_HINT.search(raw)
    if port and not req.health_port:
        req.health_port = int(port.group(1))


def _finalize_commands(req: Requirements, files: dict[str, str], primary: str = "unknown") -> None:
    if req.runtime == "python" or primary == "python":
        if "requirements.txt" in files:
            req.install_command = req.install_command or "pip install -r requirements.txt"
        elif "pyproject.toml" in files:
            if req.package_manager == "poetry":
                req.install_command = req.install_command or "poetry install"
            else:
                req.install_command = req.install_command or "pip install ."
        elif "Pipfile" in files:
            req.install_command = req.install_command or "pipenv install"
        req.package_manager = req.package_manager or "pip"
        if req.start_command:
            req.health_path = req.health_path or "/"
            req.health_port = req.health_port or 8000
        return

    if req.runtime == "node" or primary == "node":
        if "pnpm-lock.yaml" in files:
            req.package_manager = "pnpm"
            req.install_command = req.install_command or "pnpm install"
        elif "yarn.lock" in files:
            req.package_manager = "yarn"
            req.install_command = req.install_command or "yarn install"
        else:
            req.package_manager = req.package_manager or "npm"
            req.install_command = req.install_command or "npm install"
        if req.start_command:
            req.health_path = req.health_path or "/"
            req.health_port = req.health_port or 3000
        # Do NOT invent "npm start" when there is no start script


def _first_number(text: str) -> str | None:
    m = re.search(r"(\d+(?:\.\d+)*)", text)
    return m.group(1) if m else None


def _normalize_python(ver: str) -> str | None:
    if not ver:
        return None
    ver = ver.strip().lstrip("v")
    parts = ver.split(".")
    if parts and parts[0] == "3" and len(parts) == 1:
        return "3"
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return ver
