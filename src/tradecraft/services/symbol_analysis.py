from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from tradecraft.services.jue_language_policy import jue_language_policy
from tradecraft.services.codex_native import CodexNativeRuntime

SYMBOL_ANALYSIS_CONTEXT_TARGET_CHARS = 45_000
SYMBOL_ANALYSIS_DROPPED_KEYS = {
    "raw",
    "raw_json",
    "payload_json",
    "html",
    "body",
    "content",
    "response",
    "raw_response",
    "prompt",
}


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _string_list(value: Any, limit: int = 8) -> list[str]:
    items = value if isinstance(value, list) else [value] if value else []
    return [
        str(item).strip()[:300]
        for item in items
        if str(item or "").strip()
    ][:limit]


def _parse_json_content(result: dict[str, Any]) -> dict[str, Any]:
    content = str(result.get("content") or "").strip()
    if not content:
        return {}
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _compact_evidence_text(value: Any, *, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[: max(int(limit), 1)]


def _thread_key_component(value: Any, *, default: str = "general") -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or default)[:64]


def _compact_prompt_value(
    value: Any,
    *,
    string_limit: int = 360,
    list_limit: int = 8,
    dict_limit: int = 80,
) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        omitted = 0
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in SYMBOL_ANALYSIS_DROPPED_KEYS:
                omitted += 1
                continue
            if len(compact) >= max(int(dict_limit), 0):
                omitted += 1
                continue
            compact[str(key)] = _compact_prompt_value(
                child,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
            )
        if omitted:
            compact["_omitted_key_count"] = omitted
        return compact
    if isinstance(value, (list, tuple)):
        return [
            _compact_prompt_value(
                item,
                string_limit=string_limit,
                list_limit=list_limit,
                dict_limit=dict_limit,
            )
            for item in list(value)[: max(int(list_limit), 0)]
        ]
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value).strip()
        if len(text) > max(int(string_limit), 1):
            return f"[truncated:{len(text)} chars]"
        return text
    return value


def _prompt_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _analysis_context_compaction_meta(
    *,
    original_chars: int,
    compact_chars: int,
    string_limit: int,
    list_limit: int,
    dict_limit: int,
    mode: str,
) -> dict[str, Any]:
    return {
        "version": "symbol_analysis_prompt_compaction_v1",
        "mode": mode,
        "original_chars": original_chars,
        "compact_chars": compact_chars,
        "target_chars": SYMBOL_ANALYSIS_CONTEXT_TARGET_CHARS,
        "over_budget": compact_chars > SYMBOL_ANALYSIS_CONTEXT_TARGET_CHARS,
        "string_limit": string_limit,
        "list_limit": list_limit,
        "dict_limit": dict_limit,
        "policy": (
            "Keep LLM symbol analysis inputs audit-ready but bounded; raw "
            "payloads are dropped and long evidence is represented by compact markers."
        ),
    }


def _emergency_analysis_context(context: dict[str, Any], *, original_chars: int) -> dict[str, Any]:
    string_limit = 80
    list_limit = 1
    dict_limit = 16
    emergency_keys = (
        "symbol",
        "name",
        "trigger",
        "quote",
        "fundamentals",
        "reports",
        "rag_chunks",
        "blocks",
        "recent_history",
    )
    compact = {
        key: _compact_prompt_value(
            context.get(key),
            string_limit=string_limit,
            list_limit=list_limit,
            dict_limit=dict_limit,
        )
        for key in emergency_keys
        if context.get(key) not in (None, "", [], {})
    }
    compact_chars = _prompt_chars(compact)
    compact["prompt_compaction"] = _analysis_context_compaction_meta(
        original_chars=original_chars,
        compact_chars=compact_chars,
        string_limit=string_limit,
        list_limit=list_limit,
        dict_limit=dict_limit,
        mode="emergency",
    )
    return compact


