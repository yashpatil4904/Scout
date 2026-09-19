from __future__ import annotations

import json

from . import bedrock
from .models import Blocker, BootResult, Fingerprint, InstallResult, Requirements, Score, to_dict
from .score import _node_satisfies, _python_satisfies, clamp_percent, compute_score


REPO_ANALYST_SYSTEM = """You are RepoAnalystAgent for a setup-readiness checker.
Figure out what SOFTWARE a developer must have on their laptop to run this repository.

Return ONLY JSON with these keys:
- language, runtime, runtime_version, runtime_constraint
- package_manager, install_command, start_command
- health_path, health_port (number or null)
- env_vars (array of ONLY boot-critical secrets, max 5), services (array), notes (array)
- packages (array of inferred top-level deps if no lockfile/requirements — e.g. flask, sklearn)
- runnable (boolean): true only if this is an app you can boot
- confidence (0-1)

Rules:
- Prefer the ROOT stack. Nested frontend/package.json must NOT override a Python/Java/Go root.
- engines.node / .nvmrc are MINIMUM versions (constraint ">=N"), not exact pins.
- Infer runtime version from imports/syntax when manifests omit it (old React → Node 16 hint in notes, etc.).
- Do not invent npm start / python main.py unless the repo has that entrypoint or script.
- env_vars: ONLY names that would crash boot if missing (DATABASE_URL, SECRET_KEY). Omit Vite public keys unless clearly required.
- Never claim Docker is required just because a Dockerfile exists alongside a normal local run path.
"""

BLOCKER_AUDITOR_SYSTEM = """You are BlockerAuditorAgent.
You audit setup-readiness blockers against the machine fingerprint.

Return ONLY JSON:
{"drop_ids": ["id", ...], "summary": "optional rewritten one-line summary"}

Drop a blocker ONLY when it is factually wrong, for example:
- npm-missing when node is installed (npm.cmd on Windows is still npm)
- node-mismatch when installed Node major >= required minimum
- boot-unverified / install-unverified when they are optional next steps, not failures
- claiming the wrong language (Node on a Python repo)
- duplicate env-* ids when config-env already covers them

Never drop python-mismatch / node-mismatch / git-missing / docker-missing / svc-* if the fingerprint actually fails that check.
Do not invent new ids.
"""


SCORE_AGENT_SYSTEM = """You are ScoreAgent for a laptop vs repository readiness checker.

Your ONLY job: answer "Can THIS PC run THIS repo, and what SOFTWARE must the student install?"

Return ONLY JSON:
{
  "percent": 0-100,
  "summary": "one sentence: Your PC is N% ready — <what to install or change>",
  "issues": [
    {"id": "short-id", "title": "Install/fix X", "severity": "critical|warning|info",
     "evidence": "repo needs … ; this PC has …", "fix": "exact install command for fingerprint.os"}
  ]
}

PRIORITY ORDER for issues (must follow):
1. Missing or wrong RUNTIME (Python/Node/Go version) — severity critical
2. Missing PACKAGE MANAGER / git / docker when required — critical or warning
3. Local SERVICES not installed/running (postgres, redis, …) — warning
4. ONE config issue max for env vars (id: config-env) — warning, NEVER one card per env var
5. install-unverified / boot-unverified — severity info ONLY, and only if software already matches

Hard rules:
- Compare fingerprint fields (python, node, npm, git, docker, tools, ram_mb, os) to requirements.
- Every evidence line MUST state both sides: "repo needs X; this PC has Y".
- Fixes must be installable software steps on fingerprint.os (Windows/macOS/Linux), e.g. nvm/pyenv/winget/choco/python.org.
- Node: installed major >= required minimum is OK (Node 24 satisfies engines 20).
- If node is installed, do not flag npm-missing.
- Nested frontend/package.json on a Python repo is not a Node requirement.
- Do NOT flood issues with VITE_* / public anon keys — at most one config-env card listing names.
- Do NOT make env vars the main story when runtime/tooling already matches — say software is OK.
- Never give >=90 if start_command exists and boot was not confirmed alive.
- Never give 100 on fingerprint-only.
- percent weighting hint: runtime ~50, tooling ~25, env/services ~10, optional install/boot ~15.
"""

