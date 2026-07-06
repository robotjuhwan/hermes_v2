from __future__ import annotations

import fnmatch
import posixpath
from typing import Any

GLOB_CHARS = frozenset("*?[")


def validate_patch_paths(
    touched_paths: list[str],
    allowed_paths: list[str],
    blocked_paths: list[str],
) -> dict[str, Any]:
    normalized_allowed_result = _normalize_policy_paths(allowed_paths, "allowed_paths")
    normalized_blocked_result = _normalize_policy_paths(blocked_paths, "blocked_paths")
    invalid_policy_paths = [
        *normalized_allowed_result["invalid"],
        *normalized_blocked_result["invalid"],
    ]
    if invalid_policy_paths:
        return {
            "status": "rejected",
            "reason": "invalid_policy_path",
            "invalid_policy_paths": invalid_policy_paths,
        }

    invalid_paths: list[dict[str, str]] = []
    normalized_touched: list[str] = []

    for path in touched_paths:
        normalized = _normalize_repo_path(path)
        if normalized["status"] != "ok":
            invalid_paths.append(
                {"path": str(path), "reason": str(normalized["reason"])}
            )
            continue
        normalized_touched.append(str(normalized["path"]))

    if invalid_paths:
        return {
            "status": "rejected",
            "reason": "invalid_path",
            "invalid_paths": invalid_paths,
        }

    normalized_allowed = normalized_allowed_result["paths"]
    normalized_blocked = normalized_blocked_result["paths"]

    blocked_matches = [
        path
        for path in normalized_touched
        if any(_matches_policy(path, blocked) for blocked in normalized_blocked)
    ]
    if blocked_matches:
        return {
            "status": "rejected",
            "reason": "blocked_path_touched",
            "blocked_matches": blocked_matches,
        }

    outside_allowed = [
        path
        for path in normalized_touched
        if not any(_matches_policy(path, allowed) for allowed in normalized_allowed)
    ]
    if outside_allowed:
        return {
            "status": "rejected",
            "reason": "outside_allowed_paths",
            "outside_allowed": outside_allowed,
        }

    return {"status": "ok"}


def _normalize_policy_paths(paths: list[str], policy_name: str) -> dict[str, Any]:
    normalized_paths: list[str] = []
    invalid_paths: list[dict[str, str]] = []

    for path in paths:
        normalized = _normalize_repo_path(path)
        if normalized["status"] != "ok":
            invalid_paths.append(
                {
                    "path": str(path),
                    "policy": policy_name,
                    "reason": str(normalized["reason"]),
                }
            )
            continue
        normalized_paths.append(str(normalized["path"]))

    return {"paths": normalized_paths, "invalid": invalid_paths}


def _normalize_repo_path(path: str) -> dict[str, str]:
    raw_path = str(path).replace("\\", "/").strip()
    if raw_path.startswith("/"):
        return {"status": "rejected", "reason": "absolute_path"}

    parts = [part for part in raw_path.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return {"status": "rejected", "reason": "path_traversal"}

    normalized = posixpath.normpath("/".join(parts))
    if normalized in {"", "."}:
        return {"status": "rejected", "reason": "empty_path"}

    return {"status": "ok", "path": normalized}


def _matches_policy(path: str, policy: str) -> bool:
    if any(char in policy for char in GLOB_CHARS):
        return fnmatch.fnmatchcase(path, policy)
    return path == policy or path.startswith(f"{policy}/")
