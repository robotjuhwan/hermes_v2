from __future__ import annotations

from secrets import compare_digest
from typing import Sequence


def parse_bearer_token(authorization: str | None) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        return ""
    parts = raw.split(" ", 1)
    if len(parts) != 2:
        return ""
    if parts[0].strip().lower() != "bearer":
        return ""
    return parts[1].strip()


def is_valid_bearer_token(authorization: str | None, expected_token: str) -> bool:
    return is_valid_bearer_token_any(authorization, [expected_token])


def is_valid_bearer_token_any(
    authorization: str | None,
    expected_tokens: Sequence[str],
) -> bool:
    provided = parse_bearer_token(authorization)
    return is_valid_token_any(provided, expected_tokens)


def is_valid_token_any(
    provided_token: str | None,
    expected_tokens: Sequence[str],
) -> bool:
    provided = str(provided_token or "").strip()
    if not provided:
        return False
    for expected in expected_tokens:
        token = str(expected or "").strip()
        if token and compare_digest(provided, token):
            return True
    return False


def is_valid_request_token_any(
    authorization: str | None,
    header_token: str | None,
    expected_tokens: Sequence[str],
) -> bool:
    if is_valid_bearer_token_any(authorization, expected_tokens):
        return True
    return is_valid_token_any(header_token, expected_tokens)