# Heuristic software gaps the LLM must not erase.
_SOFTWARE_PREFIXES = (
    "python-",
    "node-",
    "git-",
    "npm-",
    "pip-",
    "yarn-",
    "pnpm-",
    "docker-",
    "runtime-",
    "svc-",
)


def score_machine(
    req: Requirements | None,
    fp: Fingerprint | None,
    ins: InstallResult | None,
    bt: BootResult | None,
) -> Score:
    """Heuristic baseline, then LLM ScoreAgent explains what this PC is missing."""
    baseline = compute_score(req, fp, ins, bt, polish=False)
    data = bedrock.invoke_json(
        system=SCORE_AGENT_SYSTEM,
        user=(
            "TASK: Compare THIS LAPTOP to THIS REPO. List software to install first.\n\n"
            "Repository requirements:\n"
            f"{json.dumps(to_dict(req) if req else {}, indent=2, default=str)}\n\n"
            "This laptop fingerprint (NOT the cloud host):\n"
            f"{json.dumps(to_dict(fp) if fp else {}, indent=2, default=str)}\n\n"
            "Install result:\n"
            f"{json.dumps(to_dict(ins) if ins else {}, indent=2, default=str)}\n\n"
            "Boot result:\n"
            f"{json.dumps(to_dict(bt) if bt else {}, indent=2, default=str)}\n\n"
            "Heuristic baseline (keep real software gaps; rewrite vague env spam):\n"
            f"{json.dumps(to_dict(baseline), indent=2, default=str)}"
        ),
    )
    scored = apply_score_llm(baseline, data, req, fp, ins, bt)
    return audit_score(scored, req, fp)


def apply_score_llm(
    baseline: Score,
    data: dict | None,
    req: Requirements | None,
    fp: Fingerprint | None,
    ins: InstallResult | None,
    bt: BootResult | None,
) -> Score:
    if not data:
        return _collapse_env_blockers(baseline)
    req = req or Requirements()
    fp = fp or Fingerprint()
    ins = ins or InstallResult()
    bt = bt or BootResult()

    raw_issues = data.get("issues") if isinstance(data.get("issues"), list) else data.get("blockers")
    blockers: list[Blocker] = []
    if isinstance(raw_issues, list) and raw_issues:
        for i, item in enumerate(raw_issues):
            if not isinstance(item, dict):
                continue
            bid = str(item.get("id") or f"issue-{i}")
            severity = str(item.get("severity") or "warning").lower()
            if severity not in {"critical", "warning", "info"}:
                severity = "warning"
            if bid in {"install-unverified", "boot-unverified"} or bid.startswith("env-"):
                if bid.startswith("env-"):
                    continue  # collapsed below
                severity = "info"
            if bid == "config-env":
                severity = "warning"
            if _reject_llm_issue(bid, req, fp):
                continue
            blockers.append(
                Blocker(
                    id=bid,
                    title=str(item.get("title") or bid),
                    severity=severity,
                    evidence=str(item.get("evidence") or ""),
                    fix=str(item.get("fix") or ""),
                )
            )
    else:
        blockers = list(baseline.blockers)

    blockers = _merge_software_gaps(baseline.blockers, blockers)
    blockers = _collapse_env_list(blockers, req, fp)

    percent = data.get("percent")
    try:
        percent = int(percent)
    except (TypeError, ValueError):
        percent = baseline.percent
    if any(b.severity == "critical" for b in blockers):
        percent = min(percent, 72)
    percent = clamp_percent(percent, req, ins, bt)
    summary = str(data.get("summary") or baseline.summary)
    blockers.sort(key=lambda b: {"critical": 0, "warning": 1, "info": 2}.get(b.severity, 3))
    return Score(
        percent=percent,
        summary=summary,
        blockers=blockers,
        engine=bedrock.last_provider() or "bedrock",
    )


