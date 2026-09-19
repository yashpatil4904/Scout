from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.diagnose import heuristic_diagnosis
from shared.github import parse_repo_url
from shared.infer import _heuristic_from_paths
from shared.models import BootResult, Fingerprint, InstallResult, Requirements
from shared.parse import parse_manifests
from shared.score import compute_score, _node_satisfies


def test_github_contents_path_encodes_spaces():
    import urllib.parse

    path = "backend/Ai DIy/app.py"
    encoded = urllib.parse.quote(path, safe="/")
    assert " " not in encoded
    assert "Ai%20DIy" in encoded


def test_parse_github_urls():
    assert parse_repo_url("https://github.com/pallets/flask") == ("pallets", "flask")
    assert parse_repo_url("https://github.com/pallets/flask.git") == ("pallets", "flask")
    assert parse_repo_url("pallets/flask") == ("pallets", "flask")
    assert parse_repo_url("https://github.com/pallets/flask") == ("pallets", "flask")
    assert parse_repo_url("https://github.com/pallets/flask.git") == ("pallets", "flask")
    assert parse_repo_url("pallets/flask") == ("pallets", "flask")


def test_parse_requirements_txt_flask():
    req = parse_manifests(
        {
            "requirements.txt": "flask==3.0.0\nredis==5.0.0\n",
            ".env.example": "SECRET_KEY=\nDATABASE_URL=postgres://\n",
        }
    )
    assert req.runtime == "python"
    assert req.package_manager == "pip"
    assert "redis" in req.services
    assert "SECRET_KEY" in req.env_vars
    assert req.install_command.startswith("pip")


def test_nested_package_json_does_not_override_python_root():
    req = parse_manifests(
        {
            "requirements.txt": "flask==3.0.0\n",
            "frontend/package.json": '{"engines":{"node":"20"},"scripts":{"start":"vite"},"dependencies":{"vite":"5"}}',
        }
    )
    assert req.runtime == "python"
    assert req.package_manager == "pip"
    assert req.runtime_version != "20"


def test_parse_package_json_engines_soft_minimum():
    req = parse_manifests(
        {
            "package.json": '{"engines":{"node":"20"},"scripts":{"start":"node server.js"},"dependencies":{"express":"4.18.0"}}'
        }
    )
    assert req.runtime == "node"
    assert req.runtime_version == "20"
    assert req.start_command == "npm start"


def test_library_package_json_has_no_fake_start():
    req = parse_manifests(
        {
            "package.json": '{"name":"lib","engines":{"node":">=18"},"dependencies":{"lodash":"4"}}'
        }
    )
    assert req.runtime == "node"
    assert req.start_command is None


def test_fingerprint_only_does_not_list_install_as_failure():
    score = compute_score(
        Requirements(runtime="python", runtime_version="3.13", package_manager="pip", install_command="pip install -r requirements.txt"),
        Fingerprint(os="Windows", arch="AMD64", python="3.13.5", pip="24.0", git="git version 2.45"),
        InstallResult(attempted=False),
        BootResult(attempted=False),
    )
    assert not any(b.id == "install-unverified" and b.severity == "critical" for b in score.blockers)
    # optional next step may exist as info
    assert all(b.severity != "critical" for b in score.blockers if b.id in {"install-unverified", "boot-unverified"})


def test_python_mismatch_is_critical():
    score = compute_score(
        Requirements(runtime="python", runtime_version="3.11", runtime_constraint=">=3.11,<3.12"),
        Fingerprint(os="Windows", python="3.13.1", git="git version 2"),
        InstallResult(attempted=False),
        BootResult(attempted=False),
    )
    assert any(b.id == "python-mismatch" and b.severity == "critical" for b in score.blockers)


def test_ready_requires_boot_only_when_start_exists():
    req = Requirements(
        runtime="python",
        runtime_version="3.13",
        package_manager="pip",
        install_command="pip install -r requirements.txt",
        start_command="flask run",
    )
    fp = Fingerprint(os="Windows", python="3.13.1", pip="24", git="git version 2")
    installed = InstallResult(attempted=True, ok=True, command="pip install -r requirements.txt")
    score_install_only = compute_score(req, fp, installed, BootResult(attempted=False))
    assert score_install_only.percent < 90

    score_booted = compute_score(
        req,
        fp,
        installed,
        BootResult(attempted=True, ok=True, port=8000, health="HTTP 200"),
    )
    assert score_booted.percent >= 90


def test_library_gets_boot_points_without_start():
    score = compute_score(
        Requirements(runtime="node", runtime_version="18", package_manager="npm", install_command="npm install"),
        Fingerprint(os="Windows", node="v24.19.0", npm="11.0.0", git="git version 2", tools=["npm", "git"]),
        InstallResult(attempted=True, ok=True),
        BootResult(attempted=False),
    )
    assert score.percent >= 90
    assert not any(b.id == "boot-unverified" for b in score.blockers)


def test_infer_python_from_source_paths():
    req = _heuristic_from_paths(Requirements(), ["app.py", "utils.py", "templates/home.html"])
    assert req.runtime == "python"
    assert req.language == "python"


def test_diagnose_python_version_error():
    text = heuristic_diagnosis(
        "ERROR: Package 'foo' requires-python >=3.11,<3.12\nUnsupported Python version",
        Requirements(runtime="python", runtime_version="3.11"),
        Fingerprint(python="3.13.1"),
    )
    assert "3.11" in text
    assert "3.13" in text


def test_node_newer_major_is_ok():
    assert _node_satisfies("24.19.0", "20", "20")
    assert _node_satisfies("24.19.0", "18", ">=18")
    assert not _node_satisfies("16.0.0", "18", ">=18")


