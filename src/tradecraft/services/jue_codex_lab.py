from __future__ import annotations

import asyncio
import inspect
import json
import posixpath
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from tradecraft.services.jue_codex_lab_models import RepairTask
from tradecraft.services.jue_codex_lab_store import JueCodexLabStore
from tradecraft.services.jue_codex_patch_workspace import validate_patch_paths
from tradecraft.services.jue_codex_repair_catalog import repair_strategy_for
from tradecraft.services.jue_codex_verifier import JueCodexVerifier


ACTIVE_FAILURE_STATUSES = {"fail", "missing", "warn"}
PRIORITY_BY_CODE = {"p0": 100, "p1": 80, "p2": 60, "p3": 40}
PRIORITY_BY_STATUS = {"fail": 90, "missing": 80, "warn": 60}
KST = ZoneInfo("Asia/Seoul")
KIS_HOT_DEPLOY_SENSITIVE_PREFIXES = (
    "src/tradecraft/services/kis.py",
    "src/tradecraft/services/kis_",
    "src/tradecraft/runtime/kis",
    "src/tradecraft/api/kis",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _policy_paths(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_venue(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_failure_status(item: dict[str, Any]) -> str:
    return _clean_text(
        item.get("failure_status") or item.get("status") or item.get("metric_status")
    ).lower()


def _priority_value(value: Any, *, failure_status: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    normalized = _clean_text(value).lower()
    if normalized in PRIORITY_BY_CODE:
        return PRIORITY_BY_CODE[normalized]
    return PRIORITY_BY_STATUS.get(failure_status, 50)


def _failure_evidence(item: dict[str, Any]) -> str:
    for key in ("evidence", "runner_hint", "exit_criteria"):
        value = _clean_text(item.get(key))
        if value:
            return value
    return ""


def _task_id_for_item(
    item: dict[str, Any],
    *,
    venue: str,
    discipline_id: str,
) -> str:
    repair_action_id = _clean_text(item.get("repair_action_id"))
    if repair_action_id:
        return f"{venue}:{repair_action_id}"
    return f"{venue}:validation:{discipline_id}"


class JueCodexLabService:
    def __init__(
        self,
        store: JueCodexLabStore,
        validation_db_path: str | Path,
        codex_runtime: Any | None = None,
        repo_root: str | Path = ".",
        verifier: Any | None = None,
        autonomy_mode: str = "auto_apply_verified",
        max_patch_bytes: int = 120_000,
        allowed_paths: str | list[str] | tuple[str, ...] | None = None,
        blocked_paths: str | list[str] | tuple[str, ...] | None = None,
        market_hours_hot_deploy: bool = True,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.validation_db_path = Path(validation_db_path)
        self.codex_runtime = codex_runtime
        self.repo_root = Path(repo_root)
        self.autonomy_mode = _clean_text(autonomy_mode) or "auto_apply_verified"
        self.max_patch_bytes = max(int(max_patch_bytes), 0)
        self.allowed_paths = _policy_paths(allowed_paths)
        self.blocked_paths = _policy_paths(blocked_paths)
        self.market_hours_hot_deploy = bool(market_hours_hot_deploy)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.verifier = (
            verifier if verifier is not None else JueCodexVerifier(self.repo_root)
        )

    def record_green_path_progress(
        self,
        venue: str,
        discipline_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        repair_task_id: str,
    ) -> dict[str, Any]:
        return self.store.record_green_path_progress(
            venue=venue,
            discipline_id=discipline_id,
            before_status=_clean_text(before.get("status")),
            after_status=_clean_text(after.get("status")),
            before_score=before.get("score"),
            after_score=after.get("score"),
            validation_run_before=_clean_text(before.get("run_id")),
            validation_run_after=_clean_text(after.get("run_id")),
            repair_task_id=repair_task_id,
        )

    def status(self) -> dict[str, Any]:
        db_path = str(self.store.db_path)
        try:
            status_counts = self.store.task_status_counts()
        except Exception as exc:
            return {
                "status": "error",
                "db_path": db_path,
                "initialized": False,
                "queued_count": 0,
                "failed_count": 0,
                "error_message": str(exc),
            }
        counts = status_counts.get("counts") if isinstance(status_counts, dict) else {}
        counts = counts if isinstance(counts, dict) else {}
        return {
            "status": "ok",
            "db_path": db_path,
            "initialized": bool(status_counts.get("initialized")),
            "queued_count": int(counts.get("queued") or 0),
            "failed_count": int(counts.get("failed") or 0),
        }

    @staticmethod
    def build_codex_repair_prompt(task: dict[str, Any]) -> dict[str, Any]:
        required_output_fields = [
            "root_cause",
            "patch_strategy",
            "patch",
            "verification_commands",
            "rollback_notes",
            "wiki_memory",
        ]
        return {
            "role": "jue_codex_autonomous_repair",
            "approval_policy": "no_human_approval_required",
            "failure_policy": "fail_loudly_no_fallback",
            "objective": (
                "Turn the validation warning/failure into a verified "
                "code/data/research/test improvement. Do not hide the metric."
            ),
            "task": deepcopy(task),
            "allowed_paths": list(task.get("allowed_paths") or []),
            "blocked_paths": list(task.get("blocked_paths") or []),
            "operational_constraints": {
                "do_not_edit_secrets": True,
                "do_not_disable_safety_gates": True,
                "do_not_convert_failures_to_fallbacks": True,
                "must_add_or_update_tests": True,
                "must_keep_runtime_restart_scope_minimal": True,
            },
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": required_output_fields,
                "properties": {
                    "root_cause": {"type": "string"},
                    "patch_strategy": {"type": "string"},
                    "patch": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["touched_paths", "files"],
                        "properties": {
                            "touched_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "files": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["path", "content"],
                                    "properties": {
                                        "path": {"type": "string"},
                                        "content": {"type": "string"},
                                    },
                                },
                            },
                            "diff_summary": {"type": "string"},
                        },
                    },
                    "verification_commands": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "rollback_notes": {"type": "string"},
                    "wiki_memory": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["should_update", "summary", "tags"],
                        "properties": {
                            "should_update": {"type": "boolean"},
                            "summary": {"type": "string"},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }

    def ingest_validation_work_queue(self, venue: str) -> dict[str, Any]:
        normalized_venue = _normalize_venue(venue)
        self.store.initialize()
        try:
            row = self._latest_validation_row(normalized_venue)
        except sqlite3.DatabaseError as exc:
            return {
                "status": "error",
                "venue": normalized_venue,
                "source_validation_run_id": "",
                "created_count": 0,
                "queued_count": 0,
                "message": f"could not read validation DB: {exc}",
            }
        if row is None:
            return {
                "status": "empty",
                "venue": normalized_venue,
                "source_validation_run_id": "",
                "created_count": 0,
                "queued_count": 0,
            }

        source_run_id = _clean_text(row.get("run_id"))
        raw_payload = _clean_text(row.get("payload_json")) or "{}"
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            return {
                "status": "error",
                "venue": normalized_venue,
                "source_validation_run_id": source_run_id,
                "created_count": 0,
                "queued_count": 0,
                "message": f"invalid validation payload JSON: {exc}",
            }
        if not isinstance(payload, dict):
            return {
                "status": "error",
                "venue": normalized_venue,
                "source_validation_run_id": source_run_id,
                "created_count": 0,
                "queued_count": 0,
                "message": "validation payload must decode to an object",
            }

        work_queue_result = self._work_queue_from_payload(payload)
        if work_queue_result["status"] == "error":
            return {
                "status": "error",
                "venue": normalized_venue,
                "source_validation_run_id": source_run_id,
                "created_count": 0,
                "queued_count": 0,
                "message": work_queue_result["message"],
            }

        created_count = 0
        queued_count = 0
        now_iso = _utc_now_iso()
        for item in work_queue_result["work_queue"]:
            task_result = self._task_from_item(
                item,
                venue=normalized_venue,
                source_validation_run_id=source_run_id,
            )
            if task_result["status"] == "skip":
                continue
            if task_result["status"] == "error":
                return {
                    "status": "error",
                    "venue": normalized_venue,
                    "source_validation_run_id": source_run_id,
                    "created_count": created_count,
                    "queued_count": queued_count,
                    "message": task_result["message"],
                }
            queued_count += 1
            if self.store.upsert_task(task_result["task"], now_iso=now_iso):
                created_count += 1

        return {
            "status": "ok",
            "venue": normalized_venue,
            "source_validation_run_id": source_run_id,
            "created_count": created_count,
            "queued_count": queued_count,
        }

    def run_once(self, max_tasks: int = 1) -> dict[str, Any]:
        self.store.initialize()
        tasks = self.store.list_tasks(status="queued")[: max(0, int(max_tasks))]
        task_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        deployed_count = 0
        failed_count = 0

        for task in tasks:
            run_id = f"repair_run_{uuid4().hex}"
            task_id = str(task["task_id"])
            now_iso = _utc_now_iso()
            self.store.record_repair_run_start(
                run_id=run_id,
                task_id=task_id,
                owner=str(task.get("owner") or ""),
                started_at=now_iso,
                summary={
                    "task_status": str(task.get("status") or ""),
                    "source_validation_run_id": str(
                        task.get("source_validation_run_id") or ""
                    ),
                    "failure_status": str(task.get("failure_status") or ""),
                },
            )
            policy_decode_errors = task.get("policy_decode_errors") or []
            if policy_decode_errors:
                error = self._record_early_failure(
                    task_id=task_id,
                    run_id=run_id,
                    reason="policy_decode_error",
                    message="Task path policy JSON could not be decoded safely",
                    detail={"policy_decode_errors": policy_decode_errors},
                    now_iso=now_iso,
                )
                failed_count += 1
                errors.append(error)
                task_results.append(
                    {"task_id": task_id, "status": "failed", "error": error}
                )
                continue

            if self.codex_runtime is None:
                error = self._record_early_failure(
                    task_id=task_id,
                    run_id=run_id,
                    reason="no_codex_runtime",
                    message=(
                        "codex_runtime is required; no deterministic fallback was used"
                    ),
                    detail={},
                    now_iso=now_iso,
                )
                failed_count += 1
                errors.append(error)
                task_results.append(
                    {"task_id": task_id, "status": "failed", "error": error}
                )
                continue

            prompt = self.build_codex_repair_prompt(task)
            try:
                response = self._complete_json(prompt)
            except Exception as exc:
                error = {
                    "task_id": task_id,
                    "reason": "codex_runtime_error",
                    "message": str(exc),
                }
                self._record_early_failure(
                    task_id=task_id,
                    run_id=run_id,
                    reason="codex_runtime_error",
                    message=str(exc),
                    detail={},
                    now_iso=_utc_now_iso(),
                )
                failed_count += 1
                errors.append(error)
                task_results.append(
                    {"task_id": task_id, "status": "failed", "error": error}
                )
                continue

            if isinstance(response, dict) and response.get("ok") is False:
                response_summary = self._response_summary(response)
                error = self._record_early_failure(
                    task_id=task_id,
                    run_id=run_id,
                    reason="codex_response_error",
                    message=str(
                        response.get("error")
                        or "Codex response was not a usable repair payload"
                    ),
                    detail={"response_summary": response_summary},
                    now_iso=_utc_now_iso(),
                )
                failed_count += 1
                errors.append(error)
                task_results.append(
                    {"task_id": task_id, "status": "failed", "error": error}
                )
                continue

            if not isinstance(response, dict) or not isinstance(
                response.get("patch"),
                dict,
            ):
                response_summary = self._response_summary(response)
                error = self._record_early_failure(
                    task_id=task_id,
                    run_id=run_id,
                    reason="patch_missing",
                    message="Codex response did not include a patch object",
                    detail={
                        "response_type": type(response).__name__,
                        "response_summary": response_summary,
                    },
                    now_iso=_utc_now_iso(),
                )
                failed_count += 1
                errors.append(error)
                task_results.append(
                    {"task_id": task_id, "status": "failed", "error": error}
                )
                continue

            apply_result = self._apply_verified_patch(
                task=task,
                run_id=run_id,
                response=response,
            )
            task_results.append(apply_result)
            if apply_result["status"] == "deployed":
                deployed_count += 1
            else:
                failed_count += 1
                errors.append(
                    {
                        "task_id": task_id,
                        "reason": apply_result.get("reason", "patch_failed"),
                        "message": apply_result.get("message", ""),
                    }
                )

        return {
            "status": "error" if failed_count else "ok",
            "processed_count": len(tasks),
            "deployed_count": deployed_count,
            "failed_count": failed_count,
            "errors": errors,
            "tasks": task_results,
        }

    def _complete_json(self, prompt: dict[str, Any]) -> Any:
        complete_json = getattr(self.codex_runtime, "complete_json", None)
        if not callable(complete_json):
            raise TypeError("codex_runtime.complete_json must be callable")
        result = complete_json(prompt)
        if inspect.iscoroutine(result):
            return _run_awaitable_from_sync(result)
        if inspect.isawaitable(result):
            raise TypeError(
                "codex_runtime.complete_json returned an unsupported awaitable; "
                "return a coroutine object or a plain dict"
            )
        return result

    def _apply_verified_patch(
        self,
        *,
        task: dict[str, Any],
        run_id: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = str(task["task_id"])
        patch = response["patch"]
        normalized = self._normalize_patch_payload(patch)
        if normalized["status"] != "ok":
            return self._fail_patch(
                task_id=task_id,
                run_id=run_id,
                reason=str(normalized["reason"]),
                message=str(normalized.get("message", "")),
                detail=normalized,
            )

        touched_paths = normalized["touched_paths"]
        files = normalized["files"]
        validation = validate_patch_paths(
            touched_paths,
            list(task.get("allowed_paths") or []),
            list(task.get("blocked_paths") or []),
        )
        if validation["status"] != "ok":
            return self._fail_patch(
                task_id=task_id,
                run_id=run_id,
                reason=str(validation["reason"]),
                message="Patch touched paths failed policy validation",
                detail=validation,
                files=touched_paths,
            )

        file_paths = [str(file_item["path"]) for file_item in files]
        missing_touched = [path for path in file_paths if path not in set(touched_paths)]
        if missing_touched:
            return self._fail_patch(
                task_id=task_id,
                run_id=run_id,
                reason="file_not_declared_touched",
                message="Every patched file must be listed in touched_paths",
                detail={"missing_touched_paths": missing_touched},
                files=file_paths,
            )

        safety = self._validate_safety_controls(
            task=task,
            normalized=normalized,
            file_paths=file_paths,
        )
        if safety["status"] != "ok":
            return self._fail_patch(
                task_id=task_id,
                run_id=run_id,
                reason=str(safety["reason"]),
                message=str(safety.get("message", "")),
                detail=safety,
                files=file_paths,
            )

        verification_commands = response.get("verification_commands")
        if not isinstance(verification_commands, list) or not all(
            isinstance(command, str) for command in verification_commands
        ):
            return self._fail_patch(
                task_id=task_id,
                run_id=run_id,
                reason="verification_commands_invalid",
                message="verification_commands must be a list of strings",
                detail={"verification_commands": verification_commands},
                files=file_paths,
            )

        path_result = self._resolve_patch_files(files)
        if path_result["status"] != "ok":
            return self._fail_patch(
                task_id=task_id,
                run_id=run_id,
                reason=str(path_result["reason"]),
                message=str(path_result.get("message", "")),
                detail=path_result,
                files=file_paths,
            )

        snapshots: list[dict[str, Any]] = []
        patch_summary = str(
            patch.get("diff_summary") or response.get("patch_strategy") or ""
        )
        try:
            snapshots = self._snapshot_files(path_result["files"])
            self._write_patch_files(path_result["files"])
            self.store.record_patch_attempt(
                run_id=run_id,
                task_id=task_id,
                status="applied",
                patch_summary=patch_summary,
                files=file_paths,
                created_at=_utc_now_iso(),
            )
            verification = self.verifier.run_commands(verification_commands)
        except Exception as exc:
            rollback = self._restore_snapshots(snapshots)
            return self._fail_patch(
                task_id=task_id,
                run_id=run_id,
                reason="patch_apply_exception",
                message=str(exc),
                detail={
                    "reason": "patch_apply_exception",
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                    **rollback,
                },
                files=file_paths,
                deployment_status="patch_apply_exception",
            )

        if verification.get("status") != "pass":
            rollback = self._restore_snapshots(snapshots)
            return self._fail_patch(
                task_id=task_id,
                run_id=run_id,
                reason="verification_failed",
                message="Verification commands failed",
                detail={"verification": verification, "files": file_paths, **rollback},
                files=file_paths,
                deployment_status="verification_failed",
            )

        self.store.mark_task_status(task_id, "deployed", _utc_now_iso())
        self.store.record_deployment_event(
            task_id=task_id,
            run_id=run_id,
            status="deployed",
            detail={"files": file_paths, "verification": verification},
            created_at=_utc_now_iso(),
        )
        self.store.finish_repair_run(
            run_id=run_id,
            status="deployed",
            finished_at=_utc_now_iso(),
            summary={
                "files": file_paths,
                "verification_status": verification.get("status"),
            },
        )
        return {
            "task_id": task_id,
            "status": "deployed",
            "run_id": run_id,
            "files": file_paths,
            "verification": verification,
        }

    def _validate_safety_controls(
        self,
        *,
        task: dict[str, Any],
        normalized: dict[str, Any],
        file_paths: list[str],
    ) -> dict[str, Any]:
        if self.autonomy_mode != "auto_apply_verified":
            return {
                "status": "rejected",
                "reason": "autonomy_mode_not_auto_apply",
                "message": "Codex Lab is not configured for verified auto-apply",
                "autonomy_mode": self.autonomy_mode,
            }

        patch_bytes = sum(
            len(str(file_item.get("content") or "").encode("utf-8"))
            for file_item in list(normalized.get("files") or [])
            if isinstance(file_item, dict)
        )
        if patch_bytes > self.max_patch_bytes:
            return {
                "status": "rejected",
                "reason": "patch_too_large",
                "message": "Patch exceeds configured Codex Lab byte limit",
                "patch_bytes": patch_bytes,
                "max_patch_bytes": self.max_patch_bytes,
            }

        touched_paths = list(normalized.get("touched_paths") or [])
        if self.allowed_paths or self.blocked_paths:
            global_allowed = self.allowed_paths if self.allowed_paths else touched_paths
            global_policy = validate_patch_paths(
                touched_paths,
                global_allowed,
                self.blocked_paths,
            )
            if global_policy["status"] != "ok":
                return {
                    **global_policy,
                    "message": "Patch touched paths failed global Codex Lab policy",
                    "policy_scope": "global",
                }

        if self._market_hours_hot_deploy_blocked(task=task, file_paths=file_paths):
            return {
                "status": "rejected",
                "reason": "market_hours_hot_deploy_blocked",
                "message": "KIS live execution patch blocked during market hours",
                "venue": str(task.get("venue") or ""),
                "files": file_paths,
            }

        return {"status": "ok"}

    def _market_hours_hot_deploy_blocked(
        self,
        *,
        task: dict[str, Any],
        file_paths: list[str],
    ) -> bool:
        if self.market_hours_hot_deploy:
            return False
        if _normalize_venue(str(task.get("venue") or "")) != "kis":
            return False
        if not any(
            path == prefix or path.startswith(f"{prefix}/") or path.startswith(prefix)
            for path in file_paths
            for prefix in KIS_HOT_DEPLOY_SENSITIVE_PREFIXES
        ):
            return False
        current = self.now()
        if current.tzinfo is None:
            local = current.replace(tzinfo=KST)
        else:
            local = current.astimezone(KST)
        minutes = local.hour * 60 + local.minute
        return local.weekday() < 5 and (9 * 60) <= minutes <= (15 * 60 + 30)

    def _record_early_failure(
        self,
        *,
        task_id: str,
        run_id: str,
        reason: str,
        message: str,
        detail: dict[str, Any],
        now_iso: str,
    ) -> dict[str, Any]:
        error = {"task_id": task_id, "reason": reason, "message": message}
        self.store.mark_task_status(task_id, "failed", now_iso)
        self.store.record_deployment_event(
            task_id=task_id,
            run_id=run_id,
            status=reason,
            detail={**detail, **error},
            created_at=now_iso,
        )
        self.store.finish_repair_run(
            run_id=run_id,
            status=reason,
            finished_at=now_iso,
            summary={**detail, **error},
        )
        return error

    @staticmethod
    def _response_summary(response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {"response_type": type(response).__name__}
        summary: dict[str, Any] = {
            "response_type": "dict",
            "keys": sorted(str(key) for key in response.keys()),
        }
        for key in ("ok", "mode", "error"):
            if key in response:
                summary[key] = response.get(key)
        content = response.get("content")
        if isinstance(content, str) and content.strip():
            summary["content_preview"] = content.strip()[:500]
        return summary

    @staticmethod
    def _normalize_patch_payload(patch: dict[str, Any]) -> dict[str, Any]:
        touched_paths = patch.get("touched_paths")
        files = patch.get("files")
        if not isinstance(touched_paths, list) or not all(
            isinstance(path, str) for path in touched_paths
        ):
            return {
                "status": "rejected",
                "reason": "patch_invalid",
                "message": "patch.touched_paths must be a list of strings",
            }
        if not isinstance(files, list):
            return {
                "status": "rejected",
                "reason": "patch_invalid",
                "message": "patch.files must be a list",
            }
        normalized_files: list[dict[str, str]] = []
        for file_item in files:
            if not isinstance(file_item, dict):
                return {
                    "status": "rejected",
                    "reason": "patch_invalid",
                    "message": "patch.files entries must be objects",
                }
            path = file_item.get("path")
            content = file_item.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                return {
                    "status": "rejected",
                    "reason": "patch_invalid",
                    "message": "patch file entries require string path and content",
                }
            normalized_path = posixpath.normpath(path.replace("\\", "/"))
            if "\\" in path:
                return {
                    "status": "rejected",
                    "reason": "invalid_patch_file_path",
                    "message": "patch file paths must use POSIX separators",
                    "invalid_paths": [
                        {
                            "path": path,
                            "reason": "backslash_separator",
                            "normalized_path": normalized_path,
                        }
                    ],
                }
            if normalized_path != path:
                return {
                    "status": "rejected",
                    "reason": "invalid_patch_file_path",
                    "message": "patch file paths must be normalized POSIX paths",
                    "invalid_paths": [
                        {
                            "path": path,
                            "reason": "not_normalized",
                            "normalized_path": normalized_path,
                        }
                    ],
                }
            normalized_files.append({"path": path, "content": content})
        return {
            "status": "ok",
            "touched_paths": touched_paths,
            "files": normalized_files,
        }

    def _resolve_patch_files(
        self,
        files: list[dict[str, str]],
    ) -> dict[str, Any]:
        repo_root = self.repo_root.resolve(strict=False)
        resolved_files: list[dict[str, Any]] = []
        symlink_indirections: list[dict[str, str]] = []
        for file_item in files:
            target = (repo_root / file_item["path"]).resolve(strict=False)
            if target != repo_root and repo_root not in target.parents:
                return {
                    "status": "rejected",
                    "reason": "path_outside_repo",
                    "message": "patch file resolved outside repo_root",
                    "path": file_item["path"],
                }
            resolved_path = target.relative_to(repo_root).as_posix()
            if resolved_path != file_item["path"]:
                symlink_indirections.append(
                    {
                        "path": file_item["path"],
                        "resolved_path": resolved_path,
                    }
                )
                continue
            resolved_files.append(
                {
                    "path": file_item["path"],
                    "target": target,
                    "content": file_item["content"],
                }
            )
        if symlink_indirections:
            return {
                "status": "rejected",
                "reason": "symlink_indirected_path",
                "message": "patch file resolved to a different in-repo path",
                "resolved_paths": symlink_indirections,
            }
        return {"status": "ok", "files": resolved_files}

    @staticmethod
    def _snapshot_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for file_item in files:
            target = file_item["target"]
            existed = target.exists()
            snapshots.append(
                {
                    "path": file_item["path"],
                    "target": target,
                    "existed": existed,
                    "content": target.read_text(encoding="utf-8") if existed else "",
                }
            )
        return snapshots

    @staticmethod
    def _write_patch_files(files: list[dict[str, Any]]) -> None:
        for file_item in files:
            target = file_item["target"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file_item["content"], encoding="utf-8")

    @staticmethod
    def _restore_snapshots(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        rollback_errors: list[dict[str, str]] = []
        for snapshot in snapshots:
            try:
                target = snapshot["target"]
                if snapshot["existed"]:
                    target.write_text(snapshot["content"], encoding="utf-8")
                elif target.exists():
                    target.unlink()
            except Exception as exc:
                rollback_errors.append(
                    {
                        "path": str(snapshot.get("path", snapshot.get("target", ""))),
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    }
                )
        if rollback_errors:
            return {
                "rollback_status": "partial_restore_failed",
                "rollback_errors": rollback_errors,
            }
        return {"rollback_status": "restored"}

    def _fail_patch(
        self,
        *,
        task_id: str,
        run_id: str,
        reason: str,
        message: str,
        detail: dict[str, Any],
        files: list[str] | None = None,
        deployment_status: str | None = None,
    ) -> dict[str, Any]:
        now_iso = _utc_now_iso()
        self.store.mark_task_status(task_id, "failed", now_iso)
        self.store.record_patch_attempt(
            run_id=run_id,
            task_id=task_id,
            status="failed",
            patch_summary=reason,
            files=files or [],
            created_at=now_iso,
        )
        self.store.record_deployment_event(
            task_id=task_id,
            run_id=run_id,
            status=deployment_status or reason,
            detail=detail,
            created_at=now_iso,
        )
        self.store.finish_repair_run(
            run_id=run_id,
            status=deployment_status or reason,
            finished_at=now_iso,
            summary={
                "reason": reason,
                "message": message,
                "detail": detail,
                "files": files or [],
            },
        )
        return {
            "task_id": task_id,
            "status": "failed",
            "run_id": run_id,
            "reason": reason,
            "message": message,
            "detail": detail,
        }

    def _latest_validation_row(self, venue: str) -> dict[str, Any] | None:
        if not self.validation_db_path.exists():
            return None
        params: list[Any] = []
        where = ""
        if venue:
            where = "WHERE venue = ?"
            params.append(venue)
        try:
            with sqlite3.connect(self.validation_db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    f"""
                    SELECT run_id, venue, payload_json, computed_at
                    FROM validation_runs
                    {where}
                    ORDER BY computed_at DESC, run_id DESC
                    LIMIT 1
                    """,
                    params,
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _work_queue_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
        remediation_plan = payload.get("remediation_plan")
        if remediation_plan is None:
            return {"status": "ok", "work_queue": []}
        if not isinstance(remediation_plan, dict):
            return {
                "status": "error",
                "message": "validation remediation_plan must be an object",
            }
        work_queue = remediation_plan.get("work_queue", [])
        if not isinstance(work_queue, list):
            return {
                "status": "error",
                "message": "validation remediation_plan.work_queue must be a list",
            }
        for item in work_queue:
            if not isinstance(item, dict):
                return {
                    "status": "error",
                    "message": "validation work_queue items must be objects",
                }
        return {"status": "ok", "work_queue": work_queue}

    @staticmethod
    def _task_from_item(
        item: dict[str, Any],
        *,
        venue: str,
        source_validation_run_id: str,
    ) -> dict[str, Any]:
        failure_status = _normalize_failure_status(item)
        if failure_status not in ACTIVE_FAILURE_STATUSES:
            return {"status": "skip"}
        discipline_id = _clean_text(item.get("discipline_id") or item.get("id"))
        if not discipline_id:
            return {
                "status": "error",
                "message": "active validation work_queue item missing discipline_id",
            }
        task_id = _task_id_for_item(
            item,
            venue=venue,
            discipline_id=discipline_id,
        )
        automation_hook = _clean_text(item.get("automation_hook"))
        strategy = repair_strategy_for(
            venue=venue,
            discipline_id=discipline_id,
            automation_hook=automation_hook,
            failure_status=failure_status,
        )
        return {
            "status": "ok",
            "task": RepairTask(
                task_id=task_id,
                venue=venue,
                discipline_id=discipline_id,
                source_validation_run_id=source_validation_run_id,
                status="queued",
                priority=_priority_value(
                    item.get("priority"),
                    failure_status=failure_status,
                ),
                owner=_clean_text(strategy.get("owner")),
                automation_hook=_clean_text(strategy.get("automation_hook")),
                failure_status=failure_status,
                failure_evidence=_failure_evidence(item),
                green_condition=dict(strategy.get("green_condition") or {}),
                allowed_paths=[
                    str(path)
                    for path in strategy.get("allowed_paths", [])
                    if str(path).strip()
                ],
                blocked_paths=[
                    str(path)
                    for path in strategy.get("blocked_paths", [])
                    if str(path).strip()
                ],
            ),
        }


def _run_awaitable_from_sync(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")
