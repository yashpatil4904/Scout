from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import BootResult, Fingerprint, InstallResult, Requirements, to_dict
from .score import _node_satisfies, _python_satisfies, _semver_head

# Map import name → pip/npm package when they differ
PY_IMPORT_TO_PIP = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "cv": "opencv-python",
    "Crypto": "pycryptodome",
    "serial": "pyserial",
    "wx": "wxPython",
    "gi": "PyGObject",
    "attr": "attrs",
    "dateutil": "python-dateutil",
    "flask_sqlalchemy": "Flask-SQLAlchemy",
    "flask_cors": "Flask-Cors",
    "rest_framework": "djangorestframework",
    "jose": "python-jose",
    "jwt": "PyJWT",
}

PY_STDLIB = {
    "os", "sys", "re", "json", "time", "datetime", "math", "random", "pathlib",
    "typing", "collections", "itertools", "functools", "subprocess", "threading",
    "multiprocessing", "asyncio", "logging", "argparse", "unittest", "io",
    "copy", "hashlib", "hmac", "base64", "urllib", "http", "email", "csv",
    "sqlite3", "tempfile", "shutil", "glob", "struct", "enum", "dataclasses",
    "contextlib", "traceback", "warnings", "abc", "pprint", "string", "textwrap",
    "socket", "ssl", "queue", "concurrent", "importlib", "pkgutil", "platform",
    "getpass", "secrets", "statistics", "decimal", "fractions", "array", "bisect",
    "heapq", "weakref", "types", "inspect", "ast", "dis", "gc", "ctypes",
    "__future__", "builtins", "site", "errno", "signal", "mmap", "select",
}

PY_IMPORT = re.compile(
    r"^\s*(?:from\s+([a-zA-Z_][\w.]*)\s+import|import\s+([a-zA-Z_][\w.]*))",
    re.MULTILINE,
)
JS_IMPORT = re.compile(
    r"""(?:require\(\s*['"]([^'"./][^'"]*)['"]\s*\)|from\s+['"]([^'"./][^'"]*)['"]|import\s+['"]([^'"./][^'"]*)['"])""",
)

NODE_BUILTIN = {
    "fs", "path", "http", "https", "url", "util", "os", "crypto", "stream",
    "events", "buffer", "child_process", "net", "zlib", "assert", "querystring",
    "readline", "tty", "dns", "cluster", "worker_threads", "perf_hooks",
}


@dataclass
class CrashFrame:
    step: int
    status: str  # ok | fail | warn | skip
    title: str
    detail: str
    would_see: str = ""
    fix: str = ""
    category: str = "runtime"  # runtime | package | env | service | install | boot

    def to_dict(self) -> dict:
        return to_dict(self)


