from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def _load_dotenv() -> None:
    """Load repo-root or backend/.env into os.environ (does not override existing vars)."""
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / ".env",
        here.parents[1] / ".env",
        Path.cwd() / ".env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, _, val = raw.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
        break


_load_dotenv()


_LAST_ERROR: str | None = None
_LAST_PROVIDER: str | None = None
DEFAULT_MODEL = "amazon.nova-lite-v1:0"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-20b"
GROQ_RETIRED_MODELS = {
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "llama3-8b-8192",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
}


def last_error() -> str | None:
    return _LAST_ERROR


def last_provider() -> str | None:
    return _LAST_PROVIDER


def groq_or_openai_configured() -> bool:
    return bool(
        os.environ.get("GROQ_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OLLAMA_MODEL")
    )


def groq_model_id() -> str:
    name = (os.environ.get("GROQ_MODEL") or "").strip()
    if not name or name in GROQ_RETIRED_MODELS:
        return GROQ_DEFAULT_MODEL
    return name


def llm_status() -> dict[str, Any]:
    if os.environ.get("GROQ_API_KEY"):
        return {
            "enabled": True,
            "provider": "groq",
            "model": groq_model_id(),
            "last_error": _LAST_ERROR,
        }
    if os.environ.get("OPENAI_API_KEY"):
        return {
            "enabled": True,
            "provider": "openai",
            "model": os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
            "last_error": _LAST_ERROR,
        }
    if os.environ.get("GEMINI_API_KEY"):
        return {
            "enabled": True,
            "provider": "gemini",
            "model": os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash",
            "last_error": _LAST_ERROR,
        }
    if os.environ.get("OLLAMA_MODEL"):
        return {
            "enabled": True,
            "provider": "ollama",
            "model": os.environ.get("OLLAMA_MODEL"),
            "last_error": _LAST_ERROR,
        }
    model = model_id()
    return {
        "enabled": bool(model),
        "provider": "bedrock" if model else None,
        "model": model,
        "last_error": _LAST_ERROR,
    }


def invoke_json(system: str, user: str) -> dict[str, Any] | None:
    raw = invoke_text(system, user)
    if not raw:
        return None
    return _extract_json(raw)


def model_id() -> str | None:
    if os.environ.get("BEDROCK_DISABLED") == "1":
        return None
    explicit = (os.environ.get("BEDROCK_MODEL_ID") or "").strip()
    if explicit:
        return explicit
    return None


def invoke_text(system: str, user: str) -> str | None:
    global _LAST_ERROR
    _LAST_ERROR = None

    alt = _invoke_openai_compatible(system, user)
    if alt:
        return alt
    if groq_or_openai_configured() and (os.environ.get("LLM_PROVIDER") or "auto") != "bedrock":
        return None

    if os.environ.get("BEDROCK_DISABLED") == "1":
        return None
    model = model_id()
    if not model:
        if _LAST_ERROR is None and not groq_or_openai_configured():
            _LAST_ERROR = "No LLM configured. Set GROQ_API_KEY (free) or wait for Bedrock authorization."
        return None
    try:
        import boto3
    except ImportError:
        _LAST_ERROR = "boto3 is not installed"
        return None

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    client = boto3.client("bedrock-runtime", region_name=region)
    errors: list[str] = []

    for candidate in _model_candidates(model):
        if "anthropic." in candidate or "claude" in candidate:
            text = _invoke_anthropic(client, candidate, system, user)
        else:
            text = _invoke_nova(client, candidate, system, user)
        if text:
            global _LAST_PROVIDER
            _LAST_PROVIDER = "bedrock"
            return text
        if _LAST_ERROR:
            errors.append(_LAST_ERROR)

    _LAST_ERROR = " | ".join(errors) if errors else "unknown Bedrock error"
    return None


def _print_llm(provider: str, model: str, text: str) -> None:
    print(f"\n===== {provider} ({model}) response =====", flush=True)
    print(text, flush=True)
    print("===== end LLM response =====\n", flush=True)


def _invoke_openai_compatible(system: str, user: str) -> str | None:
    """Groq (free), OpenAI, Gemini, or local Ollama — used when Bedrock is NOT_AUTHORIZED."""
    global _LAST_ERROR, _LAST_PROVIDER
    if os.environ.get("GROQ_API_KEY"):
        groq_model = groq_model_id()
        print(f"[groq] calling {groq_model}...", flush=True)
        text = _chat_completions(
            "https://api.groq.com/openai/v1/chat/completions",
            os.environ["GROQ_API_KEY"],
            groq_model,
            system,
            user,
        )
        if text:
            _LAST_PROVIDER = "groq"
            _print_llm("groq", groq_model, text)
            return text
        print(f"[groq] FAILED {_LAST_ERROR}", flush=True)
        return None
    if os.environ.get("OPENAI_API_KEY"):
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        text = _chat_completions(
            url,
            os.environ["OPENAI_API_KEY"],
            os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
            system,
            user,
        )
        if text:
            _LAST_PROVIDER = "openai"
            return text
        return None
    if os.environ.get("GEMINI_API_KEY"):
        text = _invoke_gemini(system, user)
        if text:
            _LAST_PROVIDER = "gemini"
        return text
    if os.environ.get("OLLAMA_MODEL"):
        text = _invoke_ollama(system, user)
        if text:
            _LAST_PROVIDER = "ollama"
        return text
    return None


