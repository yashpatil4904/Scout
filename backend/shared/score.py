from __future__ import annotations

import re

from .diagnose import polish_blockers
from .models import (
    Blocker,
    BootResult,
    Fingerprint,
    InstallResult,
    Requirements,
    Score,
)

# Primary score = "will this repo run on THIS PC's software?"
# Runtime + tooling dominate. Env is config (not install). Install/boot are optional proof.
RUNTIME_WEIGHT = 50
TOOLING_WEIGHT = 25
ENV_WEIGHT = 10
INSTALL_WEIGHT = 8
BOOT_WEIGHT = 7


def compute_score(
    requirements: Requirements | None,
    fingerprint: Fingerprint | None,
    install: InstallResult | None,
    boot: BootResult | None,
    polish: bool = True,
) -> Score:
    req = requirements or Requirements()
    fp = fingerprint or Fingerprint()
    ins = install or InstallResult()
    bt = boot or BootResult()
    blockers: list[Blocker] = []
    next_steps: list[Blocker] = []

    runtime_pts = _score_runtime(req, fp, blockers)
    tooling_pts = _score_tooling(req, fp, blockers)
    env_pts = _score_env(req, fp, blockers)
    install_pts = _score_install(req, ins, blockers, next_steps)
    boot_pts = _score_boot(req, ins, bt, blockers, next_steps)

    percent = int(round(runtime_pts + tooling_pts + env_pts + install_pts + boot_pts))
    percent = clamp_percent(percent, req, ins, bt)

    # Real problems first; optional next steps last
    blockers.sort(key=lambda b: {"critical": 0, "warning": 1, "info": 2}.get(b.severity, 3))
    summary = _summary(percent, blockers, next_steps, req, fp)
    payload = [
        {
            "id": b.id,
            "title": b.title,
            "severity": b.severity,
            "evidence": b.evidence,
            "fix": b.fix,
        }
        for b in blockers + next_steps
    ]
    summary, payload = polish_blockers(summary, payload) if polish else (summary, payload)
    blockers_out = [Blocker.from_dict(b) for b in payload]
    return Score(percent=percent, summary=summary, blockers=blockers_out, engine="heuristic")


def clamp_percent(
    percent: int,
    req: Requirements,
    ins: InstallResult,
    bt: BootResult,
) -> int:
    percent = max(0, min(100, int(percent)))
    needs_install = bool(req.install_command)
    needs_boot = bool(req.start_command)
    if needs_install and not ins.attempted:
        percent = min(percent, 85)
    elif needs_install and ins.ok is False:
        percent = min(percent, 55)
    if needs_boot and ins.ok and not bt.attempted:
        percent = min(percent, 90)
    elif needs_boot and bt.attempted and bt.ok is False:
        percent = min(percent, 82)
    if percent >= 90 and needs_boot and not (bt.attempted and bt.ok):
        percent = min(percent, 88)
    return percent


