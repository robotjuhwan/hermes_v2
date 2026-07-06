from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from tradecraft.services.jue_wiki import JueWikiService


class JueWikiRepairService:
    def __init__(self, service: JueWikiService) -> None:
        self._service = service

    def run_once(self, *, scope: str | None = None) -> dict[str, Any]:
        findings = self._service.list_lint_findings(scope=scope, status="open")
        actions = []
        for finding in findings:
            action_type = self._action_type_for(finding)
            details = self._details_for_lint_finding(finding)
            existing = self._existing_open_action(
                finding_id=str(finding["finding_id"]),
                action_type=action_type,
            )
            if existing:
                action = self._refresh_existing_action_details(
                    existing=existing,
                    details=details,
                )
            else:
                action = self._service.record_repair_action(
                    finding_id=finding["finding_id"],
                    page_id=finding["page_id"],
                    action_type=action_type,
                    status=self._status_for_lint_action(action_type),
                    details=details,
                )
            actions.append(action)
        lint_actions = list(actions)
        evidence_actions = self._record_evidence_quality_actions(scope=scope)
        actions.extend(evidence_actions)
        repair_queue_shadow_resolved_actions = (
            self._resolve_repair_queue_evidence_shadow_actions(scope=scope)
        )
        actions.extend(repair_queue_shadow_resolved_actions)
        quality_warning_resolved_actions: list[dict[str, Any]] = []
        quality_warning_actions = self._record_quality_warning_effectiveness_actions(
            scope=scope,
            resolved_actions=quality_warning_resolved_actions,
        )
        actions.extend(quality_warning_actions)
        actions.extend(quality_warning_resolved_actions)
        application_repair_queue_pressure_resolved_actions: list[dict[str, Any]] = []
        application_repair_queue_pressure_actions = (
            self._record_application_repair_queue_pressure_actions(
                scope=scope,
                resolved_actions=application_repair_queue_pressure_resolved_actions,
            )
        )
        actions.extend(application_repair_queue_pressure_actions)
        actions.extend(application_repair_queue_pressure_resolved_actions)
        usage_guidance_actions = self._record_usage_guidance_effectiveness_actions(
            scope=scope
        )
        actions.extend(usage_guidance_actions)
        wiki_application_coverage_actions = (
            self._record_wiki_application_coverage_actions(scope=scope)
        )
        actions.extend(wiki_application_coverage_actions)
        research_coverage_issues = self._current_research_coverage_issues(
            scope=scope
        )
        research_coverage_actions = self._record_research_coverage_actions(
            issues=research_coverage_issues,
        )
        actions.extend(research_coverage_actions)
        research_coverage_resolved_actions = (
            self._resolve_stale_research_coverage_actions(
                scope=scope,
                active_finding_ids={
                    str(issue.get("finding_id") or "")
                    for issue in research_coverage_issues
                    if str(issue.get("finding_id") or "")
                },
            )
        )
        research_coverage_resolved_actions.extend(
            self._resolve_stale_research_coverage_shadow_actions(
                scope=scope,
                resolved_source_ids={
                    str(action.get("action_id") or "")
                    for action in research_coverage_resolved_actions
                    if str(action.get("action_id") or "")
                },
            )
        )
        actions.extend(research_coverage_resolved_actions)
        duplicate_repair_actions_resolved = (
            self._resolve_duplicate_open_repair_actions(scope=scope)
        )
        actions.extend(duplicate_repair_actions_resolved)
        research_coverage_resolved_output = [
            *research_coverage_resolved_actions,
            *[
                action
                for action in repair_queue_shadow_resolved_actions
                if "research_coverage_unhealthy"
                in {
                    str(item).strip()
                    for item in list(
                        dict(action.get("details") or {}).get("quality_warnings")
                        or []
                    )
                    if str(item).strip()
                }
            ],
        ]
        repair_queue_pages = self._refresh_repair_queue_pages(
            actions=actions,
            scope=scope,
        )
        return {
            "status": "ok",
            "actions": actions,
            "lint_actions": lint_actions,
            "evidence_quality_actions": evidence_actions,
            "repair_queue_shadow_resolved_actions": (
                repair_queue_shadow_resolved_actions
            ),
            "quality_warning_effectiveness_actions": quality_warning_actions,
            "quality_warning_effectiveness_resolved_actions": (
                quality_warning_resolved_actions
            ),
            "application_repair_queue_pressure_actions": (
                application_repair_queue_pressure_actions
            ),
            "application_repair_queue_pressure_resolved_actions": (
                application_repair_queue_pressure_resolved_actions
            ),
            "usage_guidance_effectiveness_actions": usage_guidance_actions,
            "wiki_application_coverage_actions": wiki_application_coverage_actions,
            "research_coverage_actions": research_coverage_actions,
            "research_coverage_resolved_actions": research_coverage_resolved_output,
            "duplicate_repair_actions_resolved": duplicate_repair_actions_resolved,
            "repair_queue_pages": repair_queue_pages,
        }

    def _refresh_repair_queue_pages(
        self,
        *,
        actions: list[dict[str, Any]],
        scope: str | None,
    ) -> dict[str, int]:
        scopes: set[str] = set()
        clean_scope = str(scope or "").strip().lower()
        if clean_scope in {"kis", "binance"}:
            scopes.add(clean_scope)
        for action in actions:
            page_id = str(action.get("page_id") or "").strip().lower()
            page_scope = page_id.split(".", 1)[0] if "." in page_id else ""
            if page_scope in {"kis", "binance"}:
                scopes.add(page_scope)
            details = action.get("details") if isinstance(action.get("details"), dict) else {}
            for key in ("scope", "decision_scope"):
                detail_scope = str(details.get(key) or "").strip().lower()
                if detail_scope in {"kis", "binance"}:
                    scopes.add(detail_scope)
        refreshed: dict[str, int] = {}
        for target_scope in sorted(scopes):
            refreshed[target_scope] = self._service._rebuild_repair_queue_page(
                scope=target_scope
            )
        return refreshed

    def _action_type_for(self, finding: dict[str, Any]) -> str:
        finding_type = str(finding.get("finding_type") or "")
        if finding_type in {
            "stale_page",
            "missing_sources",
            "oversized_page",
        }:
            return "rebuild_page"
        if finding_type == "source_ref_identity_gap":
            return "repair_source_ref_identity_gap"
        if finding_type == "scope_leakage":
            return "repair_scope_leakage"
        return "mark_unresolved"

    @staticmethod
    def _status_for_lint_action(action_type: str) -> str:
        return "unresolved" if action_type == "mark_unresolved" else "scheduled"

    def _details_for_lint_finding(self, finding: dict[str, Any]) -> dict[str, Any]:
        finding_type = str(finding.get("finding_type") or "")
        evidence = (
            finding.get("evidence")
            if isinstance(finding.get("evidence"), dict)
            else {}
        )
        details = {
            key: value
            for key, value in {
                "finding_type": finding_type,
                "severity": str(finding.get("severity") or ""),
                "message": str(finding.get("message") or ""),
                "repair_action": self._repair_action_for_lint_finding(finding_type),
            }.items()
            if value not in (None, "", [], {})
        }
        if finding_type == "source_ref_identity_gap":
            details["gap_count"] = int(evidence.get("gap_count") or 0)
            details["examples"] = [
                dict(item)
                for item in list(evidence.get("examples") or [])[:8]
                if isinstance(item, dict)
            ]
        return details

    @staticmethod
    def _repair_action_for_lint_finding(finding_type: str) -> str:
        actions = {
            "source_ref_identity_gap": (
                "repair source_type/source_id provenance before strong wiki reuse"
            ),
            "scope_leakage": "remove cross-scope content before strong wiki reuse",
            "missing_sources": "attach audit-ready source refs before strong wiki reuse",
            "oversized_page": "compact oversized wiki page before prompt injection",
            "stale_page": "rebuild stale wiki page before strong reuse",
        }
        return actions.get(
            str(finding_type or ""),
            "inspect unresolved wiki lint finding before strong reuse",
        )

    def _record_evidence_quality_actions(
        self,
        *,
        scope: str | None,
        limit: int = 64,
    ) -> list[dict[str, Any]]:
        pages = self._service.search_pages(
            scope=scope,
            include_content=False,
        )
        actions: list[dict[str, Any]] = []
        for page in pages:
            page_id = str(page.get("page_id") or "")
            if not page_id or page_id.endswith(".research.evidence_quality"):
                continue
            if page_id.endswith(".research.repair_queue"):
                continue
            symbols = [
                str(symbol).strip().upper()
                for symbol in list(page.get("symbols") or [])[:8]
                if str(symbol).strip()
            ]
            for ref in self._service._flatten_source_refs(page.get("source_refs")):
                if not isinstance(ref, dict):
                    continue
                if str(ref.get("source_type") or "") == "wiki_repair_queue":
                    continue
                status = str(ref.get("quality_status") or "").strip().lower()
                warnings = [
                    str(item).strip()
                    for item in list(ref.get("quality_warnings") or [])
                    if str(item).strip()
                ]
                evidence_quality = (
                    ref.get("evidence_quality")
                    if isinstance(ref.get("evidence_quality"), dict)
                    else {}
                )
                if not status:
                    status = self._service._quality_status_from_evidence_quality(
                        evidence_quality
                    )
                if not warnings:
                    warnings = self._service._quality_warnings_from_evidence_quality(
                        evidence_quality
                    )
                if status not in {"weak", "partial"} and not warnings:
                    continue
                finding_id = self._evidence_finding_id(
                    page_id=page_id,
                    source_type=str(ref.get("source_type") or ""),
                    source_id=str(ref.get("source_id") or ""),
                    quality_status=status or "unknown",
                    warnings=warnings,
                )
                action_type = self._action_type_for_evidence_quality(
                    source_type=str(ref.get("source_type") or ""),
                    warnings=warnings,
                )
                existing = self._existing_open_action(
                    finding_id=finding_id,
                    action_type=action_type,
                )
                if existing:
                    actions.append(existing)
                    continue
                action = self._service.record_repair_action(
                    finding_id=finding_id,
                    page_id=page_id,
                    action_type=action_type,
                    status="scheduled",
                    details={
                        "finding_type": "evidence_quality",
                        "scope": str(page.get("scope") or ""),
                        "symbols": symbols,
                        "source_type": str(ref.get("source_type") or ""),
                        "source_id": str(ref.get("source_id") or ""),
                        "quality_status": status or "unknown",
                        "quality_warnings": warnings[:8],
                        "repair_action": self._repair_action_for_evidence_quality(
                            status=status,
                            warnings=warnings,
                        ),
                    },
                )
                actions.append(action)
                if len(actions) >= max(int(limit), 0):
                    return actions
        return actions

    def _record_research_coverage_actions(
        self,
        *,
        issues: list[dict[str, Any]],
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for issue in issues:
            action_type = str(issue.get("action_type") or "")
            finding_id = str(issue.get("finding_id") or "")
            if not action_type or not finding_id:
                continue
            existing = self._existing_open_action(
                finding_id=finding_id,
                action_type=action_type,
            )
            if existing:
                actions.append(existing)
                continue
            action = self._service.record_repair_action(
                finding_id=finding_id,
                page_id=str(issue.get("page_id") or ""),
                action_type=action_type,
                status="scheduled",
                details=dict(issue.get("details") or {}),
            )
            actions.append(action)
            if len(actions) >= max(int(limit), 0):
                return actions
        return actions

    def _resolve_repair_queue_evidence_shadow_actions(
        self,
        *,
        scope: str | None,
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        self._service.initialize()
        with self._service._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                  AND action_type = 'cross_check_evidence_quality'
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
        resolved: list[dict[str, Any]] = []
        for row in rows:
            details: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row["details_json"] or "{}"))
                if isinstance(parsed, dict):
                    details = parsed
            except json.JSONDecodeError:
                details = {}
            if str(details.get("source_type") or "") != "wiki_repair_queue":
                continue
            row_scope = str(details.get("scope") or "").strip().lower()
            page_scope = str(row["page_id"] or "").split(".", 1)[0].lower()
            if clean_scope in {"kis", "binance"} and clean_scope not in {
                row_scope,
                page_scope,
            }:
                continue
            existing = {
                "action_id": str(row["action_id"]),
                "finding_id": str(row["finding_id"]),
                "page_id": str(row["page_id"]),
                "action_type": str(row["action_type"]),
                "status": str(row["status"]),
                "details": details,
                "created_at": str(row["created_at"] or ""),
                "finished_at": str(row["finished_at"] or ""),
                "error_message": str(row["error_message"] or ""),
            }
            resolved.append(
                self._resolve_existing_action_details(
                    existing=existing,
                    details={
                        **details,
                        "resolved_by": "repair_queue_meta_shadow_clean",
                    },
                )
            )
        return resolved

    def _current_research_coverage_issues(
        self,
        *,
        scope: str | None,
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        scopes = (
            [clean_scope]
            if clean_scope in {"kis", "binance"}
            else ["kis", "binance"]
        )
        issues: list[dict[str, Any]] = []
        for target_scope in scopes:
            for row in self._service._research_coverage_rows(scope=target_scope):
                source_id = str(row.get("source_id") or "").strip()
                reported_status = str(row.get("status") or "").strip().lower()
                issue = self._research_coverage_issue(row)
                status = str(issue.get("status") or "").strip().lower()
                if not source_id or status in {"", "missing_path"}:
                    continue
                action_type = self._action_type_for_research_coverage(status)
                finding_id = self._research_coverage_finding_id(
                    scope=target_scope,
                    source_id=source_id,
                    status=status,
                    path=str(row.get("path") or ""),
                )
                repair_action = self._repair_action_for_research_coverage(
                    source_id=source_id,
                    status=status,
                )
                issues.append(
                    {
                        "finding_id": finding_id,
                        "page_id": f"{target_scope}.research.coverage",
                        "action_type": action_type,
                        "details": {
                            "finding_type": "research_coverage",
                            "scope": target_scope,
                            "source_id": source_id,
                            "source_status": status,
                            "source_reported_status": reported_status,
                            "path": str(row.get("path") or ""),
                            "reason": str(
                                issue.get("reason") or row.get("reason") or ""
                            ),
                            "rows": int(row.get("rows") or 0),
                            "symbols": int(row.get("symbols") or 0),
                            "primary_table": str(row.get("primary_table") or ""),
                            "latest_at": str(row.get("latest_at") or ""),
                            "table_issues": list(issue.get("table_issues") or []),
                            "quality_warnings": ["research_coverage_unhealthy"],
                            "repair_action": repair_action,
                            "repair_targets": [
                                {
                                    "source_id": source_id,
                                    "scope": target_scope,
                                    "recommended_action": repair_action,
                                }
                            ],
                        },
                    }
                )
        return issues

    def _resolve_stale_research_coverage_actions(
        self,
        *,
        scope: str | None,
        active_finding_ids: set[str],
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        self._service.initialize()
        with self._service._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                  AND (
                    finding_id LIKE 'research_coverage:%'
                    OR action_type IN (
                        'restore_research_source_db',
                        'repair_research_source_schema',
                        'populate_research_source_rows',
                        'inspect_research_source_coverage'
                    )
                  )
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
        resolved: list[dict[str, Any]] = []
        for row in rows:
            details: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row["details_json"] or "{}"))
                if isinstance(parsed, dict):
                    details = parsed
            except json.JSONDecodeError:
                details = {}
            if str(details.get("finding_type") or "") != "research_coverage":
                continue
            row_scope = str(details.get("scope") or "").strip().lower()
            page_scope = str(row["page_id"] or "").split(".", 1)[0].lower()
            if clean_scope in {"kis", "binance"} and clean_scope not in {
                row_scope,
                page_scope,
            }:
                continue
            finding_id = str(row["finding_id"] or "")
            if finding_id in active_finding_ids:
                continue
            existing = {
                "action_id": str(row["action_id"]),
                "finding_id": finding_id,
                "page_id": str(row["page_id"]),
                "action_type": str(row["action_type"]),
                "status": str(row["status"]),
                "details": details,
                "created_at": str(row["created_at"] or ""),
                "finished_at": str(row["finished_at"] or ""),
                "error_message": str(row["error_message"] or ""),
            }
            resolved.append(
                self._resolve_existing_action_details(
                    existing=existing,
                    details={
                        **details,
                        "resolved_by": "research_coverage_clean",
                    },
                )
            )
        return resolved

    def _resolve_stale_research_coverage_shadow_actions(
        self,
        *,
        scope: str | None,
        resolved_source_ids: set[str],
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        self._service.initialize()
        with self._service._connect() as conn:
            resolved_rows = conn.execute(
                """
                SELECT action_id, page_id, details_json
                FROM wiki_repair_actions
                WHERE status = 'resolved'
                  AND (
                    finding_id LIKE 'research_coverage:%'
                    OR action_type IN (
                        'restore_research_source_db',
                        'repair_research_source_schema',
                        'populate_research_source_rows',
                        'inspect_research_source_coverage'
                    )
                  )
                """
            ).fetchall()
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                  AND action_type = 'cross_check_evidence_quality'
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
        source_ids = set(resolved_source_ids)
        for row in resolved_rows:
            details: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row["details_json"] or "{}"))
                if isinstance(parsed, dict):
                    details = parsed
            except json.JSONDecodeError:
                details = {}
            if str(details.get("finding_type") or "") != "research_coverage":
                continue
            row_scope = str(details.get("scope") or "").strip().lower()
            page_scope = str(row["page_id"] or "").split(".", 1)[0].lower()
            if clean_scope in {"kis", "binance"} and clean_scope not in {
                row_scope,
                page_scope,
            }:
                continue
            action_id = str(row["action_id"] or "")
            if action_id:
                source_ids.add(action_id)
        if not source_ids:
            return []
        resolved: list[dict[str, Any]] = []
        pending = list(rows)
        while pending:
            next_pending = []
            made_progress = False
            for row in pending:
                resolved_action = self._resolve_stale_research_coverage_shadow_row(
                    row=row,
                    clean_scope=clean_scope,
                    resolved_source_ids=source_ids,
                )
                if not resolved_action:
                    next_pending.append(row)
                    continue
                resolved.append(resolved_action)
                source_ids.add(str(resolved_action.get("action_id") or ""))
                made_progress = True
            if not made_progress:
                break
            pending = next_pending
        return resolved

    def _resolve_stale_research_coverage_shadow_row(
        self,
        *,
        row: Any,
        clean_scope: str,
        resolved_source_ids: set[str],
    ) -> dict[str, Any]:
        details: dict[str, Any] = {}
        try:
            parsed = json.loads(str(row["details_json"] or "{}"))
            if isinstance(parsed, dict):
                details = parsed
        except json.JSONDecodeError:
            details = {}
        warnings = {
            str(item).strip()
            for item in list(details.get("quality_warnings") or [])
            if str(item).strip()
        }
        if "research_coverage_unhealthy" not in warnings:
            return {}
        if str(details.get("source_type") or "") != "wiki_repair_queue":
            return {}
        if str(details.get("source_id") or "") not in resolved_source_ids:
            return {}
        row_scope = str(details.get("scope") or "").strip().lower()
        page_scope = str(row["page_id"] or "").split(".", 1)[0].lower()
        if clean_scope in {"kis", "binance"} and clean_scope not in {
            row_scope,
            page_scope,
        }:
            return {}
        existing = {
            "action_id": str(row["action_id"]),
            "finding_id": str(row["finding_id"]),
            "page_id": str(row["page_id"]),
            "action_type": str(row["action_type"]),
            "status": str(row["status"]),
            "details": details,
            "created_at": str(row["created_at"] or ""),
            "finished_at": str(row["finished_at"] or ""),
            "error_message": str(row["error_message"] or ""),
        }
        return self._resolve_existing_action_details(
            existing=existing,
            details={
                **details,
                "resolved_by": "research_coverage_shadow_clean",
            },
        )

    @staticmethod
    def _research_coverage_issue(row: dict[str, Any]) -> dict[str, Any]:
        status = str(row.get("status") or "").strip().lower()
        if status and status != "ok":
            return {
                "status": status,
                "reason": str(row.get("reason") or ""),
                "table_issues": [],
            }
        table_issues = [
            {
                "table": str(detail.get("table") or ""),
                "status": str(detail.get("status") or "").strip().lower(),
            }
            for detail in list(row.get("tables") or [])
            if isinstance(detail, dict)
            and str(detail.get("status") or "").strip().lower() not in {"", "ok"}
        ]
        table_issues = [
            issue
            for issue in table_issues
            if issue.get("table") and issue.get("status")
        ]
        if not table_issues:
            return {"status": "", "reason": "", "table_issues": []}
        status_rank = {
            "error": 0,
            "missing_table": 1,
            "empty": 2,
        }
        primary_issue = sorted(
            table_issues,
            key=lambda item: (
                status_rank.get(str(item.get("status") or ""), 9),
                str(item.get("table") or ""),
            ),
        )[0]
        status = str(primary_issue.get("status") or "inspect").strip().lower()
        issue_summary = ", ".join(
            f"{issue['table']}={issue['status']}" for issue in table_issues[:6]
        )
        return {
            "status": status,
            "reason": f"table-level research coverage issue: {issue_summary}",
            "table_issues": table_issues[:12],
        }

    @staticmethod
    def _action_type_for_research_coverage(status: str) -> str:
        clean_status = str(status or "").strip().lower()
        if clean_status in {"missing_db", "error"}:
            return "restore_research_source_db"
        if clean_status == "missing_table":
            return "repair_research_source_schema"
        if clean_status == "empty":
            return "populate_research_source_rows"
        return "inspect_research_source_coverage"

    @staticmethod
    def _repair_action_for_research_coverage(
        *,
        source_id: str,
        status: str,
    ) -> str:
        clean_source = str(source_id or "").strip()
        clean_status = str(status or "").strip().lower()
        if clean_status in {"missing_db", "error"}:
            return f"restore or restart {clean_source} collector before trusting wiki coverage"
        if clean_status == "missing_table":
            return f"repair {clean_source} schema migration before rebuilding wiki"
        if clean_status == "empty":
            return f"collect fresh {clean_source} rows before relying on research memory"
        return f"inspect {clean_source} coverage before strong block sizing"

    @staticmethod
    def _research_coverage_finding_id(
        *,
        scope: str,
        source_id: str,
        status: str,
        path: str,
    ) -> str:
        raw = json.dumps(
            {
                "scope": scope,
                "source_id": source_id,
                "status": status,
                "path": path,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"research_coverage:{digest}"

    def _record_quality_warning_effectiveness_actions(
        self,
        *,
        scope: str | None,
        resolved_actions: list[dict[str, Any]] | None = None,
        limit: int = 64,
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        effectiveness = self._service.page_effectiveness_map(
            decision_scope=clean_scope,
        )
        actions: list[dict[str, Any]] = []
        for page_id, metric in sorted(effectiveness.items()):
            page_id = str(page_id or "")
            if not page_id.startswith("quality_warning."):
                continue
            if str(metric.get("status") or "").strip().lower() != "degraded":
                continue
            decision_scope = str(metric.get("decision_scope") or "").strip().lower()
            if clean_scope and decision_scope != clean_scope:
                continue
            warning = page_id.removeprefix("quality_warning.")
            finding_id = self._quality_warning_effectiveness_finding_id(
                page_id=page_id,
                decision_scope=decision_scope,
                venue=str(metric.get("venue") or ""),
                horizon=str(metric.get("horizon") or ""),
            )
            action_type = "repair_quality_warning_effectiveness"
            details = self._quality_warning_effectiveness_details(
                metric=metric,
                warning=warning,
                decision_scope=decision_scope,
            )
            impacted_pages = self._impacted_pages_for_quality_warning(
                scope=decision_scope or clean_scope,
                warning=warning,
            )
            if not impacted_pages:
                if resolved_actions is not None:
                    resolved_actions.extend(
                        self._resolve_quality_warning_effectiveness_actions_without_impacts(
                            scope=decision_scope or clean_scope,
                            warning=warning,
                            details=details,
                        )
                    )
                continue
            if impacted_pages:
                details.update(
                    self._quality_warning_impact_details(
                        warning=warning,
                        impacted_pages=impacted_pages,
                    )
                )
            existing = self._existing_open_action(
                finding_id=finding_id,
                action_type=action_type,
            )
            if existing:
                actions.append(
                    self._refresh_existing_action_details(
                        existing=existing,
                        details=details,
                    )
                )
                continue
            action = self._service.record_repair_action(
                finding_id=finding_id,
                page_id=page_id,
                action_type=action_type,
                status="scheduled",
                details=details,
            )
            actions.append(action)
            if len(actions) >= max(int(limit), 0):
                return actions
        return actions

    def _record_application_repair_queue_pressure_actions(
        self,
        *,
        scope: str | None,
        resolved_actions: list[dict[str, Any]] | None = None,
        limit: int = 64,
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        effectiveness = self._service.page_effectiveness_map(
            decision_scope=clean_scope,
        )
        actions: list[dict[str, Any]] = []
        active_finding_ids: set[str] = set()
        for page_id, metric in sorted(effectiveness.items()):
            page_id = str(page_id or "")
            reasons = self._metric_reasons(metric)
            if "application_repair_queue_pressure" not in set(reasons):
                continue
            if str(metric.get("status") or "").strip().lower() != "degraded":
                continue
            decision_scope = str(metric.get("decision_scope") or "").strip().lower()
            if clean_scope and decision_scope != clean_scope:
                continue
            finding_id = self._application_repair_queue_pressure_finding_id(
                page_id=page_id,
                decision_scope=decision_scope,
                venue=str(metric.get("venue") or ""),
                horizon=str(metric.get("horizon") or ""),
            )
            active_finding_ids.add(finding_id)
            action_type = "repair_application_repair_queue_pressure"
            details = self._application_repair_queue_pressure_details(
                metric=metric,
                page_id=page_id,
                reasons=reasons,
                decision_scope=decision_scope,
            )
            existing = self._existing_open_action(
                finding_id=finding_id,
                action_type=action_type,
            )
            if existing:
                actions.append(
                    self._refresh_existing_action_details(
                        existing=existing,
                        details=details,
                    )
                )
                continue
            actions.append(
                self._service.record_repair_action(
                    finding_id=finding_id,
                    page_id=page_id,
                    action_type=action_type,
                    status="scheduled",
                    details=details,
                )
            )
            if len(actions) >= max(int(limit), 0):
                return actions
        if resolved_actions is not None:
            resolved_actions.extend(
                self._resolve_application_repair_queue_pressure_actions_without_pressure(
                    scope=clean_scope,
                    active_finding_ids=active_finding_ids,
                )
            )
        return actions

    def _resolve_application_repair_queue_pressure_actions_without_pressure(
        self,
        *,
        scope: str,
        active_finding_ids: set[str],
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        self._service.initialize()
        with self._service._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                  AND action_type = 'repair_application_repair_queue_pressure'
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
        resolved: list[dict[str, Any]] = []
        for row in rows:
            action = self._repair_action_from_row(row)
            if str(action.get("finding_id") or "") in active_finding_ids:
                continue
            if clean_scope and not self._repair_action_matches_scope(
                action,
                scope=clean_scope,
            ):
                continue
            details = (
                dict(action.get("details"))
                if isinstance(action.get("details"), dict)
                else {}
            )
            resolved.append(
                self._resolve_existing_action_details(
                    existing=action,
                    details={
                        **details,
                        "resolved_by": "application_repair_queue_clean",
                        "resolved_application_repair_queue_pressure": True,
                    },
                )
            )
        return resolved

    def _resolve_quality_warning_effectiveness_actions_without_impacts(
        self,
        *,
        scope: str,
        warning: str,
        details: dict[str, Any],
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        clean_warning = str(warning or "").strip()
        if not clean_warning:
            return []
        page_id = f"quality_warning.{clean_warning}"
        self._service.initialize()
        with self._service._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                  AND action_type = 'repair_quality_warning_effectiveness'
                  AND page_id = ?
                ORDER BY created_at ASC, action_id ASC
                """,
                (page_id,),
            ).fetchall()
        resolved: list[dict[str, Any]] = []
        for row in rows:
            existing_details: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row["details_json"] or "{}"))
                if isinstance(parsed, dict):
                    existing_details = parsed
            except json.JSONDecodeError:
                existing_details = {}
            row_scope = str(
                existing_details.get("decision_scope")
                or existing_details.get("scope")
                or ""
            ).strip().lower()
            if clean_scope in {"kis", "binance"} and row_scope not in {
                "",
                clean_scope,
            }:
                continue
            existing = {
                "action_id": str(row["action_id"]),
                "finding_id": str(row["finding_id"]),
                "page_id": str(row["page_id"]),
                "action_type": str(row["action_type"]),
                "status": str(row["status"]),
                "details": existing_details,
                "created_at": str(row["created_at"] or ""),
                "finished_at": str(row["finished_at"] or ""),
                "error_message": str(row["error_message"] or ""),
            }
            resolved.append(
                self._resolve_existing_action_details(
                    existing=existing,
                    details={
                        **existing_details,
                        **details,
                        "resolved_by": "quality_warning_no_current_impacted_pages",
                    },
                )
            )
        return resolved

    def _resolve_duplicate_open_repair_actions(
        self,
        *,
        scope: str | None,
    ) -> list[dict[str, Any]]:
        self._service.initialize()
        with self._service._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE status IN ('scheduled', 'unresolved')
                  AND action_type IN (
                    'refresh_symbol_financials',
                    'refresh_symbol_fundamentals',
                    'refresh_symbol_quote',
                    'refresh_requested_symbol_summary'
                  )
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
        clean_scope = str(scope or "").strip().lower()
        grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in rows:
            action = self._repair_action_from_row(row)
            if clean_scope and not self._repair_action_matches_scope(
                action,
                scope=clean_scope,
            ):
                continue
            key = self._duplicate_repair_action_key(action)
            if not key:
                continue
            grouped.setdefault(key, []).append(action)

        resolved: list[dict[str, Any]] = []
        for actions in grouped.values():
            if len(actions) < 2:
                continue
            canonical = actions[0]
            for duplicate in actions[1:]:
                resolved_action = self._resolve_duplicate_repair_action(
                    duplicate=duplicate,
                    canonical=canonical,
                )
                if resolved_action:
                    resolved.append(resolved_action)
        resolved.sort(key=lambda row: str(row.get("action_id") or ""))
        return resolved

    @staticmethod
    def _repair_action_from_row(row: Any) -> dict[str, Any]:
        details: dict[str, Any] = {}
        try:
            parsed = json.loads(str(row["details_json"] or "{}"))
            if isinstance(parsed, dict):
                details = parsed
        except json.JSONDecodeError:
            details = {}
        return {
            "action_id": str(row["action_id"]),
            "finding_id": str(row["finding_id"]),
            "page_id": str(row["page_id"]),
            "action_type": str(row["action_type"]),
            "status": str(row["status"]),
            "details": details,
            "created_at": str(row["created_at"] or ""),
            "finished_at": str(row["finished_at"] or ""),
            "error_message": str(row["error_message"] or ""),
        }

    @staticmethod
    def _repair_action_matches_scope(
        action: dict[str, Any],
        *,
        scope: str,
    ) -> bool:
        page_id = str(action.get("page_id") or "").strip().lower()
        if page_id.startswith(f"{scope}."):
            return True
        details = action.get("details") if isinstance(action.get("details"), dict) else {}
        return any(
            str(details.get(key) or "").strip().lower() == scope
            for key in ("scope", "decision_scope", "source_scope")
        )

    @staticmethod
    def _duplicate_repair_action_key(action: dict[str, Any]) -> tuple[Any, ...]:
        details = action.get("details") if isinstance(action.get("details"), dict) else {}
        symbols = tuple(
            sorted(
                {
                    str(item).strip().upper()
                    for item in list(details.get("symbols") or [])
                    if str(item).strip()
                }
            )
        )
        if not symbols:
            return ()
        warnings = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in list(details.get("quality_warnings") or [])
                    if str(item).strip()
                }
            )
        )
        repair_action = str(details.get("repair_action") or "").strip()
        return (
            str(action.get("page_id") or "").strip(),
            str(action.get("action_type") or "").strip(),
            symbols,
            warnings,
            repair_action,
        )

    def _resolve_duplicate_repair_action(
        self,
        *,
        duplicate: dict[str, Any],
        canonical: dict[str, Any],
    ) -> dict[str, Any]:
        duplicate_id = str(duplicate.get("action_id") or "")
        canonical_id = str(canonical.get("action_id") or "")
        if not duplicate_id or not canonical_id or duplicate_id == canonical_id:
            return {}
        resolved_at = datetime.now(timezone.utc).isoformat()
        details = (
            dict(duplicate.get("details"))
            if isinstance(duplicate.get("details"), dict)
            else {}
        )
        details.update(
            {
                "resolved_at": resolved_at,
                "resolved_by": "duplicate_repair_action_compacted",
                "canonical_action_id": canonical_id,
            }
        )
        self._service.initialize()
        with self._service._connect() as conn:
            conn.execute(
                """
                UPDATE wiki_repair_actions
                SET status = 'resolved',
                    finished_at = CASE
                        WHEN COALESCE(finished_at, '') = '' THEN ?
                        ELSE finished_at
                    END,
                    details_json = ?
                WHERE action_id = ?
                  AND status IN ('scheduled', 'unresolved')
                """,
                (
                    resolved_at,
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    duplicate_id,
                ),
            )
        return {
            **duplicate,
            "status": "resolved",
            "details": details,
            "finished_at": resolved_at,
        }

    def _record_usage_guidance_effectiveness_actions(
        self,
        *,
        scope: str | None,
        limit: int = 64,
    ) -> list[dict[str, Any]]:
        clean_scope = str(scope or "").strip().lower()
        effectiveness = self._service.page_effectiveness_map(
            decision_scope=clean_scope,
        )
        actions: list[dict[str, Any]] = []
        for page_id, metric in sorted(effectiveness.items()):
            page_id = str(page_id or "")
            if not page_id.startswith("usage_guidance."):
                continue
            if str(metric.get("status") or "").strip().lower() != "degraded":
                continue
            decision_scope = str(metric.get("decision_scope") or "").strip().lower()
            if clean_scope and decision_scope != clean_scope:
                continue
            usage_guidance_id = page_id.removeprefix("usage_guidance.")
            finding_id = self._usage_guidance_effectiveness_finding_id(
                page_id=page_id,
                decision_scope=decision_scope,
                venue=str(metric.get("venue") or ""),
                horizon=str(metric.get("horizon") or ""),
            )
            action_type = "repair_usage_guidance_contract"
            details = self._usage_guidance_effectiveness_details(
                metric=metric,
                usage_guidance_id=usage_guidance_id,
                decision_scope=decision_scope,
            )
            existing = self._existing_open_action(
                finding_id=finding_id,
                action_type=action_type,
            )
            if existing:
                actions.append(
                    self._refresh_existing_action_details(
                        existing=existing,
                        details=details,
                    )
                )
                continue
            action = self._service.record_repair_action(
                finding_id=finding_id,
                page_id=page_id,
                action_type=action_type,
                status="scheduled",
                details=details,
            )
            actions.append(action)
            if len(actions) >= max(int(limit), 0):
                return actions
        return actions

    def _record_wiki_application_coverage_actions(
        self,
        *,
        scope: str | None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        from tradecraft.services.jue_wiki_application import JueWikiApplicationService

        application = JueWikiApplicationService(self._service)
        target_scopes = self._wiki_application_repair_scopes(
            application=application,
            scope=scope,
        )
        actions: list[dict[str, Any]] = []
        for target_scope in target_scopes[: max(int(limit), 0)]:
            coverage_payload = application.project_wiki_application_coverage(
                decision_scope=target_scope
            )
            coverage = (
                coverage_payload.get("coverage")
                if isinstance(coverage_payload.get("coverage"), dict)
                else {}
            )
            missing_count = int(
                coverage.get("closed_block_outcomes_without_horizon") or 0
            )
            missing_pct = float(
                coverage.get("closed_block_outcomes_without_horizon_pct") or 0.0
            )
            has_horizon_alert = any(
                isinstance(row, dict)
                and str(row.get("code") or "") == "wiki_outcome_horizon_missing"
                for row in list(coverage_payload.get("alerts") or [])
            )
            action_type = "reproject_closed_block_outcome_horizons"
            finding_id = f"wiki_application_coverage:{target_scope}:outcome_horizon"
            page_id = f"{target_scope}.application.closed_block_outcomes"
            existing = self._existing_open_action(
                finding_id=finding_id,
                action_type=action_type,
            )
            if missing_count <= 0 and missing_pct <= 0.0 and not has_horizon_alert:
                if existing:
                    actions.append(
                        self._resolve_existing_action_details(
                            existing=existing,
                            details={
                                **dict(existing.get("details") or {}),
                                "finding_type": "wiki_application_coverage",
                                "decision_scope": target_scope,
                                "closed_block_outcomes_without_horizon": 0,
                                "closed_block_outcomes_without_horizon_pct": 0.0,
                                "resolved_by": "wiki_application_coverage_clean",
                                "resolved_closed_block_outcome_horizon_gap": True,
                            },
                        )
                    )
                continue
            details = {
                "finding_type": "wiki_application_coverage",
                "decision_scope": target_scope,
                "closed_block_outcomes_without_horizon": missing_count,
                "closed_block_outcomes_without_horizon_pct": missing_pct,
                "quality_warnings": ["closed_block_outcome_horizon_missing"],
                "repair_action": (
                    "reproject closed block outcomes so page effectiveness is "
                    "credited to the block horizon or crypto lane"
                ),
                "reasons": [
                    f"closed_block_outcomes_without_horizon:{missing_count}",
                    f"closed_block_outcomes_without_horizon_pct:{missing_pct:.1f}",
                ],
                "repair_targets": [
                    {
                        "page_id": page_id,
                        "recommended_action": (
                            "reproject_closed_block_outcomes_with_block_horizon_or_lane"
                        ),
                    }
                ],
            }
            if existing:
                actions.append(
                    self._refresh_existing_action_details(
                        existing=existing,
                        details=details,
                    )
                )
                continue
            actions.append(
                self._service.record_repair_action(
                    finding_id=finding_id,
                    page_id=page_id,
                    action_type=action_type,
                    status="scheduled",
                    details=details,
                )
            )
        return actions

    def _wiki_application_repair_scopes(
        self,
        *,
        application: Any,
        scope: str | None,
    ) -> list[str]:
        clean_scope = str(scope or "").strip().lower()
        if clean_scope and clean_scope != "all":
            return [clean_scope]
        coverage_payload = application.project_wiki_application_coverage(
            decision_scope=None
        )
        scopes: list[str] = []
        for row in list(coverage_payload.get("alerts") or []):
            if not isinstance(row, dict):
                continue
            if str(row.get("code") or "") != "wiki_outcome_horizon_missing":
                continue
            alert_scope = str(row.get("decision_scope") or "").strip().lower()
            if alert_scope and alert_scope not in scopes:
                scopes.append(alert_scope)
        for existing_scope in self._open_wiki_application_horizon_action_scopes():
            if existing_scope not in scopes:
                scopes.append(existing_scope)
        return scopes

    def _open_wiki_application_horizon_action_scopes(self) -> list[str]:
        self._service.initialize()
        with self._service._connect() as conn:
            rows = conn.execute(
                """
                SELECT finding_id, details_json
                FROM wiki_repair_actions
                WHERE action_type = 'reproject_closed_block_outcome_horizons'
                  AND status IN ('scheduled', 'unresolved')
                ORDER BY created_at ASC, action_id ASC
                """
            ).fetchall()
        scopes: list[str] = []
        for row in rows:
            details: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row["details_json"] or "{}"))
                if isinstance(parsed, dict):
                    details = parsed
            except json.JSONDecodeError:
                details = {}
            scope = str(details.get("decision_scope") or "").strip().lower()
            if not scope:
                parts = str(row["finding_id"] or "").split(":")
                if len(parts) >= 2:
                    scope = parts[1].strip().lower()
            if scope and scope not in scopes:
                scopes.append(scope)
        return scopes

    def _impacted_pages_for_quality_warning(
        self,
        *,
        scope: str,
        warning: str,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        clean_warning = str(warning or "").strip()
        if not clean_warning:
            return []
        pages = self._service.search_pages(
            scope=str(scope or "").strip().lower() or None,
            include_content=False,
        )
        impacted: list[dict[str, Any]] = []
        for page in pages:
            page_id = str(page.get("page_id") or "")
            if not page_id or self._is_quality_warning_meta_page(page_id):
                continue
            matched_refs: list[dict[str, str]] = []
            for ref in self._service._flatten_source_refs(page.get("source_refs")):
                if not isinstance(ref, dict):
                    continue
                warnings = {
                    str(item).strip()
                    for item in list(ref.get("quality_warnings") or [])
                    if str(item).strip()
                }
                evidence_quality = (
                    ref.get("evidence_quality")
                    if isinstance(ref.get("evidence_quality"), dict)
                    else {}
                )
                if not warnings:
                    warnings = set(
                        self._service._quality_warnings_from_evidence_quality(
                            evidence_quality
                        )
                    )
                if clean_warning not in warnings:
                    continue
                matched_refs.append(
                    {
                        "page_id": page_id,
                        "source_type": str(ref.get("source_type") or ""),
                        "source_id": str(ref.get("source_id") or ""),
                        "quality_status": str(ref.get("quality_status") or ""),
                    }
                )
            if not matched_refs:
                continue
            impacted.append(
                {
                    "page_id": page_id,
                    "symbols": [
                        str(symbol).strip().upper()
                        for symbol in list(page.get("symbols") or [])[:8]
                        if str(symbol).strip()
                    ],
                    "source_refs": matched_refs[:4],
                }
            )
            if len(impacted) >= max(int(limit), 0):
                return impacted
        return impacted

    @staticmethod
    def _is_quality_warning_meta_page(page_id: str) -> bool:
        clean_page_id = str(page_id or "").strip()
        if not clean_page_id:
            return True
        return (
            clean_page_id.startswith("quality_warning.")
            or clean_page_id.endswith(".research.repair_queue")
            or clean_page_id.endswith(".research.evidence_quality")
            or clean_page_id.endswith(".research.coverage")
        )

    def _quality_warning_impact_details(
        self,
        *,
        warning: str,
        impacted_pages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        impacted_page_ids: list[str] = []
        impacted_symbols: list[str] = []
        impacted_source_refs: list[dict[str, str]] = []
        repair_targets: list[dict[str, str]] = []
        seen_symbols: set[str] = set()
        for page in impacted_pages:
            page_id = str(page.get("page_id") or "").strip()
            if not page_id:
                continue
            impacted_page_ids.append(page_id)
            page_symbols = [
                str(symbol).strip().upper()
                for symbol in list(page.get("symbols") or [])
                if str(symbol).strip()
            ]
            for symbol in page_symbols:
                if symbol not in seen_symbols:
                    seen_symbols.add(symbol)
                    impacted_symbols.append(symbol)
            impacted_source_refs.extend(
                ref
                for ref in list(page.get("source_refs") or [])[:4]
                if isinstance(ref, dict)
            )
            repair_targets.append(
                {
                    "page_id": page_id,
                    "symbol": page_symbols[0] if page_symbols else "",
                    "recommended_action": (
                        self._recommended_action_for_quality_warning_impact(warning)
                    ),
                }
            )
        return {
            "impacted_page_ids": impacted_page_ids[:12],
            "impacted_symbols": impacted_symbols[:24],
            "impacted_source_refs": impacted_source_refs[:24],
            "repair_targets": repair_targets[:12],
        }

    def _refresh_existing_action_details(
        self,
        *,
        existing: dict[str, Any],
        details: dict[str, Any],
    ) -> dict[str, Any]:
        if existing.get("details") == details:
            return existing
        self._service.initialize()
        with self._service._connect() as conn:
            conn.execute(
                """
                UPDATE wiki_repair_actions
                SET details_json = ?
                WHERE action_id = ?
                """,
                (
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    str(existing.get("action_id") or ""),
                ),
            )
        return {**existing, "details": details}

    def _resolve_existing_action_details(
        self,
        *,
        existing: dict[str, Any],
        details: dict[str, Any],
    ) -> dict[str, Any]:
        resolved_at = datetime.now(timezone.utc).isoformat()
        resolved_details = {
            **details,
            "resolved_at": resolved_at,
        }
        self._service.initialize()
        with self._service._connect() as conn:
            conn.execute(
                """
                UPDATE wiki_repair_actions
                SET status = 'resolved',
                    finished_at = CASE
                        WHEN COALESCE(finished_at, '') = '' THEN ?
                        ELSE finished_at
                    END,
                    details_json = ?
                WHERE action_id = ?
                  AND status IN ('scheduled', 'unresolved')
                """,
                (
                    resolved_at,
                    json.dumps(
                        resolved_details,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    str(existing.get("action_id") or ""),
                ),
            )
        return {
            **existing,
            "status": "resolved",
            "finished_at": resolved_at,
            "details": resolved_details,
        }

    def _quality_warning_effectiveness_details(
        self,
        *,
        metric: dict[str, Any],
        warning: str,
        decision_scope: str,
    ) -> dict[str, Any]:
        return {
            "finding_type": "quality_warning_effectiveness",
            "decision_scope": decision_scope,
            "venue": str(metric.get("venue") or ""),
            "horizon": str(metric.get("horizon") or ""),
            "warning": warning,
            "quality_warnings": [warning],
            "sample_count": int(metric.get("sample_count") or 0),
            "win_rate": float(metric.get("win_rate") or 0.0),
            "expectancy": float(metric.get("expectancy") or 0.0),
            "helpful_score": float(metric.get("helpful_score") or 0.0),
            "confidence": float(metric.get("confidence") or 0.0),
            "repair_action": (
                "repair or downgrade evidence carrying "
                f"{warning} before trusting this warning-bearing memory again"
            ),
            "reasons": self._metric_reasons(metric),
        }

    def _application_repair_queue_pressure_details(
        self,
        *,
        metric: dict[str, Any],
        page_id: str,
        reasons: list[str],
        decision_scope: str,
    ) -> dict[str, Any]:
        return {
            "finding_type": "application_repair_queue_pressure",
            "decision_scope": decision_scope,
            "venue": str(metric.get("venue") or ""),
            "horizon": str(metric.get("horizon") or ""),
            "quality_warnings": ["application_repair_queue_pressure"],
            "repair_queue_open_count": self._repair_queue_open_count_from_reasons(
                reasons
            ),
            "repair_queue_action_types": (
                self._repair_queue_action_types_from_reasons(reasons)
            ),
            "sample_count": int(metric.get("sample_count") or 0),
            "win_rate": float(metric.get("win_rate") or 0.0),
            "expectancy": float(metric.get("expectancy") or 0.0),
            "helpful_score": float(metric.get("helpful_score") or 0.0),
            "confidence": float(metric.get("confidence") or 0.0),
            "repair_action": (
                "resolve open wiki repair queue actions before trusting this page "
                "as a positive decision input again"
            ),
            "reasons": reasons,
            "repair_targets": [
                {
                    "page_id": page_id,
                    "recommended_action": (
                        "resolve_open_repair_queue_before_reusing_page"
                    ),
                }
            ],
        }

    def _usage_guidance_effectiveness_details(
        self,
        *,
        metric: dict[str, Any],
        usage_guidance_id: str,
        decision_scope: str,
    ) -> dict[str, Any]:
        return {
            "finding_type": "usage_guidance_effectiveness",
            "decision_scope": decision_scope,
            "venue": str(metric.get("venue") or ""),
            "horizon": str(metric.get("horizon") or ""),
            "usage_guidance_id": usage_guidance_id,
            "quality_warnings": ["usage_guidance_degraded"],
            "sample_count": int(metric.get("sample_count") or 0),
            "win_rate": float(metric.get("win_rate") or 0.0),
            "expectancy": float(metric.get("expectancy") or 0.0),
            "avg_return_pct": float(metric.get("avg_return_pct") or 0.0),
            "helpful_score": float(metric.get("helpful_score") or 0.0),
            "confidence": float(metric.get("confidence") or 0.0),
            "repair_action": (
                "repair degraded wiki usage guidance before reusing this page "
                "usage pattern"
            ),
            "reasons": self._metric_reasons(metric),
        }

    @staticmethod
    def _quality_warning_effectiveness_finding_id(
        *,
        page_id: str,
        decision_scope: str,
        venue: str,
        horizon: str,
    ) -> str:
        raw = json.dumps(
            {
                "page_id": page_id,
                "decision_scope": decision_scope,
                "venue": venue,
                "horizon": horizon,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"quality_warning_effectiveness:{digest}"

    @staticmethod
    def _application_repair_queue_pressure_finding_id(
        *,
        page_id: str,
        decision_scope: str,
        venue: str,
        horizon: str,
    ) -> str:
        raw = json.dumps(
            {
                "page_id": page_id,
                "decision_scope": decision_scope,
                "venue": venue,
                "horizon": horizon,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"application_repair_queue_pressure:{digest}"

    @staticmethod
    def _usage_guidance_effectiveness_finding_id(
        *,
        page_id: str,
        decision_scope: str,
        venue: str,
        horizon: str,
    ) -> str:
        raw = json.dumps(
            {
                "page_id": page_id,
                "decision_scope": decision_scope,
                "venue": venue,
                "horizon": horizon,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"usage_guidance_effectiveness:{digest}"

    @staticmethod
    def _metric_reasons(metric: dict[str, Any]) -> list[str]:
        raw = metric.get("reasons")
        if raw is None:
            raw = metric.get("reasons_json")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = [raw]
            raw = parsed
        if not isinstance(raw, list):
            return []
        return [str(item)[:180] for item in raw[:5] if str(item).strip()]

    @staticmethod
    def _repair_queue_open_count_from_reasons(reasons: list[str]) -> int:
        for reason in reasons:
            raw = str(reason or "").strip()
            if not raw.startswith("repair_queue_open_count:"):
                continue
            _, _, value = raw.partition(":")
            try:
                return max(int(float(value)), 0)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _repair_queue_action_types_from_reasons(reasons: list[str]) -> list[str]:
        action_types: list[str] = []
        for reason in reasons:
            raw = str(reason or "").strip()
            if not raw.startswith("repair_queue_action:"):
                continue
            _, _, action_type = raw.partition(":")
            clean_action_type = action_type.strip()[:120]
            if clean_action_type and clean_action_type not in action_types:
                action_types.append(clean_action_type)
            if len(action_types) >= 6:
                break
        return action_types

    @staticmethod
    def _evidence_finding_id(
        *,
        page_id: str,
        source_type: str,
        source_id: str,
        quality_status: str,
        warnings: list[str],
    ) -> str:
        raw = json.dumps(
            {
                "page_id": page_id,
                "source_type": source_type,
                "source_id": source_id,
                "quality_status": quality_status,
                "warnings": warnings,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"evidence_quality:{digest}"

    def _existing_open_action(
        self,
        *,
        finding_id: str,
        action_type: str,
    ) -> dict[str, Any]:
        self._service.initialize()
        with self._service._connect() as conn:
            row = conn.execute(
                """
                SELECT action_id, finding_id, page_id, action_type, status,
                       details_json, created_at, finished_at, error_message
                FROM wiki_repair_actions
                WHERE finding_id = ?
                  AND action_type = ?
                  AND status IN ('scheduled', 'unresolved')
                ORDER BY created_at DESC, action_id DESC
                LIMIT 1
                """,
                (finding_id, action_type),
            ).fetchone()
        if row is None:
            return {}
        details: dict[str, Any] = {}
        try:
            parsed = json.loads(str(row["details_json"] or "{}"))
            if isinstance(parsed, dict):
                details = parsed
        except json.JSONDecodeError:
            details = {}
        return {
            "action_id": str(row["action_id"]),
            "finding_id": str(row["finding_id"]),
            "page_id": str(row["page_id"]),
            "action_type": str(row["action_type"]),
            "status": str(row["status"]),
            "details": details,
            "created_at": str(row["created_at"] or ""),
            "finished_at": str(row["finished_at"] or ""),
            "error_message": str(row["error_message"] or ""),
        }

    @staticmethod
    def _action_type_for_evidence_quality(
        *,
        source_type: str,
        warnings: list[str],
    ) -> str:
        warning_set = set(warnings)
        if source_type == "symbol_fundamentals":
            if "price_missing" in warning_set:
                return "refresh_symbol_quote"
            if (
                "financials_missing" in warning_set
                or "financial_rows_rejected_credit_rating" in warning_set
            ):
                return "refresh_symbol_financials"
            return "refresh_symbol_fundamentals"
        if "identity_name_missing" in warning_set:
            return "repair_symbol_identity"
        return "cross_check_evidence_quality"

    @staticmethod
    def _repair_action_for_evidence_quality(
        *,
        status: str,
        warnings: list[str],
    ) -> str:
        warning_set = set(warnings)
        if "identity_name_missing" in warning_set:
            return "repair symbol identity before using this page for block sizing"
        if "price_missing" in warning_set:
            return "refresh price and quote evidence before designing an executable block"
        if "financials_missing" in warning_set:
            return "collect or cross-check financial statements before mid/long sizing"
        if "valuation_metrics_sparse" in warning_set:
            return "refresh valuation metrics and keep valuation as a weak secondary signal"
        if "financial_rows_rejected_credit_rating" in warning_set:
            return "repair fundamentals parser noise and cross-check WiseReport financial rows"
        if any(warning.startswith("valuation_stale") for warning in warning_set):
            return "refresh stale valuation before relying on discount or premium labels"
        if status == "weak":
            return "treat weak evidence as repair-only unless live structure is independently strong"
        return "cross-check partial evidence before increasing block size"

    @staticmethod
    def _recommended_action_for_quality_warning_impact(warning: str) -> str:
        clean_warning = str(warning or "").strip()
        if clean_warning == "financials_missing":
            return "refresh_symbol_financials_and_rewrite_page_evidence"
        if clean_warning == "price_missing":
            return "refresh_symbol_quote_and_rewrite_page_evidence"
        if clean_warning == "identity_name_missing":
            return "repair_symbol_identity_and_rewrite_page_title"
        if clean_warning == "valuation_metrics_sparse":
            return "refresh_valuation_metrics_and_downgrade_size_until_confirmed"
        if clean_warning == "financial_rows_rejected_credit_rating":
            return "repair_financial_parser_noise_and_rewrite_page_evidence"
        if clean_warning.startswith("valuation_stale"):
            return "refresh_stale_valuation_and_rewrite_page_evidence"
        return "cross_check_warning_bearing_evidence_and_rewrite_page_evidence"
