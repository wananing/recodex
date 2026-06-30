from __future__ import annotations

import re
from pathlib import Path
from typing import Any

API_KEY_RE = re.compile(r"\b(?:OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*\S+", re.I)
TOKEN_RE = re.compile(r"\b(?:GITHUB_TOKEN|GH_TOKEN|API_TOKEN)\s*=\s*\S+", re.I)
AUTH_RE = re.compile(r"\bAuthorization:\s*(?:Bearer|Basic)\s+\S+", re.I)
DATABASE_URL_RE = re.compile(r"\bDATABASE_URL\s*=\s*\S+", re.I)
JDBC_URL_RE = re.compile(r"\b(?:jdbc:)?(?:postgresql|mysql|mariadb|mongodb|redis)://\S+", re.I)
JWT_SECRET_RE = re.compile(r"\bJWT_SECRET\s*=\s*\S+", re.I)
PASSWORD_RE = re.compile(r"\b(password|passwd|pwd)\s*=\s*[^\s]+", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
IP_PORT_PATTERN_RE = re.compile(r"\b(?:\d{1,3}\\\.){3}\d{1,3}\|\d{2,5}\b")
ESCAPED_IPV4_RE = re.compile(r"\b(?:\d{1,3}\\\.){3}\d{1,3}\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?\b")
PRIVATE_URL_RE = re.compile(
    r"https?://(?:"
    r"localhost"
    r"|127(?:\.\d{1,3}){3}"
    r"|10(?:\.\d{1,3}){3}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2}"
    r"|(?:\d{1,3}\.){3}\d{1,3}"
    r")[^\s`'\"<>)]*",
    re.I,
)
SSH_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.S,
)


def redact_text(text: str, *, home: Path | None = None) -> str:
    if home is None:
        home = Path.home()
    if _looks_like_context_text(text):
        return "[REDACTED:CONTEXT]"
    redacted = text
    redacted = SSH_KEY_RE.sub("[REDACTED:SSH_PRIVATE_KEY]", redacted)
    redacted = API_KEY_RE.sub("OPENAI_API_KEY=[REDACTED:API_KEY]", redacted)
    redacted = TOKEN_RE.sub(lambda match: match.group(0).split("=", 1)[0] + "=[REDACTED:TOKEN]", redacted)
    redacted = AUTH_RE.sub("Authorization: [REDACTED:AUTHORIZATION]", redacted)
    redacted = DATABASE_URL_RE.sub("DATABASE_URL=[REDACTED:DATABASE_URL]", redacted)
    redacted = JDBC_URL_RE.sub("[REDACTED:DATABASE_URL]", redacted)
    redacted = JWT_SECRET_RE.sub("JWT_SECRET=[REDACTED:JWT_SECRET]", redacted)
    redacted = PASSWORD_RE.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)
    redacted = EMAIL_RE.sub("[REDACTED:EMAIL]", redacted)
    redacted = PRIVATE_URL_RE.sub("[REDACTED:URL]", redacted)
    redacted = IP_PORT_PATTERN_RE.sub("[REDACTED:IP_PATTERN]", redacted)
    redacted = ESCAPED_IPV4_RE.sub("[REDACTED:IP]", redacted)
    redacted = IPV4_RE.sub("[REDACTED:IP]", redacted)
    home_text = str(home.expanduser())
    if home_text:
        redacted = redacted.replace(home_text, "[REDACTED:HOME]")
    return redacted


def _looks_like_context_text(text: str) -> bool:
    lowered = text.strip().lower()
    return (
        lowered.startswith("<environment_context>")
        or lowered.startswith("<permissions")
        or lowered.startswith("<collaboration_mode>")
        or lowered.startswith("<skills_instructions>")
        or ("knowledge cutoff" in lowered[:240] and "you are" in lowered[:600])
        or ("sandbox_mode" in lowered[:600] and "filesystem sandboxing" in lowered[:600])
    )


def redact_mapping(value: Any, *, home: Path | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_sensitive_value(key, nested, home=home)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item, home=home) for item in value]
    if isinstance(value, str):
        return redact_text(value, home=home)
    return value


def _redact_sensitive_value(key: object, value: Any, *, home: Path | None) -> Any:
    lowered = str(key).lower()
    if isinstance(value, str):
        if "api_key" in lowered:
            return "[REDACTED:API_KEY]"
        if "token" in lowered:
            return "[REDACTED:TOKEN]"
        if "email" in lowered:
            return "[REDACTED:EMAIL]"
        if "password" in lowered:
            return "[REDACTED:PASSWORD]"
        if "secret" in lowered:
            return "[REDACTED:SECRET]"
    return redact_mapping(value, home=home)