def _score_runtime(req: Requirements, fp: Fingerprint, blockers: list[Blocker]) -> float:
    if not req.runtime:
        blockers.append(
            Blocker(
                id="runtime-unknown",
                title="Could not tell which runtime this repo needs",
                severity="warning",
                evidence="No language/runtime was declared or inferred",
                fix="Add a package.json, requirements.txt, or .python-version so checks can be exact",
            )
        )
        return RUNTIME_WEIGHT * 0.5

    if req.runtime == "python":
        have = _semver_head(fp.python)
        need = req.runtime_version or _semver_head(req.runtime_constraint)
        if not have:
            blockers.append(
                Blocker(
                    id="python-missing",
                    title="Python is not installed (or not on PATH)",
                    severity="critical",
                    evidence="Agent did not detect a python executable",
                    fix=_python_fix(need),
                )
            )
            return 0
        if need and not _python_satisfies(have, need, req.runtime_constraint):
            blockers.append(
                Blocker(
                    id="python-mismatch",
                    title=f"Python {need} required, this machine has {have}",
                    severity="critical",
                    evidence=f"Repo wants {req.runtime_constraint or need}; agent reported {fp.python}",
                    fix=_python_fix(need),
                )
            )
            return 0
        return RUNTIME_WEIGHT

    if req.runtime == "node":
        have = _semver_head(fp.node)
        need = req.runtime_version
        if not have:
            blockers.append(
                Blocker(
                    id="node-missing",
                    title="Node.js is not installed (or not on PATH)",
                    severity="critical",
                    evidence="Agent did not detect a node executable",
                    fix=_node_fix(need),
                )
            )
            return 0
        if need and not _node_satisfies(have, need, req.runtime_constraint):
            blockers.append(
                Blocker(
                    id="node-mismatch",
                    title=f"Node {need} required, this machine has {have}",
                    severity="critical",
                    evidence=f"Repo wants {req.runtime_constraint or need}; agent reported {fp.node}",
                    fix=_node_fix(need),
                )
            )
            return 0
        return RUNTIME_WEIGHT

    # Other runtimes: just check the binary exists if we know the name
    tool = req.runtime
    present = tool in (fp.tools or []) or getattr(fp, tool, None)
    if present:
        return RUNTIME_WEIGHT
    blockers.append(
        Blocker(
            id=f"{tool}-missing",
            title=f"{tool} is required but was not found on this machine",
            severity="critical",
            evidence=f"Inferred runtime: {tool}",
            fix=f"Install {tool} and ensure it is on PATH, then rerun the agent",
        )
    )
    return 0


def _score_tooling(req: Requirements, fp: Fingerprint, blockers: list[Blocker]) -> float:
    pts = TOOLING_WEIGHT
    tools = set(fp.tools or [])
    if not fp.git and "git" not in tools:
        blockers.append(
            Blocker(
                id="git-missing",
                title="git is not installed",
                severity="critical",
                evidence="The agent needs git to clone the repo into a sandbox",
                fix="Install Git (https://git-scm.com) and reopen the terminal",
            )
        )
        pts -= 8
    pm = req.package_manager
    if pm in {"pip", "poetry", "pipenv"} and pm == "pip" and not fp.pip:
        blockers.append(
            Blocker(
                id="pip-missing",
                title="pip is not available",
                severity="warning",
                evidence="Python was found without pip",
                fix="python -m ensurepip --upgrade",
            )
        )
        pts -= 5
    if pm in {"npm", "yarn", "pnpm"}:
        # Node present ⇒ npm is treated as available (Windows .cmd false-negatives).
        npm_ok = bool(fp.npm) or "npm" in tools or bool(fp.node)
        if pm == "npm" and not npm_ok:
            blockers.append(
                Blocker(
                    id="npm-missing",
                    title="npm is not available",
                    severity="critical",
                    evidence="Node project but neither node nor npm was detected on PATH",
                    fix="Install Node.js from https://nodejs.org (includes npm), then click Rescan",
                )
            )
            pts -= 8
        if pm == "yarn" and "yarn" not in tools and not fp.node:
            blockers.append(
                Blocker(
                    id="yarn-missing",
                    title="yarn is required by this repo",
                    severity="warning",
                    evidence="yarn.lock present",
                    fix="npm install -g yarn   (or corepack enable)",
                )
            )
            pts -= 5
        if pm == "pnpm" and "pnpm" not in tools:
            blockers.append(
                Blocker(
                    id="pnpm-missing",
                    title="pnpm is required by this repo",
                    severity="warning",
                    evidence="pnpm-lock.yaml present",
                    fix="npm install -g pnpm   (or corepack enable)",
                )
            )
            pts -= 5
    if "docker" in (req.services or []) or req.package_manager == "docker":
        if not fp.docker and "docker" not in tools:
            blockers.append(
                Blocker(
                    id="docker-missing",
                    title="Docker is not installed",
                    severity="warning",
                    evidence="Repo looks like it expects containers",
                    fix="Install Docker Desktop and confirm `docker info` works",
                )
            )
            pts -= 4
    return max(0, pts)


