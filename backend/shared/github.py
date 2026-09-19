from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

GITHUB_API = "https://api.github.com"
USER_AGENT = "setup-readiness-checker"


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def parse_repo_url(url: str) -> tuple[str, str]:
    raw = url.strip()
    if not raw:
        raise GitHubError("Repository URL is required")
    raw = raw.rstrip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "https://www.github.com/",
        "git@github.com:",
    )
    matched = False
    for prefix in prefixes:
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
            matched = True
            break
    if not matched and raw.count("/") == 1 and "://" not in raw:
        matched = True
    if not matched:
        raise GitHubError("Only public GitHub URLs are supported (https://github.com/owner/repo)")
    parts = raw.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise GitHubError("Could not parse owner/repo from that URL")
    return parts[0], parts[1]


def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get(path: str) -> Any:
    if path.startswith("http"):
        url = path
    else:
        url = f"{GITHUB_API}{path}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code == 404:
            raise GitHubError("Repository not found or private. Public repos only for now.", 404)
        if exc.code == 403:
            raise GitHubError(
                "GitHub API rate limit hit. Set GITHUB_TOKEN on the backend and retry.", 403
            )
        raise GitHubError(f"GitHub API error {exc.code}: {detail}", exc.code)
    except urllib.error.URLError as exc:
        raise GitHubError(f"Could not reach GitHub: {exc.reason}")


def get_repo(owner: str, repo: str) -> dict:
    return github_get(f"/repos/{owner}/{repo}")


def get_tree(owner: str, repo: str, branch: str) -> list[dict]:
    data = github_get(f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    return data.get("tree") or []


def get_file_text(owner: str, repo: str, path: str, ref: str | None = None) -> str | None:
    encoded_path = urllib.parse.quote(path.lstrip("/").replace("\\", "/"), safe="/")
    qs = f"?ref={urllib.parse.quote(ref, safe='')}" if ref else ""
    try:
        data = github_get(f"/repos/{owner}/{repo}/contents/{encoded_path}{qs}")
    except GitHubError as exc:
        if exc.status == 404:
            return None
        raise
    if not isinstance(data, dict) or data.get("type") != "file":
        return None
    encoding = data.get("encoding")
    content = data.get("content") or ""
    if encoding == "base64":
        import base64

        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return None
    if isinstance(content, str):
        return content
    return None
