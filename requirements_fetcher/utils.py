from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "key",
}

STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "application",
    "as",
    "at",
    "be",
    "by",
    "clone",
    "feature",
    "for",
    "from",
    "in",
    "including",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def scope_keywords(*texts: str) -> list[str]:
    words: list[str] = []
    for text in texts:
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower()):
            normalized = word.replace("_", "-")
            if normalized not in STOPWORDS and normalized not in words:
                words.append(normalized)
    return words[:30]


def relevance_score(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def safe_filename(value: str, fallback: str = "artifact") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.").lower()
    return (cleaned[:80] or fallback).rstrip(".")


def content_fingerprint(url: str, text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().lower()[:8_000]
    return hashlib.sha256(f"{url}\n{normalized}".encode()).hexdigest()[:16]


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in SENSITIVE_KEYS or any(
        part in normalized for part in ("password", "secret", "token", "cookie", "authorization")
    )


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if is_sensitive_key(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact(item) for item in value]
    return value


def redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            query.append((key, "[REDACTED]" if is_sensitive_key(key) else value))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
    except ValueError:
        return url


def json_shape(value: Any, depth: int = 0) -> Any:
    if depth >= 8:
        return "unknown"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return {str(key): json_shape(item, depth + 1) for key, item in list(value.items())[:100]}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return []
        shapes = [json_shape(item, depth + 1) for item in list(value)[:3]]
        unique: list[Any] = []
        for shape in shapes:
            if shape not in unique:
                unique.append(shape)
        return [unique[0]] if len(unique) == 1 else unique
    return type(value).__name__


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def load_gemini_key_from_env_files(paths: Sequence[Path]) -> None:
    """Load GEMINI_API_KEY from the first available local .env file.

    Existing process environment values always win, and no other .env values are loaded.
    """
    if os.environ.get("GEMINI_API_KEY"):
        return
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        for raw_line in resolved.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() != "GEMINI_API_KEY":
                continue
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ["GEMINI_API_KEY"] = value
                return