def _score_env(req: Requirements, fp: Fingerprint, blockers: list[Blocker]) -> float:
    required = [e for e in (req.env_vars or []) if e]
    if not required:
        for svc in req.services or []:
            if svc in {"postgres", "redis", "mongodb", "mysql"} and svc not in (fp.services_running or []):
                blockers.append(
                    Blocker(
                        id=f"svc-{svc}",
                        title=f"{svc} does not appear to be running locally",
                        severity="warning",
                        evidence=f"Repo depends on {svc}; default port was closed",
                        fix=_service_fix(svc),
                    )
                )
        if any(b.id.startswith("svc-") for b in blockers):
            missing_n = len([b for b in blockers if b.id.startswith("svc-")])
            ratio = max(0.0, 1 - missing_n / 3)
            return ENV_WEIGHT * ratio
        return ENV_WEIGHT

    missing = list(fp.env_vars_missing or [])
    if not missing and fp.env_vars_present:
        present = set(fp.env_vars_present)
        missing = [e for e in required if e not in present]
    # One config card — never spam N env warnings (that is not "install software").
    if missing:
        shown = ", ".join(missing[:6])
        if len(missing) > 6:
            shown += f" (+{len(missing) - 6} more)"
        blockers.append(
            Blocker(
                id="config-env",
                title=f"Set {len(missing)} env var{'s' if len(missing) != 1 else ''} before the app can boot",
                severity="warning",
                evidence=f"Missing on this machine: {shown}",
                fix="Copy .env.example → .env (or create .env) and fill real values. This is config, not a software install.",
            )
        )
    for svc in req.services or []:
        if svc in {"postgres", "redis", "mongodb", "mysql"} and svc not in (fp.services_running or []):
            blockers.append(
                Blocker(
                    id=f"svc-{svc}",
                    title=f"Install/start local {svc} — required by this repo",
                    severity="warning",
                    evidence=f"Repo depends on {svc}; default port was closed on this PC",
                    fix=_service_fix(svc),
                )
            )
    if not missing and not any(b.id.startswith("svc-") for b in blockers):
        return ENV_WEIGHT
    svc_n = len([b for b in blockers if b.id.startswith("svc-")])
    penalty = min(ENV_WEIGHT, (6 if missing else 0) + 4 * svc_n)
    return max(0, ENV_WEIGHT - penalty)


def _score_install(
    req: Requirements,
    ins: InstallResult,
    blockers: list[Blocker],
    next_steps: list[Blocker],
) -> float:
    if not req.install_command:
        return INSTALL_WEIGHT
    if not ins.attempted:
        next_steps.append(
            Blocker(
                id="install-unverified",
                title="Prove deps install (optional)",
                severity="info",
                evidence="Your PC already has the right runtime/tools — click only if you want to confirm package install works",
                fix=req.install_command or "Run the repo install command in a sandbox",
            )
        )
        return 0
    if ins.ok:
        return INSTALL_WEIGHT
    blockers.append(
        Blocker(
            id="install-failed",
            title="Sandboxed install failed",
            severity="critical",
            evidence=(ins.diagnosis or (ins.logs or "")[-400] or "non-zero exit"),
            fix=ins.diagnosis or "See the install log and match the required runtime version",
        )
    )
    return 0


def _score_boot(
    req: Requirements,
    ins: InstallResult,
    bt: BootResult,
    blockers: list[Blocker],
    next_steps: list[Blocker],
) -> float:
    if not req.start_command:
        return BOOT_WEIGHT
    if ins.attempted and ins.ok is False:
        return 0
    if not bt.attempted:
        next_steps.append(
            Blocker(
                id="boot-unverified",
                title="Prove the app boots (optional)",
                severity="info",
                evidence="Software looks compatible — click Boot only to confirm the start command stays healthy",
                fix=req.start_command or "Start the app briefly and probe the health port",
            )
        )
        return 0
    if bt.ok:
        return BOOT_WEIGHT
    blockers.append(
        Blocker(
            id="boot-failed",
            title="App did not become healthy after start",
            severity="critical",
            evidence=bt.health or (bt.logs or "")[-400] or f"port {bt.port} did not respond",
            fix="Check the start command, required env vars, and that the health port is free",
        )
    )
    return 0