def _compact_analysis_context(context: dict[str, Any]) -> dict[str, Any]:
    original_chars = _prompt_chars(context)
    for string_limit, list_limit, dict_limit in (
        (360, 6, 80),
        (240, 4, 56),
        (160, 3, 40),
        (100, 2, 28),
        (64, 1, 18),
    ):
        compact = _compact_prompt_value(
            context,
            string_limit=string_limit,
            list_limit=list_limit,
            dict_limit=dict_limit,
        )
        if not isinstance(compact, dict):
            return {}
        compact_chars = _prompt_chars(compact)
        compact["prompt_compaction"] = _analysis_context_compaction_meta(
            original_chars=original_chars,
            compact_chars=compact_chars,
            string_limit=string_limit,
            list_limit=list_limit,
            dict_limit=dict_limit,
            mode="bounded",
        )
        if _prompt_chars(compact) <= SYMBOL_ANALYSIS_CONTEXT_TARGET_CHARS:
            return compact
    return _emergency_analysis_context(context, original_chars=original_chars)



def _analysis_action(stance: Any) -> str:
    normalized = str(stance or "").strip().lower()
    mapping = {
        "block_candidate": "create_or_wait_block",
        "confirm": "confirm_existing_thesis",
        "hold": "hold_or_adopt",
        "risk_check": "risk_check",
        "avoid": "avoid",
        "watch": "watch_add",
    }
    return mapping.get(normalized, normalized or "watch_add")


def _analysis_horizon(analysis: dict[str, Any]) -> str:
    if _compact_evidence_text(analysis.get("long_view")):
        return "long"
    if _compact_evidence_text(analysis.get("mid_view")):
        return "mid"
    return "short"