def _is_software_blocker(bid: str) -> bool:
    return bid.startswith(_SOFTWARE_PREFIXES) or bid in {
        "python-missing",
        "python-mismatch",
        "node-missing",
        "node-mismatch",
        "git-missing",
        "docker-missing",
        "npm-missing",
        "pip-missing",
    }


def _merge_software_gaps(baseline: list[Blocker], llm: list[Blocker]) -> list[Blocker]:
    """Keep heuristic software gaps if the LLM forgot them or only returned env fluff."""
    by_id = {b.id: b for b in llm}
    for b in baseline:
        if not _is_software_blocker(b.id):
            continue
        if b.id not in by_id:
            by_id[b.id] = b
            continue
        # Prefer LLM wording but never lose critical severity from heuristic.
        cur = by_id[b.id]
        if b.severity == "critical" and cur.severity != "critical":
            by_id[b.id] = Blocker(
                id=cur.id,
                title=cur.title or b.title,
                severity="critical",
                evidence=cur.evidence or b.evidence,
                fix=cur.fix or b.fix,
            )
    # If LLM returned only info/config and baseline has software gaps, surface those.
    software_llm = [b for b in by_id.values() if _is_software_blocker(b.id)]
    software_base = [b for b in baseline if _is_software_blocker(b.id)]
    if software_base and not software_llm:
        for b in software_base:
            by_id[b.id] = b
    # Preserve optional next steps from baseline if LLM omitted them.
    for b in baseline:
        if b.id in {"install-unverified", "boot-unverified"} and b.id not in by_id:
            by_id[b.id] = b
    return list(by_id.values())


def _collapse_env_list(blockers: list[Blocker], req: Requirements, fp: Fingerprint) -> list[Blocker]:
    env_items = [b for b in blockers if b.id.startswith("env-") or b.id == "config-env"]
    others = [b for b in blockers if not (b.id.startswith("env-") or b.id == "config-env")]
    if not env_items:
        return blockers
    names: list[str] = []
    for b in env_items:
        if b.id.startswith("env-"):
            names.append(b.id[4:])
        elif "Missing:" in (b.evidence or ""):
            # already collapsed text
            pass
    if not names:
        missing = list(fp.env_vars_missing or [])
        if not missing:
            present = set(fp.env_vars_present or [])
            missing = [e for e in (req.env_vars or []) if e and e not in present]
        names = missing
    if not names and any(b.id == "config-env" for b in env_items):
        # Keep existing config-env as-is
        cfg = next(b for b in env_items if b.id == "config-env")
        return others + [cfg]
    if not names:
        return others
    shown = ", ".join(names[:6])
    if len(names) > 6:
        shown += f" (+{len(names) - 6} more)"
    others.append(
        Blocker(
            id="config-env",
            title=f"Set {len(names)} env var{'s' if len(names) != 1 else ''} before boot",
            severity="warning",
            evidence=f"Repo needs these on this PC; missing: {shown}",
            fix="Copy .env.example → .env and fill values. Config only — not a software install.",
        )
    )
    return others


def _collapse_env_blockers(score: Score) -> Score:
    score.blockers = _collapse_env_list(score.blockers, Requirements(), Fingerprint())
    return score


def _reject_llm_issue(bid: str, req: Requirements, fp: Fingerprint) -> bool:
    if bid == "npm-missing" and (fp.node or fp.npm):
        return True
    if bid == "node-mismatch" and _node_satisfies(fp.node or "", req.runtime_version or "", req.runtime_constraint):
        return True
    if bid == "python-mismatch" and fp.python and req.runtime_version:
        if _python_satisfies(fp.python, req.runtime_version, req.runtime_constraint):
            return True
    if bid in {"node-mismatch", "npm-missing"} and req.runtime == "python":
        return True
    return False


