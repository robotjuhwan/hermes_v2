from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tradecraft.services.jue_wiki import JueWikiService


_REFLECTION_COLUMNS = [
    "block_id",
    "scope",
    "target_scope",
    "venue",
    "symbol",
    "lesson",
    "lesson_md",
    "reflection_md",
    "notes_md",
    "summary",
    "created_at",
    "updated_at",
    "pnl_krw",
    "pnl_usdt",
    "metrics_json",
]
_REFLECTION_TEXT_COLUMNS = [
    "lesson",
    "lesson_md",
    "summary",
    "reflection_md",
    "notes_md",
]


@dataclass(frozen=True)
class _ReflectionLoadResult:
    status: str
    rows: list[dict[str, Any]]
    error_message: str = ""
    skipped_count: int = 0


class JueWikiPlaybookCompiler:
    def __init__(
        self,
        service: JueWikiService,
        *,
        investment_memory_db_path: str | Path,
    ) -> None:
        self.service = service
        self.investment_memory_db_path = Path(investment_memory_db_path)

    def compile_all(self) -> dict[str, Any]:
        load_result = self._load_reflections()
        if load_result.status == "error":
            return {
                "status": "error",
                "updated_count": 0,
                "skipped_count": load_result.skipped_count,
                "error_message": load_result.error_message,
            }
        reflections = load_result.rows
        grouped: dict[str, list[dict[str, Any]]] = {"kis": [], "binance": []}
        for reflection in reflections:
            scope = self._reflection_scope(reflection)
            if scope in grouped:
                grouped[scope].append(reflection)

        updated_count = 0
        for scope in ("kis", "binance"):
            scoped = grouped[scope]
            if not scoped:
                continue
            self.service.write_page(
                scope=scope,
                page_type="playbook",
                key="reflection_lessons",
                title=f"{scope.upper()} Reflection Lessons",
                symbols=self._symbols(scoped),
                content_sections=self._content_sections(scope=scope, scoped=scoped),
                source_refs=self._source_refs(scope=scope, scoped=scoped),
                confidence=min(0.25 + len(scoped) * 0.05, 0.8),
                freshness="fresh",
            )
            updated_count += 1

        result: dict[str, Any] = {"status": "ok", "updated_count": updated_count}
        if load_result.skipped_count:
            result["skipped_count"] = load_result.skipped_count
        return result

    def _load_reflections(self) -> _ReflectionLoadResult:
        if not self.investment_memory_db_path.exists():
            return _ReflectionLoadResult(status="ok", rows=[])
        with sqlite3.connect(self.investment_memory_db_path) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'block_reflections'
                """
            ).fetchone()
            if table is None:
                return _ReflectionLoadResult(status="ok", rows=[])

            columns = [
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(block_reflections)")
                if str(row["name"]) in _REFLECTION_COLUMNS
            ]
            if not columns:
                return _ReflectionLoadResult(status="ok", rows=[])
            if not any(column in _REFLECTION_TEXT_COLUMNS for column in columns):
                return _ReflectionLoadResult(
                    status="error",
                    rows=[],
                    error_message=(
                        "block_reflections table lacks a lesson/text column "
                        f"({', '.join(_REFLECTION_TEXT_COLUMNS)})"
                    ),
                )
            order_clause = self._order_clause(columns)
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM block_reflections{order_clause} LIMIT 200"
            ).fetchall()
        reflections: list[dict[str, Any]] = []
        skipped_count = 0
        for row in rows:
            reflection = dict(row)
            if not self._has_lesson_evidence(reflection):
                skipped_count += 1
                continue
            reflections.append(reflection)
        return _ReflectionLoadResult(
            status="ok",
            rows=reflections,
            skipped_count=skipped_count,
        )

    def _order_clause(self, columns: list[str]) -> str:
        order_columns = [
            f"{column} DESC"
            for column in ("updated_at", "created_at", "block_id")
            if column in columns
        ]
        if not order_columns:
            return ""
        return f" ORDER BY {', '.join(order_columns)}"

    def _reflection_scope(self, reflection: dict[str, Any]) -> str:
        for key in ("scope", "target_scope", "venue"):
            scope = self._normalize_scope(reflection.get(key))
            if scope:
                return scope
        symbol = str(reflection.get("symbol") or "").strip().upper()
        if symbol.endswith(("USDT", "BUSD", "USDC")):
            return "binance"
        if symbol.isdigit() and len(symbol) == 6:
            return "kis"
        return ""

    def _normalize_scope(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        if "binance" in text or "crypto" in text or "futures" in text:
            return "binance"
        if "kis" in text or "kr" in text or "korea" in text or "equity" in text:
            return "kis"
        if text in {"spot", "coin"}:
            return "binance"
        return text if text in {"kis", "binance"} else ""

    def _has_lesson_evidence(self, reflection: dict[str, Any]) -> bool:
        return any(
            str(reflection.get(key) or "").strip()
            for key in _REFLECTION_TEXT_COLUMNS
        )

    def _lesson_text(self, reflection: dict[str, Any]) -> str:
        for key in _REFLECTION_TEXT_COLUMNS:
            text = str(reflection.get(key) or "").strip()
            if text:
                return text
        metrics = str(reflection.get("metrics_json") or "").strip()
        if not metrics:
            return ""
        try:
            payload = json.loads(metrics)
        except json.JSONDecodeError:
            return metrics
        if isinstance(payload, dict):
            for key in ("lesson", "summary", "reflection", "note"):
                text = str(payload.get(key) or "").strip()
                if text:
                    return text
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _symbols(self, scoped: list[dict[str, Any]]) -> list[str]:
        symbols: list[str] = []
        seen: set[str] = set()
        for reflection in scoped:
            symbol = str(reflection.get("symbol") or "").strip().upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
        return symbols

    def _source_refs(
        self,
        *,
        scope: str,
        scoped: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for reflection in scoped:
            block_id = str(reflection.get("block_id") or "").strip()
            if not block_id or block_id in seen:
                continue
            seen.add(block_id)
            refs.append(
                {
                    "source_type": "block_reflections",
                    "source_id": block_id,
                    "source_scope": scope,
                }
            )
        return refs

    def _content_sections(
        self,
        *,
        scope: str,
        scoped: list[dict[str, Any]],
    ) -> dict[str, str]:
        lesson_lines = self._lesson_lines(scoped)
        symbols = ", ".join(self._symbols(scoped)) or "scope-wide"
        realized = self._performance_summary(scope=scope, scoped=scoped)
        source_lines = [
            f"- block_reflections:{reflection.get('block_id')}"
            for reflection in scoped
            if str(reflection.get("block_id") or "").strip()
        ]
        return {
            "Current Stance": (
                f"- Treat recent {scope.upper()} block reflections as live "
                "guardrails for the next block design."
            ),
            "Durable Facts": f"- Reflections reviewed: {len(scoped)}.\n- Symbols: {symbols}.",
            "Evidence Links": "\n".join(source_lines) or "- block_reflections.",
            "Trading History": realized,
            "Lessons": "\n".join(lesson_lines) or "- No explicit lesson text.",
            "Contradictions": "- Re-check against fresh market regime before reuse.",
            "Open Questions": "- Which lessons still hold after the next closed block?",
            "Next Context Pack Summary": (
                f"{scope.upper()} reflection playbook distilled from "
                f"{len(scoped)} recent block reflections."
            ),
            "Entry Conditions": "\n".join(lesson_lines) or "- Use only after fresh setup validation.",
            "Exit Conditions": "- Exit rules must honor the original block invalidation plan.",
            "Failure Modes": "- Repeating a reflected mistake without new evidence.",
            "Performance Evidence": realized,
        }

    def _lesson_lines(self, scoped: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for reflection in scoped:
            text = self._lesson_text(reflection)
            if not text:
                continue
            prefix = str(reflection.get("symbol") or "").strip().upper()
            block_id = str(reflection.get("block_id") or "").strip()
            label_parts = [part for part in (prefix, block_id) if part]
            label = " / ".join(label_parts) if label_parts else "reflection"
            lines.append(f"- {label}: {text}")
        return lines

    def _performance_summary(
        self,
        *,
        scope: str,
        scoped: list[dict[str, Any]],
    ) -> str:
        pnl_key = "pnl_krw" if scope == "kis" else "pnl_usdt"
        values = [
            float(value)
            for reflection in scoped
            if (value := reflection.get(pnl_key)) is not None
        ]
        if not values:
            return "- No realized PnL fields available in selected reflections."
        total = sum(values)
        unit = "KRW" if scope == "kis" else "USDT"
        return f"- Reflections with PnL: {len(values)}.\n- Total reflected PnL: {total:.2f} {unit}."
