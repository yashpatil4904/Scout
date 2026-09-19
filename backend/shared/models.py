from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


def to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


@dataclass
class Requirements:
    language: str | None = None
    runtime: str | None = None
    runtime_version: str | None = None
    runtime_constraint: str | None = None
    package_manager: str | None = None
    env_vars: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    start_command: str | None = None
    install_command: str | None = None
    health_path: str | None = None
    health_port: int | None = None
    inferred: bool = False
    manifests_found: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source_hints: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Requirements":
        if not data:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Fingerprint:
    os: str = "unknown"
    os_version: str | None = None
    arch: str = "unknown"
    ram_mb: int | None = None
    disk_mb: int | None = None
    python: str | None = None
    node: str | None = None
    pip: str | None = None
    npm: str | None = None
    git: str | None = None
    docker: str | None = None
    tools: list[str] = field(default_factory=list)
    env_vars_present: list[str] = field(default_factory=list)
    env_vars_missing: list[str] = field(default_factory=list)
    services_running: list[str] = field(default_factory=list)
    services_missing: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Fingerprint":
        if not data:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class InstallResult:
    attempted: bool = False
    ok: bool | None = None
    exit_code: int | None = None
    logs: str = ""
    diagnosis: str | None = None
    command: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "InstallResult":
        if not data:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class BootResult:
    attempted: bool = False
    ok: bool | None = None
    port: int | None = None
    health: str | None = None
    logs: str = ""
    command: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "BootResult":
        if not data:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Blocker:
    id: str
    title: str
    severity: str  # critical | warning | info
    evidence: str
    fix: str

    @classmethod
    def from_dict(cls, data: dict) -> "Blocker":
        return cls(
            id=data.get("id", "unknown"),
            title=data.get("title", ""),
            severity=data.get("severity", "info"),
            evidence=data.get("evidence", ""),
            fix=data.get("fix", ""),
        )


@dataclass
class Score:
    percent: int = 0
    summary: str = ""
    blockers: list[Blocker] = field(default_factory=list)
    engine: str = "heuristic"

    @classmethod
    def from_dict(cls, data: dict | None) -> "Score | None":
        if not data:
            return None
        blockers = [Blocker.from_dict(b) for b in data.get("blockers") or []]
        return cls(
            percent=int(data.get("percent") or 0),
            summary=data.get("summary") or "",
            blockers=blockers,
            engine=data.get("engine") or "heuristic",
        )


@dataclass
class AgentCommand:
    posix: str
    windows: str
    local: str


@dataclass
class Session:
    session_id: str
    repo_url: str
    status: str
    owner: str | None = None
    repo: str | None = None
    source: str = "github"  # github | local
    local_path: str | None = None
    requirements: Requirements | None = None
    fingerprint: Fingerprint | None = None
    install: InstallResult = field(default_factory=InstallResult)
    boot: BootResult = field(default_factory=BootResult)
    score: Score | None = None
    crash_preview: dict | None = None
    agent_command: AgentCommand | None = None
    created_at: str | None = None
    updated_at: str | None = None
    error: str | None = None
    # Ephemeral analysis cache (snippets) — may be persisted in local store
    analysis_snippets: dict | None = None

    def to_public_dict(self) -> dict:
        data = to_dict(self)
        # Keep install logs for the dashboard (capped already by the agent).
        # Hide bulky snippet cache from the browser payload.
        data.pop("analysis_snippets", None)
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> "Session | None":
        if not data:
            return None
        cmd = data.get("agent_command")
        agent_command = None
        if cmd:
            agent_command = AgentCommand(
                posix=cmd.get("posix", ""),
                windows=cmd.get("windows", ""),
                local=cmd.get("local", ""),
            )
        return cls(
            session_id=data["session_id"],
            repo_url=data.get("repo_url") or data.get("local_path") or "",
            status=data.get("status", "unknown"),
            owner=data.get("owner"),
            repo=data.get("repo"),
            source=data.get("source") or "github",
            local_path=data.get("local_path"),
            requirements=Requirements.from_dict(data.get("requirements")),
            fingerprint=Fingerprint.from_dict(data.get("fingerprint"))
            if data.get("fingerprint")
            else None,
            install=InstallResult.from_dict(data.get("install")),
            boot=BootResult.from_dict(data.get("boot")),
            score=Score.from_dict(data.get("score")),
            crash_preview=data.get("crash_preview"),
            agent_command=agent_command,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            error=data.get("error"),
            analysis_snippets=data.get("analysis_snippets"),
        )
