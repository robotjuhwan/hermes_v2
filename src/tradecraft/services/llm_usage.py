from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
_SCHEMA_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[str] = set()
RETIRED_LLM_COMPONENTS: set[str] = {
    "kis_legacy_trader",
}
LLM_COMPONENT_META: dict[str, dict[str, str]] = {
    "kis_block_manager": {
        "label": "KIS 쥬 판단",
        "category": "jue_kis",
        "description": "국장 블록 생성, 수정, 대기진입, 청산 판단",
    },
    "market_judge": {
        "label": "국장 장중 판단",
        "category": "jue_kis",
        "description": "국장 시세, 계좌, 리서치를 묶은 장중 판단",
    },
    "investment_memory": {
        "label": "쥬 메모리/반성",
        "category": "memory",
        "description": "일지, 블록 반성, 정책 메모리 압축",
    },
    "binance_block_manager": {
        "label": "바이낸스 쥬 판단",
        "category": "jue_binance",
        "description": "바이낸스 현물/선물 블록 생성과 관리 판단",
    },
    "crypto_market_research": {
        "label": "크립토 시장 리서치",
        "category": "research",
        "description": "크립토 내러티브, 레짐, 알파 이벤트 정리",
    },
    "crypto_alpha": {
        "label": "크립토 알파 스캐너",
        "category": "research",
        "description": "크립토 후보 압축과 알파 이벤트 평가",
    },
    "research_reports": {
        "label": "네이버 리포트 지식화",
        "category": "research",
        "description": "국장 리포트 수집 후 핵심 사실 추출",
    },
    "research_pipeline": {
        "label": "리서치 파이프라인",
        "category": "research",
        "description": "리서치 요약, 전략 지식, RAG 갱신",
    },
    "research_ask": {
        "label": "AI 질문",
        "category": "assistant",
        "description": "투자 도움 에이전트 질문 응답",
    },
    "strategy_intelligence": {
        "label": "전략 인텔리전스",
        "category": "research",
        "description": "전략 후보와 근거를 LLM으로 재구성",
    },
    "symbol_analysis": {
        "label": "종목 즉석분석",
        "category": "research",
        "description": "개별 종목의 즉석 분석과 블록화 보조 판단",
    },
    "daily_discovery": {
        "label": "국장 랜덤 디스커버리",
        "category": "research",
        "description": "장전 랜덤 종목 스터디와 후보 발굴",
    },
    "portfolio_coach": {
        "label": "포트폴리오 코치",
        "category": "assistant",
        "description": "계좌와 포트폴리오 관점의 보조 판단",
    },
    "kis_legacy_trader": {
        "label": "퇴역 KIS 직접 트레이더 기록",
        "category": "retired",
        "description": "현재 주문 판단에는 쓰지 않는 과거 KIS 직접 트레이더 호출 이력",
    },
    "llm_probe": {
        "label": "LLM 연결 점검",
        "category": "ops",
        "description": "Codex native 연결 확인용 probe",
    },
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def trading_day_from_iso(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST).date().isoformat()


def estimate_tokens(text: Any) -> int:
    raw = str(text or "")
    if not raw:
        return 0
    ascii_words = re.findall(r"[A-Za-z0-9_./:-]+", raw)
    non_ascii_chars = len(re.findall(r"[^\x00-\x7F\s]", raw))
    punctuation_chunks = len(re.findall(r"[{}\[\](),:;\"']", raw))
    by_chars = max(len(raw) // 4, 1)
    by_parts = len(ascii_words) + max(non_ascii_chars // 2, 0) + punctuation_chunks // 3
    return max(by_chars, by_parts, 1)


def safe_json_dumps(value: Any) -> str:
    return json.dumps(value or {}, default=str, ensure_ascii=False, sort_keys=True)


class LLMUsageRepository:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.path.expanduser().resolve())
        with _SCHEMA_LOCK:
            if key not in _INITIALIZED_PATHS:
                self._ensure_schema()
                _INITIALIZED_PATHS.add(key)

    def _connect(self, *, initialize: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        if initialize:
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect(initialize=True) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    trading_day TEXT NOT NULL,
                    component TEXT NOT NULL,
                    operation TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    usage_source TEXT NOT NULL DEFAULT 'missing',
                    input_chars INTEGER NOT NULL DEFAULT 0,
                    output_chars INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_day_component "
                "ON llm_calls(trading_day, component, started_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_model "
                "ON llm_calls(model, trading_day)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_calls_status "
                "ON llm_calls(status, trading_day)"
            )

    def record_call(
        self,
        *,
        component: str,
        model: str,
        mode: str,
        status: str,
        latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        usage_source: str,
        input_chars: int,
        output_chars: int,
        operation: str = "",
        error_message: str = "",
        metadata: dict[str, Any] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any]:
        started = started_at or utc_now_iso()
        finished = finished_at or utc_now_iso()
        trading_day = trading_day_from_iso(started)
        row = {
            "started_at": started,
            "finished_at": finished,
            "trading_day": trading_day,
            "component": str(component or "unknown"),
            "operation": str(operation or ""),
            "model": str(model or ""),
            "mode": str(mode or ""),
            "status": str(status or "unknown"),
            "latency_ms": max(int(latency_ms or 0), 0),
            "prompt_tokens": max(int(prompt_tokens or 0), 0),
            "completion_tokens": max(int(completion_tokens or 0), 0),
            "total_tokens": max(int(total_tokens or 0), 0),
            "usage_source": str(usage_source or "missing"),
            "input_chars": max(int(input_chars or 0), 0),
            "output_chars": max(int(output_chars or 0), 0),
            "error_message": str(error_message or "")[:1200],
            "metadata_json": safe_json_dumps(metadata or {}),
        }
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO llm_calls (
                    started_at, finished_at, trading_day, component, operation,
                    model, mode, status, latency_ms, prompt_tokens,
                    completion_tokens, total_tokens, usage_source, input_chars,
                    output_chars, error_message, metadata_json
                )
                VALUES (
                    :started_at, :finished_at, :trading_day, :component, :operation,
                    :model, :mode, :status, :latency_ms, :prompt_tokens,
                    :completion_tokens, :total_tokens, :usage_source, :input_chars,
                    :output_chars, :error_message, :metadata_json
                )
                """,
                row,
            )
            row["id"] = int(cursor.lastrowid)
        return row

    def daily_summary(self, trading_day: str) -> dict[str, Any]:
        day = str(trading_day or datetime.now(KST).date().isoformat())
        with self._connect() as conn:
            total = conn.execute(
                self._summary_select("", where_sql="trading_day = ?"),
                (day,),
            ).fetchone()
            by_component = conn.execute(
                self._summary_select(
                    "component",
                    group_by="component",
                    order_by="total_tokens DESC, call_count DESC",
                    where_sql="trading_day = ?",
                ),
                (day,),
            ).fetchall()
            by_model = conn.execute(
                self._summary_select(
                    "model",
                    group_by="model",
                    order_by="total_tokens DESC, call_count DESC",
                    where_sql="trading_day = ?",
                ),
                (day,),
            ).fetchall()
            by_status = conn.execute(
                self._summary_select(
                    "status",
                    group_by="status",
                    order_by="call_count DESC, status ASC",
                    where_sql="trading_day = ?",
                ),
                (day,),
            ).fetchall()
            by_usage_source = conn.execute(
                self._summary_select(
                    "usage_source",
                    group_by="usage_source",
                    order_by="call_count DESC, usage_source ASC",
                    where_sql="trading_day = ?",
                ),
                (day,),
            ).fetchall()
        return {
            "status": "ok",
            "period": "today",
            "trading_day": day,
            "total": self._summary_row(total),
            "by_component": [
                self._component_summary_row(row)
                for row in by_component
            ],
            "by_model": [
                self._summary_row(row) | {"model": row["model"]}
                for row in by_model
            ],
            "by_status": [
                self._summary_row(row) | {"status": row["status"]}
                for row in by_status
            ],
            "by_usage_source": [
                self._summary_row(row) | {"usage_source": row["usage_source"]}
                for row in by_usage_source
            ],
        }

    def period_summary(
        self,
        *,
        period: str,
        start_day: str = "",
        end_day: str = "",
    ) -> dict[str, Any]:
        period_clean = str(period or "7d").strip().lower()
        where_sql = "trading_day BETWEEN ? AND ?"
        params: tuple[Any, ...]
        with self._connect() as conn:
            if period_clean in {"all", "total", "history"}:
                bounds = conn.execute(
                    "SELECT MIN(trading_day) AS start_day, MAX(trading_day) AS end_day FROM llm_calls"
                ).fetchone()
                start = str(bounds["start_day"] or "") if bounds else ""
                end = str(bounds["end_day"] or "") if bounds else ""
                where_sql = "1 = 1"
                params = ()
                period_value = "all"
            else:
                period_value = "7d" if period_clean in {"week", "recent_7d"} else period_clean
                end = str(end_day or "").strip() or datetime.now(KST).date().isoformat()
                start = str(start_day or "").strip()
                if not start:
                    end_date = datetime.fromisoformat(end).date()
                    start = (end_date - timedelta(days=6)).isoformat()
                params = (start, end)

            total = conn.execute(
                self._summary_select("", where_sql=where_sql),
                params,
            ).fetchone()
            by_component = conn.execute(
                self._summary_select(
                    "component",
                    group_by="component",
                    order_by="total_tokens DESC, call_count DESC",
                    where_sql=where_sql,
                ),
                params,
            ).fetchall()
            by_model = conn.execute(
                self._summary_select(
                    "model",
                    group_by="model",
                    order_by="total_tokens DESC, call_count DESC",
                    where_sql=where_sql,
                ),
                params,
            ).fetchall()
            by_status = conn.execute(
                self._summary_select(
                    "status",
                    group_by="status",
                    order_by="call_count DESC, status ASC",
                    where_sql=where_sql,
                ),
                params,
            ).fetchall()
            by_usage_source = conn.execute(
                self._summary_select(
                    "usage_source",
                    group_by="usage_source",
                    order_by="call_count DESC, usage_source ASC",
                    where_sql=where_sql,
                ),
                params,
            ).fetchall()
        return {
            "status": "ok",
            "period": period_value,
            "trading_day": end,
            "start_day": start,
            "end_day": end,
            "total": self._summary_row(total),
            "by_component": [
                self._component_summary_row(row)
                for row in by_component
            ],
            "by_model": [
                self._summary_row(row) | {"model": row["model"]}
                for row in by_model
            ],
            "by_status": [
                self._summary_row(row) | {"status": row["status"]}
                for row in by_status
            ],
            "by_usage_source": [
                self._summary_row(row) | {"usage_source": row["usage_source"]}
                for row in by_usage_source
            ],
        }

    @staticmethod
    def _summary_select(
        dimension: str,
        *,
        group_by: str = "",
        order_by: str = "",
        where_sql: str = "trading_day = ?",
    ) -> str:
        dimension_sql = f"{dimension}," if dimension else ""
        group_sql = f"GROUP BY {group_by}" if group_by else ""
        order_sql = f"ORDER BY {order_by}" if order_by else ""
        return f"""
            SELECT
                {dimension_sql}
                COUNT(*) AS call_count,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS error_count,
                SUM(prompt_tokens) AS prompt_tokens,
                SUM(completion_tokens) AS completion_tokens,
                SUM(total_tokens) AS total_tokens,
                SUM(CASE WHEN usage_source = 'exact' THEN 1 ELSE 0 END) AS exact_token_count,
                SUM(CASE WHEN usage_source = 'estimated' THEN 1 ELSE 0 END) AS estimated_token_count,
                SUM(CASE WHEN usage_source = 'missing' THEN 1 ELSE 0 END) AS missing_token_count,
                AVG(latency_ms) AS avg_latency_ms,
                SUM(input_chars) AS input_chars,
                AVG(input_chars) AS avg_input_chars,
                MAX(input_chars) AS max_input_chars,
                SUM(output_chars) AS output_chars,
                AVG(output_chars) AS avg_output_chars,
                MAX(output_chars) AS max_output_chars
            FROM llm_calls
            WHERE {where_sql}
            {group_sql}
            {order_sql}
        """

    @classmethod
    def _component_summary_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        component = str(row["component"] or "unknown")
        return cls._summary_row(row) | {"component": component} | component_meta(component)

    @staticmethod
    def _summary_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {
                "call_count": 0,
                "ok_count": 0,
                "error_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "exact_token_count": 0,
                "estimated_token_count": 0,
                "missing_token_count": 0,
                "avg_latency_ms": 0,
                "input_chars": 0,
                "avg_input_chars": 0,
                "max_input_chars": 0,
                "output_chars": 0,
                "avg_output_chars": 0,
                "max_output_chars": 0,
            }
        return {
            "call_count": int(row["call_count"] or 0),
            "ok_count": int(row["ok_count"] or 0),
            "error_count": int(row["error_count"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "exact_token_count": int(row["exact_token_count"] or 0),
            "estimated_token_count": int(row["estimated_token_count"] or 0),
            "missing_token_count": int(row["missing_token_count"] or 0),
            "avg_latency_ms": int(row["avg_latency_ms"] or 0),
            "input_chars": int(row["input_chars"] or 0),
            "avg_input_chars": int(row["avg_input_chars"] or 0),
            "max_input_chars": int(row["max_input_chars"] or 0),
            "output_chars": int(row["output_chars"] or 0),
            "avg_output_chars": int(row["avg_output_chars"] or 0),
            "max_output_chars": int(row["max_output_chars"] or 0),
        }


def component_meta(component: str) -> dict[str, str]:
    raw = str(component or "unknown")
    fallback = raw.replace("_", " ").strip().title() or "Unknown"
    return {
        "label": LLM_COMPONENT_META.get(raw, {}).get("label", fallback),
        "category": LLM_COMPONENT_META.get(raw, {}).get("category", "other"),
        "description": LLM_COMPONENT_META.get(raw, {}).get(
            "description",
            "등록되지 않은 LLM 사용 컴포넌트",
        ),
    }