def _summary(
    percent: int,
    blockers: list[Blocker],
    next_steps: list[Blocker],
    req: Requirements,
    fp: Fingerprint,
) -> str:
    software = [
        b
        for b in blockers
        if b.severity in {"critical", "warning"}
        and not b.id.startswith("env-")
        and b.id != "config-env"
    ]
    top = next((b for b in software if b.severity == "critical"), None) or next(
        (b for b in software if b.severity == "warning"), None
    )
    if top:
        return f"{percent}% ready — install/fix: {top.title}"
    config = next((b for b in blockers if b.id == "config-env"), None)
    if config:
        return f"{percent}% ready — software OK; still need env/config"
    if percent >= 90:
        return f"{percent}% ready — this PC matches what the repo needs"
    if next_steps:
        return f"{percent}% ready — software matches; optional install/boot proof left"
    runtime = req.runtime or "runtime"
    have = fp.python if req.runtime == "python" else fp.node if req.runtime == "node" else None
    if have:
        return f"{percent}% ready — {runtime} {have} on this PC looks compatible"
    return f"{percent}% ready — {runtime} looks compatible on this machine"


def _python_fix(need: str | None) -> str:
    ver = need or "3.11"
    return (
        f"Install Python {ver} (https://www.python.org/downloads/ or `pyenv install {ver}`) "
        f"and rerun: py -{ver} agent/setup_check.py <session> --api <url>"
    )


def _node_fix(need: str | None) -> str:
    ver = need or "18"
    return f"Install Node {ver} (`nvm install {ver}` or https://nodejs.org) and rerun the agent"


def _service_fix(svc: str) -> str:
    images = {
        "postgres": "docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16",
        "redis": "docker run -d -p 6379:6379 redis:7",
        "mongodb": "docker run -d -p 27017:27017 mongo:7",
        "mysql": "docker run -d -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root mysql:8",
    }
    return images.get(svc, f"Start a local {svc} instance (Docker is the fastest path)")


def _semver_head(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if not m:
        return None
    major, minor, patch = m.group(1), m.group(2) or "0", m.group(3) or "0"
    return f"{major}.{minor}.{patch}"


def _python_satisfies(have: str, need: str, constraint: str | None) -> bool:
    hv = _tuple(have)
    nv = _tuple(need)
    if not hv or not nv:
        return True
    c = constraint or ""
    if "<" in c:
        ok = True
        for bound in re.findall(r">=\s*(\d+(?:\.\d+)?)", c):
            t = _tuple(bound)
            ok = ok and t is not None and hv[:2] >= t[:2]
        for bound in re.findall(r"<=\s*(\d+(?:\.\d+)?)", c):
            t = _tuple(bound)
            ok = ok and t is not None and hv[:2] <= t[:2]
        for bound in re.findall(r"<(?!=)\s*(\d+(?:\.\d+)?)", c):
            t = _tuple(bound)
            ok = ok and t is not None and hv[:2] < t[:2]
        return ok
    if ">=" in c or c.strip().startswith(">"):
        return hv[:2] >= nv[:2]
    if "==" in c:
        return hv[:2] == nv[:2]
    # Declared 3.11 vs installed 3.13 is a mismatch (the live-demo case).
    return hv[0] == nv[0] and hv[1] == nv[1]


def _node_satisfies(have: str, need: str, constraint: str | None) -> bool:
    """For readiness: newer Node is OK. Fail only when installed major is below the minimum."""
    hv = _tuple(have)
    nv = _tuple(need)
    if not hv:
        return False
    if not nv:
        return True
    c = (constraint or need or "").strip()
    # Explicit upper bound (e.g. >=18 <20)
    if "<" in c and ">=" in c:
        m = re.search(r">=\s*(\d+)(?:\.(\d+))?", c)
        upper = re.search(r"<\s*=?\s*(\d+)(?:\.(\d+))?", c)
        if m and upper:
            min_v = (int(m.group(1)), int(m.group(2) or 0))
            uv = (int(upper.group(1)), int(upper.group(2) or 0))
            if "=" in (upper.group(0) or ""):
                return hv[:2] >= min_v and hv[:2] <= uv
            return hv[:2] >= min_v and hv[0] < uv[0]
    # ">=18", "^18", "~18", "18", "20.x", engines.node soft pin → minimum major
    return hv[0] >= nv[0]


def _tuple(ver: str) -> tuple[int, int, int] | None:
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", ver)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
