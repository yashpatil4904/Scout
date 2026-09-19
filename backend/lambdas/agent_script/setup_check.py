#!/usr/bin/env python3
"""Setup Readiness Checker — local agent (stdlib only).

Fingerprints THIS machine, clones the repo into a temp sandbox, installs
without touching global site-packages, optionally boots the app, then POSTs
JSON to the cloud API. Never uploads source files.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_API = os.environ.get("SETUP_CHECK_API", "__DEFAULT_API__")
if DEFAULT_API.startswith("__"):
    DEFAULT_API = "http://127.0.0.1:8787"

DEFAULT_AGENT_PORT = int(os.environ.get("SETUP_CHECK_AGENT_PORT", "9876"))

LOG_CAP = 8000
SERVICE_PORTS = {
    "postgres": 5432,
    "redis": 6379,
    "mongodb": 27017,
    "mongo": 27017,
    "mysql": 3306,
    "rabbitmq": 5672,
}
WINDOWS = os.name == "nt"

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
    "main.py",
    "app.py",
    "server.js",
    "server.ts",
    "index.js",
    "index.ts",
]

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
    ".setup-check",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        return serve_main(argv[1:])

    parser = argparse.ArgumentParser(
        description="Fingerprint this machine and verify repo setup",
    )
    parser.add_argument(
        "session_id",
        nargs="?",
        help="Session id from the dashboard (omit when using --path)",
    )
    parser.add_argument(
        "--path",
        help="Local project folder to check (creates a session automatically)",
    )
    parser.add_argument("--api", default=DEFAULT_API, help="API base URL")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Also run sandboxed dependency install (off by default — UI asks per item)",
    )
    parser.add_argument(
        "--boot",
        action="store_true",
        help="Also boot the app after install (off by default — UI asks)",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--skip-boot",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--install-timeout", type=int, default=180)
    parser.add_argument("--boot-wait", type=int, default=10)
    args = parser.parse_args(argv)

    api = args.api.rstrip("/")
    do_install = bool(args.install) and not args.skip_install
    do_boot = bool(args.boot) and not args.skip_boot
    if args.path:
        return run_local_folder_check(
            Path(args.path).expanduser().resolve(),
            api=api,
            skip_install=not do_install,
            skip_boot=not do_boot,
            install_timeout=args.install_timeout,
            boot_wait=args.boot_wait,
        )
    if not args.session_id:
        parser.error("session_id is required unless --path or 'serve' is used")
    return run_session_check(
        args.session_id,
        api=api,
        skip_install=not do_install,
        skip_boot=not do_boot,
        install_timeout=args.install_timeout,
        boot_wait=args.boot_wait,
    )


def run_session_check(
    session_id: str,
    *,
    api: str,
    skip_install: bool = True,
    skip_boot: bool = True,
    install_timeout: int = 180,
    boot_wait: int = 10,
    local_workdir: Path | None = None,
) -> int:
    """Fingerprint this machine. Install/boot only run when skip_* is False (user approved)."""
    print("== Setup Readiness Agent ==")
    print(f"Session: {session_id}")
    print(f"API:     {api}")
    print("This reports YOUR laptop - not the AWS server.\n")
    if skip_install and skip_boot:
        print("Mode: fingerprint only (no installs unless you approve them in the UI)\n")

    try:
        session = api_get(f"{api}/sessions/{session_id}")
    except Exception as exc:
        print(f"Could not load session: {exc}", file=sys.stderr)
        return 1

    if session.get("status") == "error":
        print(f"Session is in error state: {session.get('error')}", file=sys.stderr)
        return 1

    req = session.get("requirements") or {}
    repo_url = session.get("repo_url") or session.get("repoUrl")
    local_path = session.get("local_path") or (str(local_workdir) if local_workdir else None)
    is_local = (session.get("source") == "local") or (
        local_path is not None and str(repo_url or "").startswith("local://")
    )

    # Preserve prior install/boot when this run only fingerprints or only boots.
    prev_install = session.get("install") or {
        "attempted": False,
        "ok": None,
        "exit_code": None,
        "logs": "",
        "diagnosis": None,
        "command": None,
    }
    prev_boot = session.get("boot") or {
        "attempted": False,
        "ok": None,
        "port": req.get("health_port"),
        "health": None,
        "logs": "",
        "command": None,
    }

    print("1/4 Fingerprinting this machine...")
    fingerprint = fingerprint_machine(req)
    _print_fingerprint(fingerprint)

    install = dict(prev_install)
    boot = dict(prev_boot)

    sandbox = None
    workdir: Path | None = Path(local_path) if (is_local and local_path) else None
    cleanup_sandbox = False
    try:
        if skip_install:
            print("\n2/4 Skipping dependency install (waiting for your approval in the UI)")
        elif is_local:
            workdir = Path(local_path or local_workdir or ".")
            if not workdir.is_dir():
                install.update(
                    attempted=True,
                    ok=False,
                    logs=f"Local path does not exist: {workdir}",
                )
                print(f"\n2/4 Local path missing: {workdir}")
            else:
                print(f"\n2/4 Using local folder: {workdir}")
                print("3/4 Installing in a sandbox venv (never global)...")
                install = run_install(workdir, req, timeout=install_timeout)
                status = "ok" if install.get("ok") else "FAILED"
                print(f"    install: {status}")
                if install.get("ok") is False:
                    print(_tail(install.get("logs") or "", 8))
        elif not repo_url or str(repo_url).startswith("local://"):
            install["attempted"] = True
            install["ok"] = False
            install["logs"] = "Session has no cloneable repo_url"
            print("\n2/4 No repo URL on session - cannot clone")
        elif not fingerprint.get("git"):
            install["attempted"] = True
            install["ok"] = False
            install["logs"] = "git is not installed or not on PATH"
            print("\n2/4 git missing - cannot clone into a sandbox")
        else:
            print("\n2/4 Cloning into an isolated temp folder...")
            sandbox = tempfile.mkdtemp(prefix="setup-check-")
            cleanup_sandbox = True
            clone_logs, clone_ok = git_clone(repo_url, sandbox)
            if not clone_ok:
                install.update(
                    attempted=True,
                    ok=False,
                    exit_code=1,
                    logs=_cap(clone_logs),
                    command=f"git clone --depth 1 {repo_url}",
                )
                print("Clone failed.")
            else:
                workdir = Path(sandbox)
                print(f"    sandbox: {sandbox}")
                print("3/4 Installing in a sandbox (never global)...")
                install = run_install(workdir, req, timeout=install_timeout)
                status = "ok" if install.get("ok") else "FAILED"
                print(f"    install: {status}")
                if install.get("ok") is False:
                    print(_tail(install.get("logs") or "", 8))

        if skip_boot:
            print("\n4/4 Skipping boot (waiting for your approval in the UI)")
        elif install.get("ok") and workdir is not None:
            print("\n4/4 Starting the app to verify it is actually alive...")
            boot = run_boot(workdir, req, wait=boot_wait)
            status = "alive" if boot.get("ok") else "did not respond"
            extra = boot.get("health") or (f"port {boot.get('port')}" if boot.get("port") else "")
            print(f"    boot: {status}" + (f" ({extra})" if extra else ""))
        elif install.get("ok") and workdir is None and not skip_install:
            print("\n4/4 Skipping boot (no working directory)")
        elif not install.get("ok"):
            print("\n4/4 Skipping boot because install did not succeed")
        else:
            # Boot approved but we need a workdir (re-clone for github sessions)
            if is_local and local_path:
                workdir = Path(local_path)
                print("\n4/4 Starting the app to verify it is actually alive...")
                boot = run_boot(workdir, req, wait=boot_wait)
            elif repo_url and not str(repo_url).startswith("local://") and fingerprint.get("git"):
                print("\n4/4 Re-cloning briefly to boot-verify...")
                sandbox = tempfile.mkdtemp(prefix="setup-check-boot-")
                cleanup_sandbox = True
                clone_logs, clone_ok = git_clone(repo_url, sandbox)
                if clone_ok:
                    workdir = Path(sandbox)
                    # Re-install quickly into sandbox so boot has deps
                    install = run_install(workdir, req, timeout=install_timeout)
                    if install.get("ok"):
                        boot = run_boot(workdir, req, wait=boot_wait)
                    else:
                        boot = {
                            "attempted": True,
                            "ok": False,
                            "port": req.get("health_port"),
                            "health": "Could not reinstall deps for boot check",
                            "logs": _cap(install.get("logs") or ""),
                            "command": req.get("start_command"),
                        }
                else:
                    boot = {
                        "attempted": True,
                        "ok": False,
                        "port": req.get("health_port"),
                        "health": "Clone failed before boot",
                        "logs": _cap(clone_logs),
                        "command": None,
                    }
            else:
                print("\n4/4 Skipping boot (cannot locate project files)")
    finally:
        if cleanup_sandbox and sandbox and os.path.isdir(sandbox):
            shutil.rmtree(sandbox, ignore_errors=True)

    payload = {"fingerprint": fingerprint, "install": install, "boot": boot}
    print("\nPosting results to the dashboard...")
    try:
        result = api_post(f"{api}/sessions/{session_id}/results", payload)
    except Exception as exc:
        print(f"Failed to post results: {exc}", file=sys.stderr)
        return 1

    score = (result.get("score") or {}).get("percent")
    summary = (result.get("score") or {}).get("summary")
    if score is not None:
        print(f"\nReady: {score}%")
        if summary:
            print(f"  {summary}")
    else:
        print("Results posted. Watch the dashboard for the score.")
    return 0


def run_approved_action(
    session_id: str,
    *,
    api: str,
    action: str,
    blocker_id: str | None = None,
) -> dict:
    """Run one install/boot/tool action only after the user approved it in the UI."""
    action = (action or "").strip().lower()
    blocker_id = (blocker_id or action or "").strip()

    if action in {"deps", "dependencies", "install-unverified", "install-failed"} or blocker_id in {
        "install-unverified",
        "install-failed",
    }:
        code = run_session_check(session_id, api=api, skip_install=False, skip_boot=True)
        return {"ok": code == 0, "action": "deps"}

    if action in {"boot", "boot-unverified", "boot-failed"} or blocker_id in {
        "boot-unverified",
        "boot-failed",
        "boot-skipped",
    }:
        try:
            session = api_get(f"{api}/sessions/{session_id}")
        except Exception as exc:
            return {"ok": False, "action": "boot", "error": str(exc)}
        if not (session.get("install") or {}).get("ok"):
            return {
                "ok": False,
                "action": "boot",
                "error": "Approve project dependency install first, then boot.",
            }
        code = run_session_check(session_id, api=api, skip_install=True, skip_boot=False)
        return {"ok": code == 0, "action": "boot"}

    # System / service installs
    result = install_system_software(blocker_id or action)
    # Re-fingerprint after tool install (still no auto deps/boot)
    code = run_session_check(session_id, api=api, skip_install=True, skip_boot=True)
    return {"ok": result.get("ok") and code == 0, "action": blocker_id or action, "detail": result}


def install_system_software(blocker_id: str) -> dict:
    """Install one missing tool/service. Only called after explicit UI approval."""
    bid = (blocker_id or "").lower()
    cmd: list[str] | None = None
    shell = False

    if bid in {"git-missing"}:
        cmd = _pkg_install_cmd("Git.Git", "git")
    elif bid in {"python-missing", "python-mismatch"}:
        cmd = _pkg_install_cmd("Python.Python.3.12", "python@3.12")
    elif bid in {"node-missing", "node-mismatch"}:
        cmd = _pkg_install_cmd("OpenJS.NodeJS.LTS", "node@lts")
    elif bid in {"docker-missing"}:
        cmd = _pkg_install_cmd("Docker.DockerDesktop", "docker")
    elif bid in {"npm-missing"}:
        return {
            "ok": False,
            "logs": "npm ships with Node.js — approve the Node install instead.",
        }
    elif bid in {"pip-missing"}:
        cmd = [sys.executable, "-m", "ensurepip", "--upgrade"]
    elif bid in {"yarn-missing"}:
        cmd = ["npm", "install", "-g", "yarn"] if shutil.which("npm") else None
    elif bid in {"pnpm-missing"}:
        cmd = ["npm", "install", "-g", "pnpm"] if shutil.which("npm") else None
    elif bid.startswith("svc-"):
        svc = bid.split("-", 1)[-1]
        images = {
            "postgres": [
                "docker",
                "run",
                "-d",
                "--name",
                "setup-check-postgres",
                "-p",
                "5432:5432",
                "-e",
                "POSTGRES_PASSWORD=postgres",
                "postgres:16",
            ],
            "redis": [
                "docker",
                "run",
                "-d",
                "--name",
                "setup-check-redis",
                "-p",
                "6379:6379",
                "redis:7",
            ],
            "mongodb": [
                "docker",
                "run",
                "-d",
                "--name",
                "setup-check-mongo",
                "-p",
                "27017:27017",
                "mongo:7",
            ],
            "mysql": [
                "docker",
                "run",
                "-d",
                "--name",
                "setup-check-mysql",
                "-p",
                "3306:3306",
                "-e",
                "MYSQL_ROOT_PASSWORD=root",
                "mysql:8",
            ],
        }
        cmd = images.get(svc)
        if cmd and not shutil.which("docker"):
            return {"ok": False, "logs": "Docker is not installed. Approve docker-missing first."}
    elif bid.startswith("env-"):
        return {
            "ok": False,
            "logs": "Environment variables cannot be installed automatically — set them in a .env file.",
        }

    if not cmd:
        return {"ok": False, "logs": f"No automatic installer mapped for {blocker_id}"}

    print(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, shell=shell)
        logs = (proc.stdout or "") + (proc.stderr or "")
        return {"ok": proc.returncode == 0, "logs": _cap(logs), "command": " ".join(cmd)}
    except Exception as exc:
        return {"ok": False, "logs": str(exc), "command": " ".join(cmd)}


def _pkg_install_cmd(winget_id: str, brew_formula: str) -> list[str] | None:
    if WINDOWS:
        if shutil.which("winget"):
            return [
                "winget",
                "install",
                "-e",
                "--id",
                winget_id,
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
        return None
    if shutil.which("brew"):
        return ["brew", "install", brew_formula]
    if shutil.which("apt-get") and brew_formula in {"git", "docker"}:
        pkg = "git" if brew_formula == "git" else "docker.io"
        return ["sudo", "apt-get", "install", "-y", pkg]
    return None


def run_local_folder_check(
    folder: Path,
    *,
    api: str,
    skip_install: bool = False,
    skip_boot: bool = False,
    install_timeout: int = 180,
    boot_wait: int = 10,
) -> int:
    if not folder.is_dir():
        print(f"Folder not found: {folder}", file=sys.stderr)
        return 1
    print("== Setup Readiness Agent (local folder) ==")
    print(f"Folder: {folder}")
    print(f"API:    {api}\n")

    files, tree_paths = collect_local_project(folder)
    print(f"Found {len(files)} manifest/source files ({len(tree_paths)} paths scanned)")
    try:
        session = api_post(
            f"{api}/sessions/local",
            {
                "local_path": str(folder),
                "files": files,
                "tree_paths": tree_paths[:500],
            },
        )
    except Exception as exc:
        print(f"Could not create local session: {exc}", file=sys.stderr)
        return 1
    if session.get("status") == "error":
        print(f"Analyze failed: {session.get('error')}", file=sys.stderr)
        return 1
    session_id = session["session_id"]
    print(f"Session: {session_id}")
    return run_session_check(
        session_id,
        api=api,
        skip_install=skip_install,
        skip_boot=skip_boot,
        install_timeout=install_timeout,
        boot_wait=boot_wait,
        local_workdir=folder,
    )


def collect_local_project(folder: Path) -> tuple[dict[str, str], list[str]]:
    """Walk the whole project tree and collect manifests + key source files."""
    files: dict[str, str] = {}
    tree_paths: list[str] = []
    basename_hits = {
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "Pipfile",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".env.example",
        ".env.sample",
        ".python-version",
        ".nvmrc",
        ".node-version",
        "go.mod",
        "Cargo.toml",
        "manage.py",
        "pom.xml",
        "build.gradle",
    }

    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_PARTS and not d.startswith(".")]
        rel_root = Path(root).relative_to(folder)
        for name in names:
            rel = str(rel_root / name).replace("\\", "/") if str(rel_root) != "." else name
            if any(part in SKIP_DIR_PARTS for part in rel.split("/")):
                continue
            tree_paths.append(rel)
            lower = name.lower()
            should_read = (
                name in MANIFEST_CANDIDATES
                or lower in {b.lower() for b in basename_hits}
                or lower.endswith((".toml", ".yml", ".yaml"))
                and any(k in lower for k in ("docker", "compose", "environment"))
            )
            if should_read:
                try:
                    text = (folder / rel).read_text(encoding="utf-8", errors="replace")[:20000]
                except Exception:
                    continue
                files[rel] = text
                # Also expose top-level basename for the parser (first wins = nearest root)
                if name not in files and rel.count("/") <= 1:
                    files[name] = text
                elif name not in files:
                    files[name] = text
            elif name.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs")) and rel.count("/") <= 3:
                if len([k for k in files if k.endswith((".py", ".js", ".ts", ".tsx", ".jsx"))]) < 60:
                    try:
                        text = (folder / rel).read_text(encoding="utf-8", errors="replace")[:4000]
                    except Exception:
                        continue
                    files[rel] = text
        if len(tree_paths) >= 2000:
            break

    for name in MANIFEST_CANDIDATES:
        direct = folder / name
        if direct.is_file():
            try:
                files[name] = direct.read_text(encoding="utf-8", errors="replace")[:20000]
            except Exception:
                pass
    return files, tree_paths


def serve_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Local agent sidecar for the dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_AGENT_PORT)
    parser.add_argument("--api", default=DEFAULT_API)
    args = parser.parse_args(argv)
    return run_sidecar(port=args.port, default_api=args.api.rstrip("/"))


def run_sidecar(*, port: int, default_api: str) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    jobs: dict[str, str] = {}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *a) -> None:  # noqa: N802
            sys.stderr.write("[agent] " + (fmt % a) + "\n")

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, code: int, body: dict) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] in {"/", "/health"}:
                self._json(200, {"ok": True, "service": "setup-check-agent", "port": port})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            path = self.path.split("?")[0]
            if path == "/run":
                session_id = (body.get("session_id") or body.get("sessionId") or "").strip()
                api = (body.get("api") or default_api).rstrip("/")
                # Default: fingerprint only. Installs require /approve after UI consent.
                skip_install = body.get("skip_install", True)
                skip_boot = body.get("skip_boot", True)
                if "install" in body:
                    skip_install = not bool(body.get("install"))
                if "boot" in body:
                    skip_boot = not bool(body.get("boot"))
                if not session_id:
                    self._json(400, {"error": "session_id required"})
                    return
                with lock:
                    if jobs.get(session_id) == "running":
                        self._json(202, {"status": "already_running", "session_id": session_id})
                        return
                    jobs[session_id] = "running"

                def _job() -> None:
                    try:
                        run_session_check(
                            session_id,
                            api=api,
                            skip_install=bool(skip_install),
                            skip_boot=bool(skip_boot),
                        )
                        with lock:
                            jobs[session_id] = "done"
                    except Exception as exc:
                        with lock:
                            jobs[session_id] = f"error: {exc}"

                threading.Thread(target=_job, daemon=True).start()
                self._json(202, {"status": "started", "session_id": session_id, "mode": "fingerprint"})
                return

            if path == "/approve":
                session_id = (body.get("session_id") or body.get("sessionId") or "").strip()
                api = (body.get("api") or default_api).rstrip("/")
                action = (body.get("action") or body.get("blocker_id") or "").strip()
                if not session_id or not action:
                    self._json(400, {"error": "session_id and action/blocker_id required"})
                    return
                if not body.get("approved", True):
                    self._json(200, {"ok": True, "skipped": True, "action": action})
                    return
                job_key = f"{session_id}:{action}"
                with lock:
                    if jobs.get(job_key) == "running":
                        self._json(202, {"status": "already_running", "action": action})
                        return
                    jobs[job_key] = "running"

                def _approve_job() -> None:
                    try:
                        run_approved_action(session_id, api=api, action=action, blocker_id=action)
                        with lock:
                            jobs[job_key] = "done"
                    except Exception as exc:
                        with lock:
                            jobs[job_key] = f"error: {exc}"

                threading.Thread(target=_approve_job, daemon=True).start()
                self._json(202, {"status": "started", "session_id": session_id, "action": action})
                return

            if path == "/local":
                folder = (body.get("path") or body.get("local_path") or "").strip()
                api = (body.get("api") or default_api).rstrip("/")
                # Fingerprint only unless caller explicitly opts into install/boot.
                skip_install = body.get("skip_install", True)
                skip_boot = body.get("skip_boot", True)
                if "install" in body:
                    skip_install = not bool(body.get("install"))
                if "boot" in body:
                    skip_boot = not bool(body.get("boot"))
                if not folder:
                    self._json(400, {"error": "path required"})
                    return
                target = Path(folder).expanduser()
                if not target.is_dir():
                    self._json(400, {"error": f"folder not found: {folder}"})
                    return

                try:
                    files, tree_paths = collect_local_project(target.resolve())
                    session = api_post(
                        f"{api}/sessions/local",
                        {
                            "local_path": str(target.resolve()),
                            "files": files,
                            "tree_paths": tree_paths[:500],
                        },
                    )
                except Exception as exc:
                    self._json(502, {"error": str(exc)})
                    return
                if session.get("status") == "error":
                    self._json(502, session)
                    return
                sid = session["session_id"]

                def _continue() -> None:
                    run_session_check(
                        sid,
                        api=api,
                        skip_install=bool(skip_install),
                        skip_boot=bool(skip_boot),
                        local_workdir=target.resolve(),
                    )

                threading.Thread(target=_continue, daemon=True).start()
                self._json(
                    202,
                    {
                        "status": "started",
                        "session": session,
                        "session_id": sid,
                        "mode": "fingerprint",
                    },
                )
                return

            self._json(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Local agent sidecar listening on http://127.0.0.1:{port}")
    print(f"Default API: {default_api}")
    print("Dashboard will auto-trigger checks here — no manual paste needed.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSidecar stopped.")
        return 0
    return 0


def fingerprint_machine(req: dict) -> dict:
    python_ver = cmd_version([sys.executable, "--version"]) or ".".join(map(str, sys.version_info[:3]))
    node = cmd_version(["node", "--version"])
    npm = cmd_version(["npm", "--version"]) or cmd_version(["npm.cmd", "--version"])
    # If Node is present, npm almost always is too — resolve via which as a fallback.
    if not npm and (node or resolve_cmd("npm")):
        npm = cmd_version(["npm", "--version"]) or ("available" if resolve_cmd("npm") else None)

    tools = []
    for name in (
        "git",
        "docker",
        "pip",
        "pip3",
        "npm",
        "yarn",
        "pnpm",
        "poetry",
        "pipenv",
        "conda",
        "java",
        "go",
        "rustc",
        "pyenv",
        "nvm",
        "corepack",
    ):
        if resolve_cmd(name):
            tools.append(name)

    env_required = list(req.get("env_vars") or [])
    present = [n for n in env_required if os.environ.get(n)]
    missing = [n for n in env_required if n not in present]

    running, svc_missing = [], []
    for svc in req.get("services") or []:
        port = SERVICE_PORTS.get(str(svc).lower())
        if not port:
            continue
        if port_open(port):
            running.append(svc)
        else:
            svc_missing.append(svc)

    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "ram_mb": ram_mb(),
        "disk_mb": disk_mb(),
        "python": python_ver,
        "node": node,
        "pip": cmd_version([sys.executable, "-m", "pip", "--version"])
        or cmd_version(["pip", "--version"])
        or cmd_version(["pip3", "--version"]),
        "npm": npm,
        "git": cmd_version(["git", "--version"]),
        "docker": cmd_version(["docker", "--version"]),
        "tools": tools,
        "env_vars_present": present,
        "env_vars_missing": missing,
        "services_running": running,
        "services_missing": svc_missing,
    }


def run_install(repo: Path, req: dict, timeout: int) -> dict:
    # mkdtemp + git clone: empty dir causes clone to fail. Handle both layouts.
    repo = _repo_root(repo)
    runtime = (req.get("runtime") or req.get("language") or "").lower()
    command = req.get("install_command")
    logs = []
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["NPM_CONFIG_FUND"] = "false"
    env["NPM_CONFIG_AUDIT"] = "false"

    try:
        if runtime in {"python", "py"} or (repo / "requirements.txt").exists() or (repo / "pyproject.toml").exists():
            venv = repo / ".setup-check"
            logs.append(f"$ {sys.executable} -m venv {venv}")
            r = subprocess.run(
                [sys.executable, "-m", "venv", str(venv)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(repo),
            )
            logs.append((r.stdout or "") + (r.stderr or ""))
            if r.returncode != 0:
                return _install_result(True, False, r.returncode, "\n".join(logs), "python -m venv")
            pip = _venv_bin(venv, "pip")
            py = _venv_bin(venv, "python")
            if (repo / "requirements.txt").exists():
                cmd = [str(pip), "install", "-r", "requirements.txt"]
            elif (repo / "pyproject.toml").exists():
                cmd = [str(pip), "install", "."]
            elif command and command.startswith("pip"):
                cmd = [str(py), "-m", *command.split()]
            else:
                cmd = [str(pip), "install", "."]
            return _run_install_cmd(cmd, repo, logs, timeout, env)

        if runtime in {"node", "javascript", "typescript"} or (repo / "package.json").exists():
            if (repo / "pnpm-lock.yaml").exists() and resolve_cmd("pnpm"):
                cmd = [resolve_cmd("pnpm") or "pnpm", "install", "--ignore-scripts"]
                return _run_install_cmd(cmd, repo, logs, timeout, env)
            if (repo / "yarn.lock").exists() and resolve_cmd("yarn"):
                yarn = resolve_cmd("yarn") or "yarn"
                cmd = [yarn, "install", "--ignore-scripts"]
                if WINDOWS and yarn.lower().endswith((".cmd", ".bat")):
                    return _run_install_cmd(
                        subprocess.list2cmdline(cmd), repo, logs, timeout, env, shell=True
                    )
                return _run_install_cmd(cmd, repo, logs, timeout, env)
            npm = resolve_cmd("npm")
            if not npm:
                return _install_result(True, False, 127, "npm is not on PATH", "npm install")
            cmd = [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"]
            if WINDOWS and npm.lower().endswith((".cmd", ".bat")):
                return _run_install_cmd(
                    subprocess.list2cmdline(cmd),
                    repo,
                    logs,
                    timeout,
                    env,
                    shell=True,
                )
            return _run_install_cmd(cmd, repo, logs, timeout, env)

        if command:
            # last resort: run inferred command but still inside the sandbox cwd
            cmd = command if WINDOWS else ["bash", "-lc", command]
            if isinstance(cmd, str):
                return _run_install_cmd(cmd, repo, logs, timeout, env, shell=True)
            return _run_install_cmd(cmd, repo, logs, timeout, env)

        return _install_result(
            True,
            False,
            None,
            "No install command could be inferred (no requirements.txt / pyproject.toml / package.json)",
            None,
        )
    except subprocess.TimeoutExpired:
        return _install_result(True, False, None, "\n".join(logs) + "\n[timed out]", command)
    except Exception as exc:
        return _install_result(True, False, 1, f"{chr(10).join(logs)}\n{exc}", command)


def run_boot(repo: Path, req: dict, wait: int) -> dict:
    repo = _repo_root(repo)
    command = req.get("start_command")
    port = req.get("health_port")
    path = req.get("health_path") or "/"
    if not command:
        if (repo / "manage.py").exists():
            command = "python manage.py runserver 0.0.0.0:8000"
            port = port or 8000
        elif (repo / "package.json").exists():
            command = "npm start"
            port = port or 3000
        elif (repo / "app.py").exists():
            command = f"{sys.executable} app.py"
            port = port or 5000
        elif (repo / "main.py").exists():
            command = f"{sys.executable} main.py"
            port = port or 8000
        else:
            return {
                "attempted": True,
                "ok": False,
                "port": port,
                "health": "No start command inferred",
                "logs": "",
                "command": None,
            }

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PORT", str(port or 8000))
    # Prefer the sandbox venv interpreter for Python apps
    venv_py = _venv_bin(repo / ".setup-check", "python")
    launch = command
    if venv_py.exists() and command.startswith("python "):
        launch = f'"{venv_py}" {command[len("python "):]}'
    elif venv_py.exists() and command.startswith("uvicorn "):
        launch = f'"{venv_py}" -m {command}'

    logs = [f"$ {launch}"]
    popen_kwargs: dict = {
        "cwd": str(repo),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "env": env,
        "text": True,
    }
    if WINDOWS:
        popen_kwargs["shell"] = True
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["shell"] = True
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(launch, **popen_kwargs)
    output: list[str] = []
    reader = threading.Thread(target=_drain_stdout, args=(proc, output), daemon=True)
    reader.start()
    deadline = time.time() + max(wait, 4)
    ok = False
    health_msg = None
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                health_msg = "process exited before becoming healthy"
                break
            if port and port_open(int(port)):
                probed = http_probe(int(port), path)
                if probed and "setup-readiness" in probed:
                    health_msg = "port is in use by the readiness API, not the target app"
                    ok = False
                    break
                if probed is not None:
                    ok = True
                    health_msg = probed
                    break
                ok = True
                health_msg = f"port {port} is listening"
                break
            time.sleep(0.4)
        else:
            health_msg = health_msg or f"timed out waiting for port {port or '(unknown)'}"
    finally:
        _stop(proc)
        reader.join(timeout=1.5)

    return {
        "attempted": True,
        "ok": ok,
        "port": port,
        "health": health_msg,
        "logs": _cap("\n".join(logs + output)),
        "command": command,
    }


def _run_install_cmd(cmd, repo: Path, logs: list[str], timeout: int, env: dict, shell: bool = False) -> dict:
    display = cmd if isinstance(cmd, str) else " ".join(cmd)
    logs.append(f"$ {display}")
    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        shell=shell,
    )
    logs.append((proc.stdout or "") + (proc.stderr or ""))
    return _install_result(True, proc.returncode == 0, proc.returncode, "\n".join(logs), display)


def _install_result(attempted, ok, code, logs, command) -> dict:
    return {
        "attempted": attempted,
        "ok": ok,
        "exit_code": code,
        "logs": _cap(logs or ""),
        "diagnosis": None,
        "command": command,
    }


def _repo_root(path: Path) -> Path:
    if (path / ".git").exists() or (path / "package.json").exists() or (path / "requirements.txt").exists():
        return path
    children = [p for p in path.iterdir() if p.is_dir()]
    if len(children) == 1:
        return children[0]
    return path


def _venv_bin(venv: Path, name: str) -> Path:
    if WINDOWS:
        exe = f"{name}.exe" if not name.endswith(".exe") else name
        return venv / "Scripts" / exe
    return venv / "bin" / name


def git_clone(repo_url: str, dest: str) -> tuple[str, bool]:
    """Clone into dest. mkdtemp already created dest, so clone into a subfolder first
    or remove dest and let git create it."""
    parent = str(Path(dest))
    # git clone refuses non-empty dirs; rmtree + clone
    shutil.rmtree(parent, ignore_errors=True)
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, parent],
        capture_output=True,
        text=True,
        timeout=120,
    )
    logs = (proc.stdout or "") + (proc.stderr or "")
    return logs, proc.returncode == 0 and Path(parent).exists()


def resolve_cmd(name: str) -> str | None:
    """Resolve an executable on PATH, including Windows .cmd/.bat shims (npm, yarn, etc.)."""
    if not name:
        return None
    if os.path.isabs(name) and Path(name).exists():
        return name
    found = shutil.which(name)
    if found:
        return found
    if WINDOWS:
        for ext in (".cmd", ".bat", ".exe", ".COM"):
            found = shutil.which(name if name.lower().endswith(ext) else name + ext)
            if found:
                return found
    return None


def cmd_version(cmd: list[str]) -> str | None:
    if not cmd:
        return None
    resolved = list(cmd)
    if resolved[0] != sys.executable and not os.path.isabs(resolved[0]):
        path = resolve_cmd(resolved[0])
        if not path:
            return None
        resolved[0] = path
    try:
        # Windows .cmd/.bat requires shell=True unless we invoke via cmd.exe
        use_shell = WINDOWS and resolved[0].lower().endswith((".cmd", ".bat"))
        if use_shell:
            proc = subprocess.run(
                subprocess.list2cmdline(resolved),
                capture_output=True,
                text=True,
                timeout=8,
                shell=True,
            )
        else:
            proc = subprocess.run(resolved, capture_output=True, text=True, timeout=8)
    except Exception:
        return None
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode not in (0, None) and not text:
        return None
    if not text:
        return None
    line = text.splitlines()[0].strip()
    return line[:80]


def ram_mb() -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:
        pass
    if WINDOWS:
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys / (1024 * 1024))
        except Exception:
            return None
        return None
    try:
        pages = os.sysconf("SC_PAGE_SIZE")
        n = os.sysconf("SC_PHYS_PAGES")
        return int(pages * n / (1024 * 1024))
    except Exception:
        return None


def disk_mb() -> int | None:
    try:
        target = os.environ.get("SystemDrive", "C:\\") if WINDOWS else "/"
        usage = shutil.disk_usage(target)
        return int(usage.free / (1024 * 1024))
    except Exception:
        return None


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.35):
            return True
    except Exception:
        return False


def http_probe(port: int, path: str) -> str | None:
    url = f"http://127.0.0.1:{int(port)}{path if path.startswith('/') else '/' + path}"
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "setup-check-agent"})
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            body = resp.read(400).decode("utf-8", errors="replace")
            return f"HTTP {resp.status} {url} {body[:120]!r}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} {url}"
    except Exception:
        return None


def _drain_stdout(proc: subprocess.Popen, bucket: list[str]) -> None:
    if not proc.stdout:
        return
    try:
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            bucket.append(line)
    except Exception:
        return


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
        if WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            proc.kill()
    except Exception:
        pass


def api_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "setup-check-agent"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "setup-check-agent",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cap(text: str) -> str:
    if len(text) <= LOG_CAP:
        return text
    return text[:1000] + "\n...\n" + text[-(LOG_CAP - 1200) :]


def _tail(text: str, n: int) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join("    " + ln for ln in lines[-n:])


def _print_fingerprint(fp: dict) -> None:
    print(f"    OS       {fp.get('os')} {fp.get('arch')}")
    print(f"    Python   {fp.get('python') or 'not found'}")
    print(f"    Node     {fp.get('node') or 'not found'}")
    print(f"    RAM      {fp.get('ram_mb') or '?'} MB   free disk {fp.get('disk_mb') or '?'} MB")
    print(f"    tools    {', '.join(fp.get('tools') or []) or 'none'}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        raise SystemExit(130)
