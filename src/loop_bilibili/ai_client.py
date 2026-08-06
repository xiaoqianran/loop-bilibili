"""OpenAI-compatible chat client (stdlib only) with transient retries."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class AiClientError(RuntimeError):
    """Raised when the upstream chat API fails."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


def _is_retryable_status(code: int) -> bool:
    return code == 408 or code == 429 or code >= 500


def _once(
    *,
    url: str,
    api_key: str,
    body: dict[str, Any],
    timeout: float,
) -> ChatResult:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "loop-bilibili-ai/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            detail = str(exc)
        raise AiClientError(
            f"HTTP {exc.code}: {detail or exc.reason}",
            retryable=_is_retryable_status(int(exc.code)),
            status=int(exc.code),
        ) from exc
    except urllib.error.URLError as exc:
        raise AiClientError(
            f"network error: {exc.reason}",
            retryable=True,
        ) from exc
    except TimeoutError as exc:
        raise AiClientError(
            f"timeout after {timeout}s",
            retryable=True,
        ) from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AiClientError(f"invalid JSON: {raw_text[:300]}", retryable=True) from exc

    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        if isinstance(err, dict):
            msg = err.get("message") or json.dumps(err, ensure_ascii=False)
            code = err.get("code") or err.get("type") or ""
        else:
            msg = str(err)
            code = ""
        retryable = any(
            x in str(msg).lower() or x in str(code).lower()
            for x in ("rate", "overloaded", "timeout", "temporar", "unavailable", "429", "503")
        )
        raise AiClientError(str(msg), retryable=retryable)

    choice = None
    if isinstance(data, dict):
        choices = data.get("choices") or []
        if choices:
            choice = choices[0]
    if not isinstance(choice, dict):
        raise AiClientError(f"response missing choices: {raw_text[:300]}", retryable=True)

    message = choice.get("message") or choice.get("delta") or {}
    content = ""
    if isinstance(message, dict):
        content = str(message.get("content") or message.get("text") or "")
    if not content and isinstance(choice.get("text"), str):
        content = choice["text"]
    if not str(content).strip():
        # empty body sometimes happens on upstream glitch — worth one more try
        raise AiClientError("empty assistant content", retryable=True)

    usage = data.get("usage") if isinstance(data, dict) else None
    return ChatResult(
        content=str(content),
        model=str((data or {}).get("model") or body.get("model") or ""),
        finish_reason=str(choice.get("finish_reason") or ""),
        usage=usage if isinstance(usage, dict) else None,
        raw=data if isinstance(data, dict) else None,
    )


def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.4,
    max_tokens: int = 8192,
    timeout: float = 180.0,
    stream: bool = False,
    retries: int = 2,
    retry_backoff_s: float = 2.0,
) -> ChatResult:
    """
    POST {base_url}/chat/completions (OpenAI-compatible).

    Non-stream only for the offline worker.
    ``retries`` = extra attempts after the first (default 2 → up to 3 tries).
    Retries on timeout / network / 429 / 5xx / empty content.
    """
    root = str(base_url or "").strip().rstrip("/")
    if not root:
        raise AiClientError("AI base_url is empty")
    if not api_key:
        raise AiClientError("AI api_key is empty")
    if not model:
        raise AiClientError("AI model is empty")

    url = f"{root}/chat/completions"
    body = {
        "model": str(model).strip(),
        "messages": messages,
        "temperature": float(temperature),
        "max_tokens": int(max(256, min(128_000, max_tokens))),
        "stream": bool(stream),
    }

    attempts = max(1, int(retries) + 1)
    last: AiClientError | None = None
    for i in range(attempts):
        try:
            return _once(url=url, api_key=api_key, body=body, timeout=timeout)
        except AiClientError as exc:
            last = exc
            if not exc.retryable or i + 1 >= attempts:
                raise
            delay = float(retry_backoff_s) * (2**i)
            logger.warning(
                "chat_completion retry %s/%s after %ss · %s",
                i + 1,
                attempts - 1,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last is not None
    raise last