def test_npm_not_flagged_when_node_present():
    score = compute_score(
        Requirements(runtime="node", package_manager="npm", runtime_version="20", runtime_constraint="20"),
        Fingerprint(os="Windows", node="v24.19.0", npm=None, git="git version 2", tools=["git"]),
        InstallResult(attempted=False),
        BootResult(attempted=False),
    )
    assert not any(b.id == "npm-missing" for b in score.blockers)
    assert not any(b.id == "node-mismatch" for b in score.blockers)


def test_analyst_does_not_flip_python_root_to_node():
    from shared.agents import apply_analyst_result

    req = Requirements(
        runtime="python",
        language="python",
        package_manager="pip",
        manifests_found=["requirements.txt", "frontend/package.json"],
        start_command="flask run",
    )
    out = apply_analyst_result(
        req,
        {
            "runtime": "node",
            "language": "javascript",
            "package_manager": "npm",
            "start_command": "npm start",
            "runnable": True,
        },
    )
    assert out.runtime == "python"
    assert out.start_command == "flask run"
    assert out.package_manager == "pip"


def test_analyst_clears_start_when_not_runnable():
    from shared.agents import apply_analyst_result

    req = Requirements(runtime="node", start_command="npm start", health_port=3000)
    out = apply_analyst_result(req, {"runnable": False, "runtime": "node"})
    assert out.start_command is None
    assert out.health_port is None


def test_score_llm_names_missing_python_and_rejects_npm_on_python_repo():
    from shared.agents import apply_score_llm
    from shared.models import Score

    baseline = Score(percent=80, summary="heuristic", blockers=[], engine="heuristic")
    req = Requirements(runtime="python", runtime_version="3.11", runtime_constraint=">=3.11,<3.12")
    fp = Fingerprint(os="Windows", python="3.13.1", node="v24.19.0", git="git version 2")
    out = apply_score_llm(
        baseline,
        {
            "percent": 64,
            "summary": "Your machine is 64% ready — Python 3.11 is missing.",
            "issues": [
                {
                    "id": "python-mismatch",
                    "title": "Need Python 3.11, you have 3.13",
                    "severity": "critical",
                    "evidence": "fingerprint.python=3.13.1",
                    "fix": "Install Python 3.11 from python.org and reopen the terminal",
                },
                {
                    "id": "npm-missing",
                    "title": "npm missing",
                    "severity": "critical",
                    "evidence": "should be dropped",
                    "fix": "ignore",
                },
            ],
        },
        req,
        fp,
        InstallResult(attempted=False),
        BootResult(attempted=False),
    )
    assert out.engine in {"bedrock", "groq", "heuristic"}
    assert any(b.id == "python-mismatch" for b in out.blockers)
    assert not any(b.id == "npm-missing" for b in out.blockers)
    assert "64%" in out.summary or "Python" in out.summary


def test_env_vars_collapse_to_one_blocker():
    score = compute_score(
        Requirements(
            runtime="node",
            runtime_version="20",
            package_manager="npm",
            install_command="npm install",
            start_command="npm run dev",
            env_vars=["VITE_API_URL", "VITE_SUPABASE_URL", "VITE_SUPABASE_ANON_KEY", "DATABASE_URL"],
        ),
        Fingerprint(
            os="Windows",
            node="v24.19.0",
            npm="11.0.0",
            git="git version 2",
            tools=["npm", "git"],
            env_vars_missing=["VITE_API_URL", "VITE_SUPABASE_URL", "VITE_SUPABASE_ANON_KEY", "DATABASE_URL"],
        ),
        InstallResult(attempted=False),
        BootResult(attempted=False),
    )
    env_ids = [b.id for b in score.blockers if b.id.startswith("env-") or b.id == "config-env"]
    assert env_ids == ["config-env"]
    assert not any(b.id.startswith("env-") for b in score.blockers)


def test_future_crash_timeline_orders_runtime_before_packages():
    from shared.crash_preview import build_crash_preview

    preview = build_crash_preview(
        Requirements(
            runtime="python",
            runtime_version="3.11",
            runtime_constraint=">=3.11,<3.12",
            packages=["sklearn", "pandas"],
            start_command="python app.py",
            manifests_found=[],
        ),
        Fingerprint(os="Windows", python="3.13.1", git="git version 2"),
        InstallResult(attempted=False),
        BootResult(attempted=False),
        probe={"imports": {"sklearn": False, "pandas": False}},
    )
    assert preview.frames
    assert preview.frames[0].status == "fail"
    assert "Python" in preview.frames[0].title
    # Later package crashes are skipped because runtime fails first
    assert any(f.status == "skip" for f in preview.frames)
    assert "failure" in preview.summary.lower() or "crash" in preview.summary.lower()


def test_future_crash_predicts_missing_module_without_manifest():
    from shared.crash_preview import build_crash_preview, extract_packages_from_snippets

    pkgs = extract_packages_from_snippets(
        {"app.py": "import flask\nfrom flask_sqlalchemy import SQLAlchemy\nimport sklearn\n"},
        "python",
    )
    assert "flask" in pkgs
    assert "Flask-SQLAlchemy" in pkgs or "flask_sqlalchemy" in pkgs
    assert "scikit-learn" in pkgs

    preview = build_crash_preview(
        Requirements(runtime="python", packages=["sklearn"], manifests_found=[], start_command="python app.py"),
        Fingerprint(os="Windows", python="3.12.0", git="git"),
        InstallResult(attempted=False),
        BootResult(attempted=False),
        probe={"imports": {"sklearn": False}},
    )
    fails = [f for f in preview.frames if f.status == "fail"]
    assert fails
    assert "ModuleNotFoundError" in (fails[0].would_see or "")