class FundamentalsProvider(Protocol):
    async def collect_symbols(
        self,
        symbols: list[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]: ...

    def latest(self, symbol: str) -> dict[str, Any] | None: ...


class QuoteProvider(Protocol):
    async def fetch_quote(self, symbol: str) -> dict[str, Any]: ...


class ReportRepository(Protocol):
    def resolve_symbol_names(self, symbols: list[str]) -> dict[str, str]: ...

    def search(
        self,
        query: str,
        symbol: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]: ...


class RAGStore(Protocol):
    def query(
        self,
        text: str,
        *,
        symbol: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]: ...


class BlockProvider(Protocol):
    def blocks(self) -> dict[str, Any]: ...


@dataclass
class SymbolAnalysisService:
    codex_runtime: CodexNativeRuntime
    memory_service: Any
    fundamentals: FundamentalsProvider
    quote_provider: QuoteProvider
    report_repository: ReportRepository
    rag_store: RAGStore | None = None
    block_provider: BlockProvider | None = None
    timeout_ms: int = 45_000
    lease_retry_count: int = 3
    lease_retry_delay_sec: float = 2.0

    async def run(
        self,
        symbol_or_name: str,
        *,
        trigger: str = "user_request",
        force_collect: bool = True,
    ) -> dict[str, Any]:
        symbol = self._resolve_symbol(symbol_or_name)
        if not _is_symbol(symbol):
            return {"status": "invalid_symbol", "symbol": symbol_or_name}

        name = self._symbol_name(symbol)
        collect_result = await self.fundamentals.collect_symbols(
            [symbol],
            force=force_collect,
        )
        fundamentals = self.fundamentals.latest(symbol) or {
            "status": "missing",
            "symbol": symbol,
        }
        quote = await self.quote_provider.fetch_quote(symbol)
        reports = self._reports(symbol)
        rag_chunks = self._rag_chunks(name, symbol)
        blocks = self._symbol_blocks(symbol)
        recent_history = self.memory_service.repository.list_symbol_analyses(
            symbol,
            limit=5,
        )

        prompt = self._build_prompt(
            symbol=symbol,
            name=name,
            trigger=trigger,
            quote=quote,
            fundamentals=fundamentals,
            reports=reports,
            rag_chunks=rag_chunks,
            blocks=blocks,
            recent_history=recent_history,
        )
        analysis, raw_response = await self._call_llm(prompt)
        payload = {
            **analysis,
            "symbol": symbol,
            "name": name,
            "trigger": trigger,
            "source": "instant",
            "model": getattr(self.codex_runtime, "resolved_model", "gpt-5.5"),
            "status": str(analysis.get("status") or "ok"),
            "snapshot": {
                "quote": quote,
                "fundamentals": fundamentals,
                "fundamentals_collect": collect_result,
                "reports": reports,
                "rag_chunks": rag_chunks,
                "blocks": blocks,
                "recent_history": recent_history,
            },
            "prompt": prompt,
            "raw_response": raw_response,
        }
        saved = self.memory_service.repository.save_symbol_analysis(payload)
        self.memory_service.record_symbol_analysis_memory(saved)
        lifecycle_artifact = self._record_lifecycle_artifact(saved)
        return {
            "status": "ok",
            "symbol": symbol,
            "name": name,
            "analysis": saved,
            "lifecycle_artifact": lifecycle_artifact,
        }

    def history(self, symbol: str, *, limit: int = 10) -> dict[str, Any]:
        return self.memory_service.repository.list_symbol_analyses(symbol, limit=limit)

    def special_watch(self) -> dict[str, Any]:
        payload = self.block_provider.blocks() if self.block_provider else {}
        blocks = payload.get("blocks") if isinstance(payload, dict) else []
        items: list[dict[str, Any]] = []
        for row in list(blocks or []):
            if not isinstance(row, dict):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if (
                row.get("created_by") == "existing_position"
                or metadata.get("adopted_from_account")
            ):
                items.append(
                    {
                        "symbol": str(row.get("symbol") or ""),
                        "name": str(row.get("name") or ""),
                        "block_id": str(row.get("block_id") or ""),
                        "status": str(row.get("status") or ""),
                        "reason": "existing_position",
                    }
                )
        return {"status": "ok", "count": len(items), "items": items}

    def _record_lifecycle_artifact(self, analysis: dict[str, Any]) -> dict[str, Any]:
        repository = getattr(self.memory_service, "lifecycle_repository", None)
        upsert = getattr(repository, "upsert_artifact", None)
        if not callable(upsert):
            return {"status": "missing_lifecycle_repository"}
        artifact = self._lifecycle_artifact_from_analysis(analysis)
        try:
            saved = upsert(artifact)
        except Exception as exc:
            return {
                "status": "error",
                "error_message": _compact_evidence_text(exc, limit=240),
            }
        return {
            "status": "ok",
            "artifact_id": saved.get("artifact_id"),
            "workflow_id": saved.get("workflow_id"),
            "evidence_count": len(saved.get("evidence") or []),
        }

    def _lifecycle_artifact_from_analysis(
        self,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        symbol = _compact_evidence_text(analysis.get("symbol"), limit=16)
        name = _compact_evidence_text(analysis.get("name"), limit=80)
        analysis_id = _compact_evidence_text(analysis.get("id"), limit=40) or "latest"
        summary = _compact_evidence_text(analysis.get("summary"), limit=1200)
        return {
            "artifact_id": f"symbol_analysis:{symbol}:{analysis_id}",
            "artifact_type": "symbol_analysis",
            "workflow_id": "instant_symbol_analysis",
            "symbol": symbol,
            "title": f"{name or symbol} 즉석 분석",
            "summary_md": summary,
            "payload": {
                "stance": _compact_evidence_text(analysis.get("stance"), limit=80),
                "confidence": self._safe_float(analysis.get("confidence")),
                "short_view": _compact_evidence_text(
                    analysis.get("short_view"),
                    limit=600,
                ),
                "mid_view": _compact_evidence_text(
                    analysis.get("mid_view"),
                    limit=600,
                ),
                "long_view": _compact_evidence_text(
                    analysis.get("long_view"),
                    limit=600,
                ),
                "reasons": _string_list(analysis.get("reasons"), limit=6),
                "risks": _string_list(analysis.get("risks"), limit=6),
                "data_gaps": _string_list(analysis.get("data_gaps"), limit=6),
                "triggers": _string_list(analysis.get("triggers"), limit=6),
                "target_candidates": self._limited_list(
                    analysis.get("target_candidates"),
                ),
                "stop_candidates": self._limited_list(analysis.get("stop_candidates")),
                "block_implications": [
                    {
                        "action": _analysis_action(analysis.get("stance")),
                        "horizon": _analysis_horizon(analysis),
                        "confidence": self._safe_float(analysis.get("confidence")),
                        "reason": summary,
                    }
                ],
            },
            "evidence": self._lifecycle_evidence_from_snapshot(
                analysis.get("snapshot"),
            ),
            "status": "active",
            "updated_at": _compact_evidence_text(analysis.get("updated_at"), limit=80),
        }

    def _lifecycle_evidence_from_snapshot(self, snapshot: Any) -> list[dict[str, Any]]:
        row = snapshot if isinstance(snapshot, dict) else {}
        evidence: list[dict[str, Any]] = []
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        if quote:
            evidence.append(
                {
                    "source": "quote",
                    "symbol": _compact_evidence_text(quote.get("symbol"), limit=16),
                    "status": _compact_evidence_text(quote.get("status"), limit=40),
                    "price": quote.get("price"),
                    "change_pct": quote.get("change_pct"),
                }
            )
        fundamentals = (
            row.get("fundamentals")
            if isinstance(row.get("fundamentals"), dict)
            else {}
        )
        if fundamentals:
            evidence.append(
                {
                    "source": "fundamentals",
                    "symbol": _compact_evidence_text(
                        fundamentals.get("symbol"),
                        limit=16,
                    ),
                    "status": _compact_evidence_text(
                        fundamentals.get("status"),
                        limit=40,
                    ),
                    "summary": _compact_evidence_text(
                        fundamentals.get("valuation") or fundamentals.get("score"),
                        limit=220,
                    ),
                }
            )
        evidence.extend(
            self._rows_to_evidence(
                row.get("reports"),
                source="naver_report",
                limit=3,
            )
        )
        evidence.extend(
            self._rows_to_evidence(row.get("rag_chunks"), source="rag", limit=3)
        )
        evidence.extend(
            self._rows_to_evidence(row.get("blocks"), source="block_history", limit=3)
        )
        return [item for item in evidence if item.get("source")]

    @staticmethod
    def _rows_to_evidence(
        value: Any,
        *,
        source: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = value if isinstance(value, list) else []
        evidence: list[dict[str, Any]] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            item = {
                "source": source,
                "id": _compact_evidence_text(
                    row.get("id")
                    or row.get("report_id")
                    or row.get("chunk_id")
                    or row.get("block_id"),
                    limit=100,
                ),
                "symbol": _compact_evidence_text(row.get("symbol"), limit=16),
                "title": _compact_evidence_text(
                    row.get("title") or row.get("name") or row.get("status"),
                    limit=180,
                ),
                "status": _compact_evidence_text(row.get("status"), limit=60),
                "published_at": _compact_evidence_text(
                    row.get("published_at") or row.get("created_at"),
                    limit=80,
                ),
                "summary": _compact_evidence_text(
                    row.get("summary") or row.get("thesis") or row.get("risk_note"),
                    limit=220,
                ),
            }
            evidence.append({key: val for key, val in item.items() if val != ""})
        return evidence

    def _resolve_symbol(self, value: str) -> str:
        text = str(value or "").strip()
        if _is_symbol(text):
            return text
        directory_symbol = self._lookup_symbol_directory(text)
        if _is_symbol(directory_symbol):
            return directory_symbol
        try:
            mapping = self.report_repository.resolve_symbol_names([])
        except Exception:
            mapping = {}
        for symbol, name in mapping.items():
            if text and text == str(name):
                return str(symbol)
        try:
            rows = self.report_repository.search(text, limit=10)
        except TypeError:
            try:
                rows = self.report_repository.search(query=text, limit=10)
            except Exception:
                rows = []
        except Exception:
            rows = []
        for row in list(rows or []):
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            names = {
                str(row.get("company_name") or "").strip(),
                str(row.get("name") or "").strip(),
                str(row.get("title") or "").strip(),
            }
            if _is_symbol(symbol) and text in names:
                return symbol
        return text

    def _lookup_symbol_directory(self, name: str) -> str:
        text = str(name or "").strip()
        if not text:
            return ""
        connect = getattr(self.report_repository, "_connect", None)
        if not callable(connect):
            return ""
        try:
            with connect() as conn:
                row = conn.execute(
                    """
                    SELECT symbol
                    FROM symbol_directory
                    WHERE company_name = ?
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (text,),
                ).fetchone()
        except Exception:
            return ""
        if row is None:
            return ""
        try:
            return str(row["symbol"] or "").strip()
        except (KeyError, TypeError, IndexError):
            try:
                return str(row[0] or "").strip()
            except (TypeError, IndexError):
                return ""

    def _symbol_blocks(self, symbol: str) -> list[dict[str, Any]]:
        if not self.block_provider:
            return []
        try:
            payload = self.block_provider.blocks()
        except Exception:
            return []
        rows = payload.get("blocks") if isinstance(payload, dict) else []
        return [
            row
            for row in list(rows or [])
            if isinstance(row, dict) and str(row.get("symbol")) == symbol
        ][:10]

    def _build_prompt(self, **context: Any) -> dict[str, Any]:
        language_policy = jue_language_policy()
        compact_context = _compact_analysis_context(context)
        symbol = _compact_evidence_text(compact_context.get("symbol"), limit=16)
        trigger_key = _thread_key_component(compact_context.get("trigger"))
        return {
            "model": getattr(self.codex_runtime, "resolved_model", "gpt-5.5"),
            "native_thread_key": (
                f"symbol_analysis:{symbol or 'unknown'}:{trigger_key}:{{date}}"
            ),
            "jue_workflow": {"workflow_id": "instant_symbol_analysis"},
            "language_policy": language_policy,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are HERMES investment partner 쥬. Perform real block "
                        "trading analysis for this symbol using price, fundamentals, "
                        "reports, RAG evidence, blocks, and recent memory. Separate "
                        "short, mid, and long views. Think and draft conclusions in "
                        "English, then translate operator-visible fields into Korean. "
                        "JSON-only output."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "instant_symbol_analysis",
                            "language_policy": language_policy,
                            "context": compact_context,
                            "response_schema": {
                                "summary": "쥬의 한 줄 이상 종합 평가",
                                "stance": (
                                    "watch|confirm|hold|risk_check|avoid|"
                                    "block_candidate"
                                ),
                                "confidence": 0.0,
                                "short_view": "단기 평가",
                                "mid_view": "중기 평가",
                                "long_view": "장기 평가",
                                "reasons": ["근거"],
                                "risks": ["반론"],
                                "data_gaps": ["부족한 자료"],
                                "triggers": ["다음 확인 조건"],
                                "target_candidates": [0],
                                "stop_candidates": [0],
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "context": compact_context,
            "response_schema": {
                "summary": "쥬의 한 줄 이상 종합 평가",
                "stance": "watch|confirm|hold|risk_check|avoid|block_candidate",
                "confidence": 0.0,
                "short_view": "단기 평가",
                "mid_view": "중기 평가",
                "long_view": "장기 평가",
                "reasons": ["근거"],
                "risks": ["반론"],
                "data_gaps": ["부족한 자료"],
                "triggers": ["다음 확인 조건"],
                "target_candidates": [0],
                "stop_candidates": [0],
            },
            "telemetry": {"component": "symbol_analysis", "operation": "run"},
        }

    async def _call_llm(
        self,
        prompt: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not getattr(self.codex_runtime, "ready", False):
            reason = "codex_runtime_unavailable"
            return self._analysis_unavailable(reason), {
                "ok": False,
                "error": reason,
            }
        attempts = max(int(self.lease_retry_count), 0) + 1
        lease_error = ""
        raw: dict[str, Any] | None = None
        for attempt in range(attempts):
            try:
                raw = await self.codex_runtime.complete(prompt, timeout_ms=self.timeout_ms)
                raw_error = str(raw.get("error") or "") if isinstance(raw, dict) else ""
                if (
                    isinstance(raw, dict)
                    and raw.get("ok") is False
                    and "thread lease unavailable" in raw_error
                ):
                    lease_error = raw_error
                    if attempt >= attempts - 1:
                        return self._analysis_unavailable(raw_error), raw
                    await asyncio.sleep(max(float(self.lease_retry_delay_sec), 0.0))
                    continue
                break
            except Exception as exc:
                reason = str(exc) or "codex_runtime_error"
                if "thread lease unavailable" not in reason:
                    return self._analysis_unavailable(reason), {
                        "ok": False,
                        "error": reason,
                    }
                lease_error = reason
                if attempt >= attempts - 1:
                    return self._analysis_unavailable(reason), {
                        "ok": False,
                        "error": reason,
                    }
                await asyncio.sleep(max(float(self.lease_retry_delay_sec), 0.0))
        if raw is None:
            reason = lease_error or "codex_runtime_error"
            return self._analysis_unavailable(reason), {
                "ok": False,
                "error": reason,
            }
        parsed = _parse_json_content(raw) if raw.get("ok") else {}
        if not parsed:
            reason = str(raw.get("error") or "empty_llm_response")
            return self._analysis_unavailable(reason), raw
        return {
            "summary": str(parsed.get("summary") or "")[:2000],
            "stance": str(parsed.get("stance") or "watch")[:80],
            "confidence": self._safe_float(parsed.get("confidence")),
            "short_view": str(parsed.get("short_view") or "")[:1200],
            "mid_view": str(parsed.get("mid_view") or "")[:1200],
            "long_view": str(parsed.get("long_view") or "")[:1200],
            "reasons": _string_list(parsed.get("reasons")),
            "risks": _string_list(parsed.get("risks")),
            "data_gaps": _string_list(parsed.get("data_gaps")),
            "triggers": _string_list(parsed.get("triggers")),
            "target_candidates": self._limited_list(parsed.get("target_candidates")),
            "stop_candidates": self._limited_list(parsed.get("stop_candidates")),
        }, raw

    @staticmethod
    def _analysis_unavailable(reason: str) -> dict[str, Any]:
        return {
            "summary": "쥬 정성 분석 생성 실패. 데이터 snapshot만 저장한다.",
            "stance": "stale",
            "confidence": 0.0,
            "short_view": "",
            "mid_view": "",
            "long_view": "",
            "reasons": [],
            "risks": [reason],
            "data_gaps": ["llm_analysis_unavailable"],
            "triggers": [],
            "target_candidates": [],
            "stop_candidates": [],
            "status": "error",
            "error_message": reason,
        }

    def _symbol_name(self, symbol: str) -> str:
        try:
            return self.report_repository.resolve_symbol_names([symbol]).get(
                symbol,
                symbol,
            )
        except Exception:
            return symbol

    def _reports(self, symbol: str) -> list[dict[str, Any]]:
        try:
            return list(self.report_repository.search("", symbol=symbol, limit=5) or [])
        except TypeError:
            try:
                return list(
                    self.report_repository.search(query="", symbol=symbol, limit=5)
                    or []
                )
            except TypeError:
                return []
        except Exception:
            return []

    def _rag_chunks(self, name: str, symbol: str) -> list[dict[str, Any]]:
        if not self.rag_store:
            return []
        try:
            return list(
                self.rag_store.query(f"{name} {symbol}", symbol=symbol, limit=5) or []
            )
        except Exception:
            return []

    @staticmethod
    def _limited_list(value: Any) -> list[Any]:
        return value[:8] if isinstance(value, list) else []

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