def refine_requirements(
    req: Requirements,
    paths: list[str],
    snippets: dict[str, str],
    origin: str,
) -> Requirements:
    """Always ask the repo analyst after heuristics. Falls back to req if LLM is off."""
    brief = json.dumps(to_dict(req), indent=2)
    tree = "\n".join(paths[:80])
    snippet_blob = "\n\n".join(f"## {p}\n{t[:800]}" for p, t in list(snippets.items())[:6])
    data = bedrock.invoke_json(
        system=REPO_ANALYST_SYSTEM,
        user=(
            f"Origin: {origin}\n"
            f"Heuristic analysis JSON:\n{brief}\n\n"
            f"File listing (truncated):\n{tree}\n\n"
            f"Source snippets:\n{snippet_blob or '(none)'}"
        ),
    )
    return apply_analyst_result(req, data)


def audit_score(score: Score, req: Requirements | None, fp: Fingerprint | None) -> Score:
    """Drop factually-wrong blockers. Percent stays unless we drop a false mismatch."""
    if not score.blockers:
        return score
    # Cheap local cleanup first (no extra LLM call when nothing looks wrong).
    score.blockers = _collapse_env_list(score.blockers, req or Requirements(), fp or Fingerprint())
    payload = [
        {
            "id": b.id,
            "title": b.title,
            "severity": b.severity,
            "evidence": b.evidence,
            "fix": b.fix,
        }
        for b in score.blockers
    ]
    # Skip auditor LLM when free-tier TPM is tight — only call if duplicates/noise likely.
    noisy = sum(1 for b in score.blockers if b.id.startswith("env-")) > 1
    false_npm = any(b.id == "npm-missing" for b in score.blockers) and bool((fp or Fingerprint()).node)
    if not noisy and not false_npm:
        score.blockers.sort(key=lambda b: {"critical": 0, "warning": 1, "info": 2}.get(b.severity, 3))
        return score

    data = bedrock.invoke_json(
        system=BLOCKER_AUDITOR_SYSTEM,
        user=(
            f"Score percent: {score.percent}\nSummary: {score.summary}\n"
            f"Requirements: {json.dumps(to_dict(req) if req else {}, default=str)}\n"
            f"Fingerprint: {json.dumps(to_dict(fp) if fp else {}, default=str)}\n"
            f"Blockers: {json.dumps(payload)}"
        ),
    )
    drop = set()
    if data and isinstance(data.get("drop_ids"), list):
        drop = {str(x) for x in data["drop_ids"] if x}
    drop = _safe_drops(drop, req, fp, {b.id for b in score.blockers})
    if not drop:
        if data and data.get("summary"):
            score.summary = str(data["summary"])
        score.blockers.sort(key=lambda b: {"critical": 0, "warning": 1, "info": 2}.get(b.severity, 3))
        return score
    score.blockers = [b for b in score.blockers if b.id not in drop]
    if data and data.get("summary"):
        score.summary = str(data["summary"])
    score.blockers.sort(key=lambda b: {"critical": 0, "warning": 1, "info": 2}.get(b.severity, 3))
    return score


