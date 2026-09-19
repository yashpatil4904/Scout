from __future__ import annotations

import re

from . import bedrock
from .models import Fingerprint, InstallResult, Requirements


def diagnose_install(
    logs: str,
    requirements: Requirements | None,
    fingerprint: Fingerprint | None,
) -> str:
    heuristic = heuristic_diagnosis(logs, requirements, fingerprint)
    ai = bedrock.invoke_json(
        system=(
            "You are DiagnoseAgent. You diagnose failed software installs. Return ONLY JSON: "
            '{"cause": "...", "fix": "..."}. One sentence each. '
            "Be specific about version mismatches. Do not dump the stack trace."
        ),
        user=(
            f"Requirements: runtime={getattr(requirements, 'runtime', None)} "
            f"{getattr(requirements, 'runtime_constraint', None)} "
            f"install={getattr(requirements, 'install_command', None)}\n"
            f"Machine: os={getattr(fingerprint, 'os', None)} "
            f"python={getattr(fingerprint, 'python', None)} "
            f"node={getattr(fingerprint, 'node', None)}\n"
            f"Install logs (truncated):\n{(logs or '')[-6000:]}"
        ),
    )
    if ai and (ai.get("cause") or ai.get("fix")):
        cause = str(ai.get("cause") or "").strip()
        fix = str(ai.get("fix") or "").strip()
        parts = [p for p in (cause, f"Fix: {fix}" if fix else "") if p]
        return " ".join(parts)
    return heuristic


def heuristic_diagnosis(
    logs: str,
    requirements: Requirements | None,
    fingerprint: Fingerprint | None,
) -> str:
    text = logs or ""
    low = text.lower()
    need_py = getattr(requirements, "runtime_version", None) if requirements else None
    have_py = getattr(fingerprint, "python", None) if fingerprint else None
    need_node = (
        requirements.runtime_version
        if requirements and requirements.runtime == "node"
        else None
    )
    have_node = getattr(fingerprint, "node", None) if fingerprint else None

    if re.search(r"requires-python|python( version)? (>=|==|~=)|unsupported python", low):
        return (
            f"The install expects Python {need_py or 'a different version'} "
            f"but this machine has {have_py or 'an unknown version'}. "
            f"Fix: install Python {need_py or 'the version in the repo'} (pyenv or python.org) "
            "and rerun the check with that interpreter."
        )
    if "could not find a version that satisfies" in low or "no matching distribution" in low:
        pkg = _extract(r"no matching distribution found for ([^\s]+)", text) or "a package"
        return (
            f"pip could not find a wheel for {pkg} that matches this Python/OS. "
            "Fix: use the repo's required Python version, or install build tools "
            "(Visual C++ Build Tools on Windows, python3-dev on Linux)."
        )
    if "microsoft visual c++" in low or "error: microsoft visual" in low:
        return (
            "A Python package needs a C compiler. "
            "Fix: install Microsoft C++ Build Tools, then retry in a fresh venv."
        )
    if "error: command 'gcc' failed" in low or "command 'clang' failed" in low:
        return (
            "A native extension failed to compile. "
            "Fix: install gcc/make/python headers (build-essential + python3-dev) and retry."
        )
    if "ebadengine" in low or "engine \"node\"" in low or "not satisfying" in low:
        return (
            f"npm refuses to install because Node {have_node or '(unknown)'} "
            f"does not satisfy {need_node or 'the repo engines.node range'}. "
            f"Fix: install Node {need_node or 'the version in package.json engines'} with nvm."
        )
    if "npm err! code enoent" in low and "package.json" in low:
        return (
            "npm ran in a folder without package.json. "
            "Fix: confirm the clone succeeded and rerun from the repo root."
        )
    if "err_os" in low or "unsupported platform" in low:
        return (
            "A dependency does not publish binaries for this OS/architecture. "
            "Fix: use WSL/Linux, or swap the package for one that supports your platform."
        )
    if "modulenotfounderror" in low or "cannot find module" in low:
        missing = _extract(r"no module named ['\"]?([^'\"\s]+)", text) or _extract(
            r"cannot find module ['\"]([^'\"]+)", text
        )
        return (
            f"A required module is missing ({missing or 'unknown'}). "
            "Fix: rerun the sandboxed install, or add the package to requirements/package.json."
        )
    if "command not found" in low or "is not recognized as an internal or external command" in low:
        cmd = _extract(r"([\w.-]+)(?:\.exe)?: command not found", text) or "the install tool"
        return (
            f"{cmd} is not installed on this machine. "
            "Fix: install that toolchain and ensure it is on PATH."
        )
    if "failed to resolve" in low or "network" in low or "timed out" in low or "403 forbidden" in low:
        return (
            "The package registry could not be reached. "
            "Fix: check network/VPN/proxy, then retry the install."
        )
    if "git" in low and ("not found" in low or "not recognized" in low):
        return "git is not installed. Fix: install Git and rerun the agent so it can clone the repo."
    if not text.strip():
        return (
            "Install failed with no captured output. "
            "Fix: run the install command manually in a temp folder and compare versions."
        )
    preview = " ".join(text.strip().splitlines()[-4:])[:280]
    return (
        f"Install failed. Last output: {preview}. "
        "Fix: match the repo runtime version and retry in a fresh sandbox."
    )


def _extract(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.I)
    return m.group(1) if m else None


def polish_blockers(summary: str, blockers: list[dict]) -> tuple[str, list[dict]]:
    """Optional Bedrock rewrite of blocker titles/fixes. Percent is never changed here."""
    if not blockers:
        return summary, blockers
    result = bedrock.invoke_json(
        system=(
            "Rewrite setup-readiness blockers in plain English for a student. "
            "Return JSON: {\"summary\": str, \"blockers\": [{\"id\", \"title\", \"severity\", "
            "\"evidence\", \"fix\"}]}. Keep the same ids and severities. Do not invent new blockers. "
            "Each fix must be a concrete command or one-step action."
        ),
        user=f"Summary: {summary}\nBlockers: {blockers}",
    )
    if not result:
        return summary, blockers
    new_summary = result.get("summary") or summary
    incoming = result.get("blockers")
    if not isinstance(incoming, list):
        return new_summary, blockers
    by_id = {b.get("id"): b for b in incoming if isinstance(b, dict)}
    polished = []
    for original in blockers:
        upd = by_id.get(original["id"], {})
        merged = {
            **original,
            "title": upd.get("title") or original["title"],
            "fix": upd.get("fix") or original["fix"],
            "evidence": upd.get("evidence") or original["evidence"],
            "severity": original["severity"],
            "id": original["id"],
        }
        polished.append(merged)
    return new_summary, polished