def _chat_completions(url: str, api_key: str, model: str, system: str, user: str) -> str | None:
    global _LAST_ERROR
    import time
    import urllib.error
    import urllib.request

    system = (system or "")[:2500]
    user = (user or "")[:8000]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    }
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if "gpt-oss" in model:
        payload["reasoning_effort"] = "low"
    body = json.dumps(payload).encode()

    last_detail = ""
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode())
            choices = payload.get("choices") or []
            if not choices:
                _LAST_ERROR = f"{model}: empty choices {str(payload)[:300]}"
                return None
            text = _assistant_text(choices[0])
            if not text:
                _LAST_ERROR = (
                    f"{model}: empty assistant text finish={choices[0].get('finish_reason')} "
                    f"msg_keys={list((choices[0].get('message') or {}).keys())}"
                )
                print(f"[groq] empty body: {_LAST_ERROR}", flush=True)
                return None
            return text
        except urllib.error.HTTPError as exc:
            last_detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 429 and attempt < 3:
                wait = _retry_after_seconds(last_detail)
                print(f"[groq] rate limited, waiting {wait:.1f}s (attempt {attempt + 1})", flush=True)
                time.sleep(wait)
                continue
            _LAST_ERROR = f"{url} HTTP {exc.code}: {last_detail}"
            return None
        except Exception as exc:
            _LAST_ERROR = f"{url}: {type(exc).__name__}: {exc}"
            return None
    _LAST_ERROR = f"{url} HTTP 429: {last_detail}"
    return None


def _retry_after_seconds(detail: str) -> float:
    m = re.search(r"try again in ([0-9.]+)s", detail, re.I)
    if m:
        return min(30.0, float(m.group(1)) + 0.5)
    return 9.0


def _assistant_text(choice: dict) -> str:
    msg = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(
            (p.get("text") or "") if isinstance(p, dict) else str(p) for p in content
        )
    if isinstance(content, str) and content.strip():
        return content
    for key in ("reasoning", "reasoning_content"):
        extra = msg.get(key)
        if isinstance(extra, str) and extra.strip():
            print(f"[groq] using {key} because content was empty", flush=True)
            return extra
    return (content or "").strip() if isinstance(content, str) else ""


def _invoke_gemini(system: str, user: str) -> str | None:
    global _LAST_ERROR
    import urllib.error
    import urllib.request

    model = os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
    key = os.environ["GEMINI_API_KEY"]
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    )
    body = json.dumps(
        {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 2500},
        }
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
        parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict)) or None
    except urllib.error.HTTPError as exc:
        _LAST_ERROR = f"gemini HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:400]}"
        return None
    except Exception as exc:
        _LAST_ERROR = f"gemini: {type(exc).__name__}: {exc}"
        return None


def _invoke_ollama(system: str, user: str) -> str | None:
    global _LAST_ERROR
    import urllib.error
    import urllib.request

    host = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    model = os.environ["OLLAMA_MODEL"]
    url = f"{host}/api/chat"
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
        return ((payload.get("message") or {}).get("content")) or None
    except Exception as exc:
        _LAST_ERROR = f"ollama: {type(exc).__name__}: {exc}"
        return None


def _invoke_anthropic(client: Any, model: str, system: str, user: str) -> str | None:
    global _LAST_ERROR
    try:
        resp = client.invoke_model(
            modelId=model,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 2500,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
            ),
        )
        payload = json.loads(resp["body"].read())
        parts = payload.get("content") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except Exception as exc:
        _LAST_ERROR = f"{model} invoke: {type(exc).__name__}: {exc}"
        return None


def _invoke_nova(client: Any, model: str, system: str, user: str) -> str | None:
    global _LAST_ERROR
    errors: list[str] = []
    prompt = f"{system}\n\n{user}" if system else user

    # Match the Bedrock console snippet: Converse + performance-config latency=standard.
    if hasattr(client, "converse"):
        try:
            resp = client.converse(
                modelId=model,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={
                    "maxTokens": 2500,
                    "temperature": 0.7,
                    "topP": 0.9,
                },
                additionalModelRequestFields={},
                performanceConfig={"latency": "standard"},
            )
            text = _content_text(((resp.get("output") or {}).get("message") or {}).get("content"))
            if text:
                return text
            errors.append(f"{model} converse: empty response")
        except Exception as exc:
            errors.append(f"{model} converse: {type(exc).__name__}: {exc}")

    try:
        resp = client.invoke_model(
            modelId=model,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "messages": [{"role": "user", "content": [{"text": prompt}]}],
                    "inferenceConfig": {
                        "maxTokens": 2500,
                        "temperature": 0.7,
                        "topP": 0.9,
                    },
                }
            ),
        )
        payload = json.loads(resp["body"].read())
        text = _content_text(((payload.get("output") or {}).get("message") or {}).get("content"))
        if text:
            return text
        if payload.get("outputText"):
            return payload["outputText"]
        errors.append(f"{model} invoke: empty response {str(payload)[:200]}")
    except Exception as exc:
        errors.append(f"{model} invoke: {type(exc).__name__}: {exc}")

    _LAST_ERROR = " | ".join(errors)
    return None


def _content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("text"))


def _model_candidates(model: str) -> list[str]:
    ids: list[str] = []
    for item in (model, "amazon.nova-lite-v1:0"):
        if item and item not in ids:
            ids.append(item)
    return ids


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