def apply_analyst_result(req: Requirements, data: dict | None) -> Requirements:
    """Merge analyst JSON into heuristic requirements without flipping the root stack."""
    if not data:
        return req

    locked = _locked_runtime(req)
    proposed_runtime = str(data.get("runtime") or "").lower()
    stack_flip = (
        (locked == "python" and proposed_runtime in {"node", "javascript", "typescript"})
        or (locked == "node" and proposed_runtime in {"python"})
    )
    if stack_flip:
        proposed_runtime = locked or proposed_runtime

    if data.get("language") and not stack_flip:
        req.language = str(data["language"])

    if proposed_runtime in {"python", "node", "go", "java", "rust"} and (not locked or locked == proposed_runtime):
        req.runtime = proposed_runtime

    for field in ("runtime_version", "runtime_constraint", "package_manager", "install_command", "health_path"):
        val = data.get(field)
        if val in (None, "", []):
            continue
        if stack_flip and field in {"package_manager", "install_command"}:
            continue
        setattr(req, field, val)

    if data.get("health_port") not in (None, "") and not stack_flip:
        try:
            req.health_port = int(data["health_port"])
        except (TypeError, ValueError):
            pass

    runnable = data.get("runnable")
    if runnable is False:
        req.start_command = None
        req.health_port = None
        req.health_path = None
    elif data.get("start_command") and not stack_flip:
        req.start_command = str(data["start_command"])

    # Replace env list when analyst returns a tight set; otherwise keep heuristic but cap.
    if isinstance(data.get("env_vars"), list):
        cleaned = [str(x) for x in data["env_vars"] if x][:5]
        if cleaned:
            req.env_vars = sorted(set(cleaned))
        else:
            req.env_vars = _cap_env(req.env_vars)
    else:
        req.env_vars = _cap_env(req.env_vars)

    if isinstance(data.get("services"), list):
        req.services.extend(str(x) for x in data["services"] if x)
        req.services = sorted(set(req.services))
    if isinstance(data.get("notes"), list):
        req.notes.extend(str(x) for x in data["notes"] if x)
    if isinstance(data.get("packages"), list) and data["packages"]:
        pkgs = [str(x) for x in data["packages"] if x]
        req.packages = sorted(set(req.packages or []) | set(pkgs))
        note = f"Inferred packages (no lockfile): {', '.join(pkgs)[:200]}"
        if note not in req.notes:
            req.notes.append(note)

    req.inferred = True
    if "RepoAnalystAgent" not in " ".join(req.notes):
        req.notes.append("RepoAnalystAgent refined this analysis via LLM")
    return req


def _cap_env(names: list[str], limit: int = 8) -> list[str]:
    """Prefer secrets/URLs over public Vite keys when we have too many."""
    uniq = sorted(set(str(x) for x in names if x))
    if len(uniq) <= limit:
        return uniq
    priority = []
    rest = []
    for n in uniq:
        upper = n.upper()
        if any(k in upper for k in ("SECRET", "TOKEN", "PASSWORD", "DATABASE", "URL", "KEY", "URI")):
            if "VITE_" in upper and "ANON" in upper:
                rest.append(n)
            else:
                priority.append(n)
        else:
            rest.append(n)
    return (priority + rest)[:limit]


def _locked_runtime(req: Requirements) -> str | None:
    names = [p.replace("\\", "/").lower() for p in req.manifests_found]
    root = [p for p in names if "/" not in p]
    py_root = any(
        n in root
        for n in (
            "requirements.txt",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "pipfile",
            "environment.yml",
            "environment.yaml",
            "uv.lock",
            "poetry.lock",
        )
    )
    node_root = "package.json" in root
    if py_root and not node_root:
        return "python"
    if node_root and not py_root:
        return "node"
    if req.runtime in {"python", "node"}:
        return req.runtime
    return None


def _safe_drops(
    requested: set[str],
    req: Requirements | None,
    fp: Fingerprint | None,
    present: set[str],
) -> set[str]:
    allowed = set()
    fp = fp or Fingerprint()
    req = req or Requirements()
    for bid in requested & present:
        if bid == "npm-missing" and (fp.node or fp.npm):
            allowed.add(bid)
        elif bid == "node-mismatch" and _node_satisfies(fp.node, req.runtime_version, req.runtime_constraint):
            allowed.add(bid)
        elif bid in {"install-unverified", "boot-unverified"}:
            allowed.add(bid)
        elif bid.startswith("env-") and "config-env" in present:
            allowed.add(bid)
        elif bid == "python-mismatch":
            continue
        elif bid in {"wrong-stack", "node-not-needed"}:
            allowed.add(bid)
    return allowed
