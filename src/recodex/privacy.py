from __future__ import annotations

import re
from pathlib import Path
from typing import Any

API_KEY_RE = re.compile(r"\b(?:OPENAI_API_KEY|ANTHROPIC_API_KEY)\s*=\s*\S+", re.I)
TOKEN_RE = re.compile(r"\b(?:GITHUB_TOKEN|GH_TOKEN|API_TOKEN)\s*=\s*\S+", re.I)
AUTH_RE = re.compile(r"\bAuthorization:\s*(?:Bearer|Basic)\s+\S+", re.I)
DATABASE_URL_RE = re.compile(r"\bDATABASE_URL\s*=\s*\S+", re.I)
JWT_SECRET_RE = re.compile(r"\bJWT_SECRET\s*=\s*\S+", re.I)
PASSWORD_RE = re.compile(r"\b(password|passwd|pwd)\s*=\s*[^\s]+", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
SSH_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.S,
)


def redact_text(text: str, *, home: Path | None = None) -> str:
    if home is None:
        home = Path.home()
    redacted = text
    redacted = SSH_KEY_RE.sub("[REDACTED:SSH_PRIVATE_KEY]", redacted)
    redacted = API_KEY_RE.sub("OPENAI_API_KEY=[REDACTED:API_KEY]", redacted)
    redacted = TOKEN_RE.sub(lambda match: match.group(0).split("=", 1)[0] + "=[REDACTED:TOKEN]", redacted)
    redacted = AUTH_RE.sub("Authorization: [REDACTED:AUTHORIZATION]", redacted)
    redacted = DATABASE_URL_RE.sub("DATABASE_URL=[REDACTED:DATABASE_URL]", redacted)
    redacted = JWT_SECRET_RE.sub("JWT_SECRET=[REDACTED:JWT_SECRET]", redacted)
    redacted = PASSWORD_RE.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)
    redacted = EMAIL_RE.sub("[REDACTED:EMAIL]", redacted)
    home_text = str(home.expanduser())
    if home_text:
        redacted = redacted.replace(home_text, "[REDACTED:HOME]")
    return redacted


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