@dataclass
class CrashPreview:
    entrypoint: str | None = None
    summary: str = ""
    frames: list[CrashFrame] = field(default_factory=list)
    predicted_fail_at: int | None = None
    engine: str = "heuristic"

    def to_dict(self) -> dict:
        return {
            "entrypoint": self.entrypoint,
            "summary": self.summary,
            "predicted_fail_at": self.predicted_fail_at,
            "engine": self.engine,
            "frames": [f.to_dict() for f in self.frames],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "CrashPreview | None":
        if not data:
            return None
        frames = []
        for item in data.get("frames") or []:
            if not isinstance(item, dict):
                continue
            frames.append(
                CrashFrame(
                    step=int(item.get("step") or 0),
                    status=str(item.get("status") or "skip"),
                    title=str(item.get("title") or ""),
                    detail=str(item.get("detail") or ""),
                    would_see=str(item.get("would_see") or ""),
                    fix=str(item.get("fix") or ""),
                    category=str(item.get("category") or "runtime"),
                )
            )
        return cls(
            entrypoint=data.get("entrypoint"),
            summary=str(data.get("summary") or ""),
            frames=frames,
            predicted_fail_at=data.get("predicted_fail_at"),
            engine=str(data.get("engine") or "heuristic"),
        )


def extract_packages_from_snippets(snippets: dict[str, str], runtime: str | None) -> list[str]:
    """Pull third-party imports from source when manifests are missing."""
    blob = "\n".join(snippets.values())
    found: set[str] = set()
    if runtime == "python" or (runtime is None and ".py" in " ".join(snippets.keys())):
        for m in PY_IMPORT.finditer(blob):
            mod = (m.group(1) or m.group(2) or "").split(".")[0]
            if not mod or mod in PY_STDLIB or mod.startswith("_"):
                continue
            found.add(PY_IMPORT_TO_PIP.get(mod, mod))
    if runtime == "node" or any(k.endswith((".js", ".ts", ".tsx", ".jsx", ".mjs")) for k in snippets):
        for m in JS_IMPORT.finditer(blob):
            name = m.group(1) or m.group(2) or m.group(3) or ""
            if not name or name.startswith(".") or name in NODE_BUILTIN:
                continue
            # scoped: @scope/pkg
            if name.startswith("@"):
                parts = name.split("/")
                found.add("/".join(parts[:2]) if len(parts) >= 2 else name)
            else:
                found.add(name.split("/")[0])
    return sorted(found)[:24]


def guess_entrypoint(req: Requirements, snippets: dict[str, str] | None = None) -> str | None:
    if req.start_command:
        return req.start_command
    snippets = snippets or {}
    priority = [
        "app.py", "main.py", "manage.py", "server.py", "wsgi.py", "asgi.py",
        "index.js", "index.ts", "server.js", "server.ts", "app.js", "app.ts",
        "src/index.js", "src/index.ts", "src/main.py", "src/app.py",
    ]
    keys = {k.replace("\\", "/"): k for k in snippets}
    for p in priority:
        if p in keys:
            if p.endswith(".py"):
                return f"python {p}"
            return f"node {p}"
    if req.runtime == "python":
        return "python app.py"
    if req.runtime == "node":
        return "npm start"
    return None


def build_crash_preview(
    req: Requirements | None,
    fp: Fingerprint | None,
    ins: InstallResult | None = None,
    bt: BootResult | None = None,
    snippets: dict[str, str] | None = None,
    probe: dict | None = None,
) -> CrashPreview:
    """
    Ordered 'future crash' timeline: what fails first on THIS PC if the student
    just runs the project — without waiting for error → Google → next error.
    """
    req = req or Requirements()
    fp = fp or Fingerprint()
    ins = ins or InstallResult()
    bt = bt or BootResult()
    snippets = snippets or {}
    probe = probe or {}

    packages = list(req.packages or [])
    if not packages:
        packages = extract_packages_from_snippets(snippets, req.runtime)
    # notes may contain "Inferred packages (no lockfile): a, b"
    for note in req.notes or []:
        if "Inferred packages" in note and ":" in note:
            tail = note.split(":", 1)[1]
            packages.extend(p.strip() for p in tail.split(",") if p.strip())
    packages = sorted(set(packages))[:20]

    entry = guess_entrypoint(req, snippets)
    frames: list[CrashFrame] = []
    step = 0
    first_fail: int | None = None
    blocked = False  # once we predict a hard fail, later frames become skip

    def add(status: str, title: str, detail: str, would_see: str = "", fix: str = "", category: str = "runtime"):
        nonlocal step, first_fail, blocked
        if blocked and status in {"fail", "warn"}:
            status = "skip"
            would_see = would_see or "(never reached — earlier step fails first)"
        step += 1
        if status == "fail" and first_fail is None:
            first_fail = step
            blocked = True
        frames.append(
            CrashFrame(
                step=step,
                status=status,
                title=title,
                detail=detail,
                would_see=would_see,
                fix=fix,
                category=category,
            )
        )

    # --- 1. Runtime ---
    if req.runtime == "python":
        have = _semver_head(fp.python)
        need = req.runtime_version or _semver_head(req.runtime_constraint)
        if not have:
            add(
                "fail",
                "Python is not installed on this PC",
                f"Repo needs Python {need or '3.x'}; this PC has none on PATH",
                "python: command not found",
                "Install Python from python.org (or pyenv) and reopen the terminal",
                "runtime",
            )
        elif need and not _python_satisfies(have, need, req.runtime_constraint):
            add(
                "fail",
                f"Wrong Python version (need {need}, have {have})",
                f"Repo expects Python {req.runtime_constraint or need}; this PC has {fp.python}",
                f"ERROR: Package requires Python {need} but you have {have}",
                f"Install Python {need} (pyenv/winget) without removing {have}",
                "runtime",
            )
        else:
            add(
                "ok",
                f"Python {have} matches",
                f"Repo needs {req.runtime_constraint or need or 'any'}; this PC has {fp.python}",
                category="runtime",
            )
    elif req.runtime == "node":
        have = _semver_head(fp.node)
        need = req.runtime_version or _semver_head(req.runtime_constraint)
        if not have:
            add(
                "fail",
                "Node.js is not installed on this PC",
                f"Repo needs Node {need or 'LTS'}; this PC has none on PATH",
                "'node' is not recognized as an internal or external command",
                "Install Node LTS from nodejs.org or use nvm-windows",
                "runtime",
            )
        elif need and not _node_satisfies(have, need, req.runtime_constraint):
            add(
                "fail",
                f"Node too old (need >={need}, have {have})",
                f"Repo engines/constraint {req.runtime_constraint or need}; this PC has {fp.node}",
                "npm ERR! ERESOLVE unable to resolve dependency tree",
                f"Use nvm to install Node {need}+ without wiping your current Node",
                "runtime",
            )
        else:
            # Newer Node on old projects often still ERESOLVE — soft warn if major gap likely
            add(
                "ok",
                f"Node {have} satisfies minimum",
                f"Repo needs {req.runtime_constraint or need or 'any'}; this PC has {fp.node}",
                category="runtime",
            )
            # Historic trap: very new Node on ancient lockfiles
            try:
                if need and have and int(str(have).split(".")[0]) >= 20 and int(str(need).split(".")[0]) <= 16:
                    add(
                        "warn",
                        "High chance of npm ERESOLVE on this Node",
                        f"Repo looks built for Node ~{need}; you have Node {have}",
                        "npm ERR! ERESOLVE unable to resolve dependency tree",
                        f"Switch to Node {need} with nvm before npm install",
                        "runtime",
                    )
            except ValueError:
                pass
    else:
        add(
            "warn",
            "Could not detect runtime",
            "No package.json / requirements.txt and source signals were weak",
            category="runtime",
        )

    # --- 2. Tooling ---
    if req.runtime == "node" and not (fp.node or fp.npm):
        pass  # already failed above
    elif req.package_manager in {"npm", "yarn", "pnpm"} and fp.node:
        add("ok", f"{req.package_manager or 'npm'} available via Node", f"node={fp.node} npm={fp.npm or 'bundled'}", category="install")
    elif req.runtime == "python" and fp.python:
        add("ok", "pip/python available for installs", f"python={fp.python}", category="install")

    if not fp.git and not (req.source_hints and "local" in str(req.notes)):
        # git only matters for clone; local folder checks skip
        pass

    # --- 3. Packages (the unique part for no-manifest repos) ---
    probe_imports = {}
    if isinstance(probe.get("imports"), dict):
        probe_imports = {str(k): v for k, v in probe["imports"].items()}

    if packages:
        for pkg in packages[:12]:
            key = pkg.replace("-", "_").split("/")[0]
            probed = probe_imports.get(pkg)
            if probed is None:
                probed = probe_imports.get(key)
            if probed is True:
                add(
                    "ok",
                    f"Package present: {pkg}",
                    f"Dry-run import of {pkg} succeeded on this PC",
                    category="package",
                )
            elif probed is False:
                mod = key if req.runtime == "python" else pkg
                would = (
                    f"ModuleNotFoundError: No module named '{mod}'"
                    if req.runtime == "python"
                    else f"Error: Cannot find module '{pkg}'"
                )
                fix = (
                    f"pip install {pkg}"
                    if req.runtime == "python"
                    else f"npm install {pkg}"
                )
                add(
                    "fail",
                    f"Missing package: {pkg}",
                    f"Code imports {pkg}; dry-run on this PC failed",
                    would,
                    fix,
                    "package",
                )
            else:
                # Not probed — predict miss if no install succeeded
                if ins.attempted and ins.ok:
                    add(
                        "ok",
                        f"Deps installed (includes {pkg}?)",
                        "Sandboxed install succeeded; package likely present",
                        category="package",
                    )
                else:
                    would = (
                        f"ModuleNotFoundError: No module named '{key}'"
                        if req.runtime == "python"
                        else f"Error: Cannot find module '{pkg}'"
                    )
                    fix = (
                        f"pip install {pkg}"
                        if req.runtime != "node"
                        else (req.install_command or "npm install")
                    )
                    # Predict as fail only when no manifests (student has no install file)
                    no_manifest = not any(
                        m.replace("\\", "/").split("/")[-1]
                        in {"requirements.txt", "package.json", "pyproject.toml", "Pipfile"}
                        for m in (req.manifests_found or [])
                    )
                    if no_manifest:
                        add(
                            "fail",
                            f"Will crash on missing package: {pkg}",
                            "No requirements/package.json — inferred from imports; not installed yet",
                            would,
                            fix,
                            "package",
                        )
                    else:
                        add(
                            "warn",
                            f"Needs dependency install for {pkg}",
                            f"Manifest exists but install not verified on this PC yet",
                            would,
                            req.install_command or fix,
                            "package",
                        )
                    # Only first predicted package fail blocks the chain for clarity
                    break
    elif req.install_command and not ins.attempted:
        add(
            "warn",
            "Dependencies not verified yet",
            f"Would run: {req.install_command}",
            "",
            req.install_command,
            "install",
        )
    elif ins.attempted and ins.ok is False:
        add(
            "fail",
            "Dependency install already failed",
            (ins.diagnosis or "")[:200] or "non-zero install exit",
            (ins.logs or "")[-180:],
            ins.diagnosis or "Fix runtime version, then re-run install",
            "install",
        )
    elif ins.attempted and ins.ok:
        add("ok", "Dependencies installed", "Sandboxed install succeeded", category="install")

    # --- 4. Env (boot-time crashes) ---
    missing_env = list(fp.env_vars_missing or [])
    if not missing_env and req.env_vars:
        present = set(fp.env_vars_present or [])
        missing_env = [e for e in req.env_vars if e not in present]
    if missing_env:
        name = missing_env[0]
        add(
            "fail" if not blocked else "skip",
            f"Boot will die on missing {name}",
            f"{len(missing_env)} env var(s) missing: {', '.join(missing_env[:5])}",
            f"Error: {name} is not defined",
            f"Create .env with {name}=... (and {len(missing_env) - 1} more)" if len(missing_env) > 1 else f"Create .env with {name}=...",
            "env",
        )

    # --- 5. Services ---
    for svc in req.services or []:
        if svc in {"postgres", "redis", "mongodb", "mysql"} and svc not in (fp.services_running or []):
            add(
                "fail" if not blocked else "skip",
                f"{svc} not running on this PC",
                f"Repo talks to {svc}; default port closed",
                f"Error: connect ECONNREFUSED 127.0.0.1 ({svc})",
                f"Install and start local {svc} (or Docker)",
                "service",
            )
            break

    # --- 6. Boot proof ---
    if req.start_command:
        if bt.attempted and bt.ok:
            add("ok", "App booted healthy", bt.health or f"port {bt.port}", category="boot")
        elif bt.attempted and bt.ok is False:
            add(
                "fail",
                "App failed health check",
                (bt.health or "")[:160] or "port did not respond",
                (bt.logs or "")[-160:],
                "Fix env/services, then retry boot",
                "boot",
            )
        else:
            add(
                "skip" if blocked else "warn",
                "Boot not proven yet",
                f"Would run: {req.start_command}",
                "",
                "Approve Boot in the UI after fixing earlier frames",
                "boot",
            )

    # Renumber + summary
    for i, fr in enumerate(frames, start=1):
        fr.step = i
    if first_fail is not None:
        # remap predicted_fail_at to renumbered step of first fail
        for fr in frames:
            if fr.status == "fail":
                first_fail = fr.step
                break

    fails = [f for f in frames if f.status == "fail"]
    if fails:
        summary = (
            f"If you run this now, you hit failure #{fails[0].step} first: {fails[0].title}. "
            f"{len(fails)} crash(es) predicted before a clean boot."
        )
    elif any(f.status == "warn" for f in frames):
        summary = "No hard crash predicted, but warnings remain before this PC is fully proven."
    else:
        summary = "Dry-run timeline looks clear — this PC should get past the usual setup traps."

    return CrashPreview(
        entrypoint=entry,
        summary=summary,
        frames=frames,
        predicted_fail_at=first_fail,
        engine="probe" if probe_imports else "heuristic",
    )
