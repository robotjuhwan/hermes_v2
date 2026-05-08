from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from tradecraft.services.llm_bridge import LLMBridge

KST = ZoneInfo("Asia/Seoul")
VALID_RITUAL_SLOTS = {
    "pre_open",
    "midday",
    "post_close",
    "block_reflection",
    "weekly",
}
SLOT_LABELS = {
    "pre_open": "장전 마음가짐",
    "midday": "장중 점검",
    "post_close": "마감 리뷰",
    "block_reflection": "블록 거래 반성",
    "weekly": "주간 압축",
}
SOFT_POLICY_STRENGTHS = {"soft", "observation", "preference", "caution", "watch"}


class TelegramSender(Protocol):
    async def send_message(
        self,
        text: str,
        parse_mode: str | None = None,
        chat_id: str | None = None,
    ) -> dict[str, Any]: ...


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return default
    return parsed


def _clean_text(value: Any, *, limit: int = 4000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(int(limit), 1)]


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "N/A", "nan"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _is_symbol(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{6}", str(value or "").strip()))


def _truncate(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 1)].rstrip() + "..."


@dataclass(slots=True)
class InvestmentMemoryConfig:
    root_path: str = ".runtime/investment_memory"
    db_path: str = ".runtime/investment_memory.db"
    strategy_md_path: str = ".runtime/strategy_krx.md"
    policy_mode: str = "soft_auto"
    persona_tone: str = "friendly_partner"
    ritual_timezone: str = "Asia/Seoul"
    telegram_enabled: bool = True
    context_max_chars: int = 8000


class InvestmentMemoryRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    slot TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    input_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_memory_runs_kind
                    ON memory_runs(kind, slot, run_at DESC);

                CREATE TABLE IF NOT EXISTS daily_journals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    message_md TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    sent_telegram INTEGER NOT NULL DEFAULT 0,
                    telegram_result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(trading_day, slot)
                );

                CREATE TABLE IF NOT EXISTS memory_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    confidence REAL NOT NULL DEFAULT 0,
                    summary_md TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    source_run_id INTEGER,
                    updated_at TEXT NOT NULL,
                    UNIQUE(memory_type, key, status)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_insights_lookup
                    ON memory_insights(memory_type, key, status);

                CREATE TABLE IF NOT EXISTS policy_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_id TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT '',
                    strength TEXT NOT NULL DEFAULT 'soft',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    reason TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    source_run_id INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_sends (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trading_day TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    sent_at TEXT NOT NULL,
                    UNIQUE(trading_day, slot)
                );
                """
            )

    def save_run(
        self,
        *,
        kind: str,
        slot: str,
        status: str,
        mode: str,
        model: str,
        error_message: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_runs (
                    run_at, kind, slot, status, mode, model, error_message,
                    input_json, output_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    kind,
                    slot,
                    status,
                    mode,
                    model,
                    error_message,
                    _json_dumps(input_payload),
                    _json_dumps(output_payload),
                ),
            )
            return int(cursor.lastrowid)

    def upsert_journal(
        self,
        *,
        trading_day: str,
        slot: str,
        title: str,
        message_md: str,
        file_path: str,
        context: dict[str, Any],
        sent_telegram: bool = False,
        telegram_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_journals (
                    trading_day, slot, title, message_md, file_path, context_json,
                    sent_telegram, telegram_result_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trading_day, slot) DO UPDATE SET
                    title = excluded.title,
                    message_md = excluded.message_md,
                    file_path = excluded.file_path,
                    context_json = excluded.context_json,
                    sent_telegram = excluded.sent_telegram,
                    telegram_result_json = excluded.telegram_result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    trading_day,
                    slot,
                    title,
                    message_md,
                    file_path,
                    _json_dumps(context),
                    1 if sent_telegram else 0,
                    _json_dumps(telegram_result or {}),
                    now,
                    now,
                ),
            )
        return self.get_journal(trading_day, slot) or {}

    def get_journal(self, trading_day: str, slot: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM daily_journals
                WHERE trading_day = ? AND slot = ?
                LIMIT 1
                """,
                (trading_day, slot),
            ).fetchone()
        return self._row_to_journal(row) if row else None

    def latest_journals(self, *, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM daily_journals
                ORDER BY trading_day DESC, updated_at DESC
                LIMIT ?
                """,
                (max(int(limit), 1),),
            ).fetchall()
        return [self._row_to_journal(row) for row in rows]

    def journals_for_day(self, trading_day: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM daily_journals
                WHERE trading_day = ?
                ORDER BY CASE slot
                    WHEN 'pre_open' THEN 1
                    WHEN 'midday' THEN 2
                    WHEN 'post_close' THEN 3
                    WHEN 'block_reflection' THEN 4
                    WHEN 'weekly' THEN 5
                    ELSE 9
                END
                """,
                (trading_day,),
            ).fetchall()
        return [self._row_to_journal(row) for row in rows]

    def save_insight(
        self,
        *,
        memory_type: str,
        key: str,
        status: str,
        confidence: float,
        summary_md: str,
        evidence: list[Any] | None = None,
        source_run_id: int | None = None,
    ) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_insights (
                    memory_type, key, status, confidence, summary_md, evidence_json,
                    source_run_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_type, key, status) DO UPDATE SET
                    confidence = excluded.confidence,
                    summary_md = excluded.summary_md,
                    evidence_json = excluded.evidence_json,
                    source_run_id = excluded.source_run_id,
                    updated_at = excluded.updated_at
                """,
                (
                    memory_type,
                    key,
                    status,
                    max(min(float(confidence), 1.0), 0.0),
                    summary_md,
                    _json_dumps(evidence or []),
                    source_run_id,
                    now,
                ),
            )

    def list_insights(
        self,
        *,
        memory_type: str = "",
        key: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_insights WHERE 1 = 1"
        params: list[Any] = []
        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)
        if key:
            query += " AND key = ?"
            params.append(key)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_insight(row) for row in rows]

    def save_policy_change(
        self,
        *,
        policy_id: str,
        action: str,
        strength: str,
        status: str,
        reason: str,
        confidence: float,
        source_run_id: int | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO policy_changes (
                    policy_id, action, strength, status, reason, confidence,
                    source_run_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy_id,
                    action,
                    strength,
                    status,
                    reason,
                    max(min(float(confidence), 1.0), 0.0),
                    source_run_id,
                    utc_now_iso(),
                ),
            )

    def list_policy_changes(
        self,
        *,
        status: str = "",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM policy_changes"
        params: list[Any] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(int(limit), 1))
        with self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_to_policy(row) for row in rows]

    def record_telegram_send(
        self,
        *,
        trading_day: str,
        slot: str,
        status: str,
        result: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_sends (trading_day, slot, status, result_json, sent_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trading_day, slot) DO UPDATE SET
                    status = excluded.status,
                    result_json = excluded.result_json,
                    sent_at = excluded.sent_at
                """,
                (trading_day, slot, status, _json_dumps(result), utc_now_iso()),
            )

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            run_count = int(conn.execute("SELECT COUNT(*) FROM memory_runs").fetchone()[0])
            journal_count = int(conn.execute("SELECT COUNT(*) FROM daily_journals").fetchone()[0])
            insight_count = int(conn.execute("SELECT COUNT(*) FROM memory_insights").fetchone()[0])
            active_policy_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM policy_changes WHERE status = 'active'"
                ).fetchone()[0]
            )
            latest_run = conn.execute(
                "SELECT * FROM memory_runs ORDER BY run_at DESC, id DESC LIMIT 1"
            ).fetchone()
            latest_send = conn.execute(
                "SELECT * FROM telegram_sends ORDER BY sent_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return {
            "status": "ok",
            "db_path": str(self.path),
            "run_count": run_count,
            "journal_count": journal_count,
            "insight_count": insight_count,
            "active_policy_count": active_policy_count,
            "latest_run": self._row_to_run(latest_run) if latest_run else {"status": "missing"},
            "latest_telegram_send": (
                self._row_to_telegram_send(latest_send)
                if latest_send
                else {"status": "missing"}
            ),
        }

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "run_at": row["run_at"],
            "kind": row["kind"],
            "slot": row["slot"],
            "status": row["status"],
            "mode": row["mode"],
            "model": row["model"],
            "error_message": row["error_message"],
            "input": _json_loads(row["input_json"], {}),
            "output": _json_loads(row["output_json"], {}),
        }

    @staticmethod
    def _row_to_journal(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "trading_day": row["trading_day"],
            "slot": row["slot"],
            "slot_label": SLOT_LABELS.get(row["slot"], row["slot"]),
            "title": row["title"],
            "message_md": row["message_md"],
            "file_path": row["file_path"],
            "context": _json_loads(row["context_json"], {}),
            "sent_telegram": bool(row["sent_telegram"]),
            "telegram_result": _json_loads(row["telegram_result_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_insight(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "memory_type": row["memory_type"],
            "key": row["key"],
            "status": row["status"],
            "confidence": float(row["confidence"] or 0),
            "summary_md": row["summary_md"],
            "evidence": _json_loads(row["evidence_json"], []),
            "source_run_id": row["source_run_id"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _row_to_policy(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "policy_id": row["policy_id"],
            "action": row["action"],
            "strength": row["strength"],
            "status": row["status"],
            "reason": row["reason"],
            "confidence": float(row["confidence"] or 0),
            "source_run_id": row["source_run_id"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _row_to_telegram_send(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "trading_day": row["trading_day"],
            "slot": row["slot"],
            "status": row["status"],
            "result": _json_loads(row["result_json"], {}),
            "sent_at": row["sent_at"],
        }


class InvestmentMemoryService:
    def __init__(
        self,
        *,
        config: InvestmentMemoryConfig,
        llm_bridge: LLMBridge | None = None,
        telegram: TelegramSender | None = None,
    ) -> None:
        self.config = config
        self.root = Path(config.root_path)
        self.repository = InvestmentMemoryRepository(config.db_path)
        self.llm_bridge = llm_bridge
        self.telegram = telegram

    def initialize(self, *, force: bool = False) -> dict[str, Any]:
        directories = [
            self.root,
            self.root / "policies",
            self.root / "journals",
            self.root / "symbols",
            self.root / "sectors",
            self.root / "regimes",
            self.root / "blocks",
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        for path, content in self._default_memory_files().items():
            if force or not path.exists():
                path.write_text(content, encoding="utf-8")
                written.append(str(path))

        legacy = self._legacy_strategy_extract()
        legacy_path = self.root / "policies" / "legacy_strategy_extract.md"
        if legacy and (force or not legacy_path.exists()):
            legacy_path.write_text(legacy, encoding="utf-8")
            written.append(str(legacy_path))

        return {
            "status": "ok",
            "root_path": str(self.root),
            "db_path": str(self.repository.path),
            "written": written,
            "written_count": len(written),
        }

    def status(self) -> dict[str, Any]:
        self.initialize()
        repo_status = self.repository.status()
        today = self._trading_day()
        return {
            **repo_status,
            "root_path": str(self.root),
            "model": str(getattr(self.llm_bridge, "resolved_model", "gpt-5.5")),
            "llm_ready": bool(getattr(self.llm_bridge, "ready", False)),
            "policy_mode": self.config.policy_mode,
            "persona_tone": self.config.persona_tone,
            "today": today,
            "today_journals": self.repository.journals_for_day(today),
            "active_policies": self.active_policies(limit=12),
        }

    def today(self, *, now: datetime | None = None) -> dict[str, Any]:
        self.initialize()
        trading_day = self._trading_day(now)
        return {
            "status": "ok",
            "trading_day": trading_day,
            "journals": self.repository.journals_for_day(trading_day),
            "active_policies": self.active_policies(limit=20),
            "latest_journals": self.repository.latest_journals(limit=6),
            "context_pack": self.context_pack(max_chars=3600),
        }

    def active_policies(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.repository.list_policy_changes(status="active", limit=limit)

    def symbol_memory(self, symbol: str) -> dict[str, Any]:
        self.initialize()
        target = str(symbol or "").strip()
        if not _is_symbol(target):
            return {"status": "invalid_symbol", "symbol": target}
        path = self.root / "symbols" / f"{target}.md"
        insights = self.repository.list_insights(
            memory_type="symbol",
            key=target,
            limit=20,
        )
        return {
            "status": "ok",
            "symbol": target,
            "path": str(path),
            "exists": path.exists(),
            "content": path.read_text(encoding="utf-8") if path.exists() else "",
            "insights": insights,
        }

    def block_memory(self, block_id: str) -> dict[str, Any]:
        self.initialize()
        target = _clean_text(block_id, limit=160)
        path = self.root / "blocks" / f"{target}.md"
        insights = self.repository.list_insights(
            memory_type="block",
            key=target,
            limit=20,
        )
        return {
            "status": "ok" if target else "invalid_block_id",
            "block_id": target,
            "path": str(path),
            "exists": path.exists(),
            "content": path.read_text(encoding="utf-8") if path.exists() else "",
            "insights": insights,
        }

    def context_pack(
        self,
        *,
        symbols: list[str] | None = None,
        block_ids: list[str] | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        limit = max(int(max_chars or self.config.context_max_chars), 1000)
        persona = self._read_memory_file("persona.md", limit=1800)
        trading = self._read_memory_file("policies/trading.md", limit=2200)
        active = self.active_policies(limit=12)
        latest = self.repository.latest_journals(limit=4)
        symbol_notes: dict[str, str] = {}
        for symbol in symbols or []:
            if not _is_symbol(symbol):
                continue
            path = self.root / "symbols" / f"{symbol}.md"
            if path.exists():
                symbol_notes[symbol] = _truncate(path.read_text(encoding="utf-8"), 1200)
        block_notes: dict[str, str] = {}
        for block_id in block_ids or []:
            key = _clean_text(block_id, limit=160)
            if not key:
                continue
            path = self.root / "blocks" / f"{key}.md"
            if path.exists():
                block_notes[key] = _truncate(path.read_text(encoding="utf-8"), 1200)

        payload = {
            "status": "ok",
            "persona": persona,
            "trading_policy": trading,
            "active_policies": active,
            "latest_journals": [
                {
                    "trading_day": row.get("trading_day"),
                    "slot": row.get("slot"),
                    "title": row.get("title"),
                    "message_md": _truncate(row.get("message_md"), 900),
                }
                for row in latest
            ],
            "symbol_notes": symbol_notes,
            "block_notes": block_notes,
            "safety_note": (
                "Memory is advisory. Kill switch, cash limits, position limits, "
                "and duplicate-order guards always override memory policies."
            ),
        }
        text = _json_dumps(payload)
        if len(text) <= limit:
            return payload
        payload["latest_journals"] = payload["latest_journals"][:2]
        payload["symbol_notes"] = {
            key: _truncate(value, 500)
            for key, value in symbol_notes.items()
        }
        payload["block_notes"] = {
            key: _truncate(value, 500)
            for key, value in block_notes.items()
        }
        return payload

    async def run_ritual(
        self,
        *,
        slot: str,
        context: dict[str, Any] | None = None,
        send_telegram: bool = False,
        force: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        normalized_slot = self._normalize_slot(slot)
        trading_day = self._trading_day(now)
        existing = self.repository.get_journal(trading_day, normalized_slot)
        if existing and not force:
            telegram_result = {}
            if send_telegram and not existing.get("sent_telegram"):
                telegram_result = await self._send_telegram_once(
                    trading_day=trading_day,
                    slot=normalized_slot,
                    message=existing["message_md"],
                )
                existing = self.repository.upsert_journal(
                    trading_day=trading_day,
                    slot=normalized_slot,
                    title=existing["title"],
                    message_md=existing["message_md"],
                    file_path=existing["file_path"],
                    context=existing.get("context") or {},
                    sent_telegram=bool(telegram_result.get("ok")),
                    telegram_result=telegram_result,
                )
            return {
                "status": "skipped",
                "reason": "journal_already_exists",
                "trading_day": trading_day,
                "slot": normalized_slot,
                "journal": existing,
                "telegram_result": telegram_result,
            }

        prompt = self._build_ritual_prompt(
            slot=normalized_slot,
            trading_day=trading_day,
            context=context or {},
        )
        output, mode, error = await self._complete_json(prompt)
        if not isinstance(output, dict):
            output = self._deterministic_ritual(
                slot=normalized_slot,
                trading_day=trading_day,
                context=context or {},
                error_message=error,
            )
            mode = "deterministic"
        title = _clean_text(
            output.get("title") or SLOT_LABELS.get(normalized_slot, normalized_slot),
            limit=160,
        )
        message = str(output.get("message_md") or output.get("message") or "").strip()
        if not message:
            message = self._deterministic_ritual(
                slot=normalized_slot,
                trading_day=trading_day,
                context=context or {},
                error_message=error,
            )["message_md"]

        run_id = self.repository.save_run(
            kind="ritual",
            slot=normalized_slot,
            status="ok" if not error else "llm_unavailable",
            mode=mode,
            model=str(getattr(self.llm_bridge, "resolved_model", "gpt-5.5")),
            error_message=error,
            input_payload=prompt,
            output_payload=output,
        )
        self._apply_memory_updates(output, source_run_id=run_id)
        file_path = self._write_journal_file(
            trading_day=trading_day,
            slot=normalized_slot,
            title=title,
            message_md=message,
            context=context or {},
            output=output,
        )
        telegram_result: dict[str, Any] = {}
        if send_telegram:
            telegram_result = await self._send_telegram_once(
                trading_day=trading_day,
                slot=normalized_slot,
                message=message,
            )
        journal = self.repository.upsert_journal(
            trading_day=trading_day,
            slot=normalized_slot,
            title=title,
            message_md=message,
            file_path=str(file_path),
            context=context or {},
            sent_telegram=bool(telegram_result.get("ok")),
            telegram_result=telegram_result,
        )
        return {
            "status": "ok" if not error else "llm_unavailable",
            "trading_day": trading_day,
            "slot": normalized_slot,
            "run_id": run_id,
            "mode": mode,
            "error_message": error,
            "journal": journal,
            "telegram_result": telegram_result,
            "memory_updates": output.get("memory_updates") or {},
            "policy_changes": output.get("policy_changes") or [],
        }

    async def run_update(
        self,
        *,
        context: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        payload = dict(context or {})
        payload["update_reason"] = payload.get("update_reason") or "manual_memory_update"
        return await self.run_ritual(
            slot="weekly",
            context=payload,
            send_telegram=False,
            force=force,
        )

    def due_slots(self, *, now: datetime | None = None) -> list[str]:
        local = (now or datetime.now(KST)).astimezone(KST)
        if local.weekday() >= 5:
            current = local.time()
            if local.weekday() == 6 and time(20, 0) <= current <= time(21, 0):
                trading_day = self._trading_day(local)
                if self.repository.get_journal(trading_day, "weekly") is None:
                    return ["weekly"]
            return []
        slots: list[str] = []
        schedule = [
            ("pre_open", time(8, 30), time(8, 55)),
            ("midday", time(11, 40), time(12, 10)),
            ("post_close", time(15, 45), time(16, 30)),
        ]
        trading_day = self._trading_day(local)
        current = local.time()
        for slot, window_start, window_end in schedule:
            if (
                window_start <= current <= window_end
                and self.repository.get_journal(trading_day, slot) is None
            ):
                slots.append(slot)
        return slots

    def _normalize_slot(self, slot: str) -> str:
        normalized = str(slot or "").strip().lower().replace("-", "_")
        if normalized in {"mindset", "morning", "open"}:
            normalized = "pre_open"
        if normalized in {"noon", "lunch", "mid"}:
            normalized = "midday"
        if normalized in {"close", "closing", "review"}:
            normalized = "post_close"
        if normalized in {"reflect", "reflection"}:
            normalized = "block_reflection"
        if normalized not in VALID_RITUAL_SLOTS:
            return "pre_open"
        return normalized

    def _trading_day(self, now: datetime | None = None) -> str:
        return (now or datetime.now(KST)).astimezone(KST).date().isoformat()

    def _default_memory_files(self) -> dict[Path, str]:
        return {
            self.root / "persona.md": "\n".join(
                [
                    "# HERMES Persona",
                    "",
                    "너의 이름은 쥬다.",
                    "쥬는 HERMES 안에서 사용자의 한국장 투자 파트너로 행동한다.",
                    "말투는 친근하지만, 판단은 차분하고 보수적이다.",
                    "과열 매매를 부추기지 않고 근거, 반론, 자료 공백, 오늘의 마음가짐을 함께 말한다.",
                    "블록 트레이딩에서는 각 블록을 독립된 약속으로 보고, 목표가/손절가/논리를 끝까지 추적한다.",
                    "기존 보유분도 새 매수 주문이 아니라 “보유 잔고를 블록 원장에 배정하는 일”로 보고, 쥬가 평단/수량/현재 손익/리스크를 바탕으로 블록화 제안을 만든다.",
                    "모든 메시지는 정보 제공용이며 매매 추천이 아니다.",
                    "",
                ]
            ),
            self.root / "policies" / "init.md": "\n".join(
                [
                    "# Memory Init Policy",
                    "",
                    "- 리포트, RAG, 밸류, 고래/세시반, KIS 계좌, 블록 거래 결과를 원천 근거로 사용한다.",
                    "- 새 기억은 원본을 그대로 복사하지 않고 짧은 판단 단위로 압축한다.",
                    "- 신뢰도가 낮은 내용은 active policy가 아니라 observation 또는 candidate로 둔다.",
                    "",
                ]
            ),
            self.root / "policies" / "update.md": "\n".join(
                [
                    "# Memory Update Policy",
                    "",
                    "- 매일 장전/장중/마감 저널을 남긴다.",
                    "- 닫힌 블록은 진입 가설, 룰 준수, 청산 품질, 놓친 리스크로 반성한다.",
                    "- 반복 확인된 교훈만 운용 정책으로 승격한다.",
                    "",
                ]
            ),
            self.root / "policies" / "trading.md": "\n".join(
                [
                    "# Trading Memory Policy",
                    "",
                    "- 메모리는 LLM 블록 매니저의 판단 보조 자료다.",
                    "- kill switch, 현금 초과 금지, 보유수량 초과 금지, 중복주문 방지는 항상 우선한다.",
                    "- 저평가/고평가 판단은 단독 진입 근거가 아니라 가격 부담 보조 신호다.",
                    "- 손절/목표가 도달은 LLM 없이 룰 실행기가 처리한다.",
                    "",
                ]
            ),
            self.root / "policies" / "telegram.md": "\n".join(
                [
                    "# Telegram Ritual Policy",
                    "",
                    "- 08:30 장전 마음가짐: 오늘 조심할 점과 집중할 블록을 정리한다.",
                    "- 11:40 장중 점검: 오전 판단 유효성과 과매매 위험을 확인한다.",
                    "- 15:45 마감 리뷰: 성과, 실수, 다음 장으로 넘길 기억을 정리한다.",
                    "- 메시지는 짧고 따뜻하게 쓰되, 투자 추천처럼 단정하지 않는다.",
                    "",
                ]
            ),
        }

    def _legacy_strategy_extract(self) -> str:
        path = Path(self.config.strategy_md_path)
        if not path.exists():
            return ""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        picked: list[str] = [
            "# Legacy Strategy Extract",
            "",
            "기존 strategy_krx.md에서 제목/원칙처럼 보이는 줄만 추린 참고 메모리입니다.",
            "원본 전체를 복사하지 않고, 이후 업데이트에서 구조화 메모리로 재평가합니다.",
            "",
        ]
        for line in lines:
            text = line.strip()
            if not text:
                continue
            if text.startswith("#") or text.startswith(("-", "*")):
                picked.append(text[:240])
            if len(picked) >= 80:
                break
        return "\n".join(picked).strip() + "\n" if len(picked) > 4 else ""

    def _read_memory_file(self, relative: str, *, limit: int) -> str:
        path = self.root / relative
        if not path.exists():
            return ""
        try:
            return _truncate(path.read_text(encoding="utf-8"), limit)
        except OSError:
            return ""

    def _build_ritual_prompt(
        self,
        *,
        slot: str,
        trading_day: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "task": "Write and update HERMES investment memory. Return JSON only.",
            "slot": slot,
            "slot_label": SLOT_LABELS.get(slot, slot),
            "trading_day": trading_day,
            "persona": self._read_memory_file("persona.md", limit=2400),
            "policies": {
                "trading": self._read_memory_file("policies/trading.md", limit=2400),
                "update": self._read_memory_file("policies/update.md", limit=1800),
                "telegram": self._read_memory_file("policies/telegram.md", limit=1400),
                "active": self.active_policies(limit=20),
            },
            "context": context,
            "output_schema": {
                "title": "short Korean title",
                "message_md": "Telegram-ready Korean markdown, warm partner tone",
                "memory_updates": {
                    "symbols": [{"symbol": "000000", "summary_md": "string", "confidence": 0.0}],
                    "blocks": [{"block_id": "string", "summary_md": "string", "confidence": 0.0}],
                    "notes": [{"key": "regime|sector|general", "summary_md": "string", "confidence": 0.0}],
                },
                "policy_changes": [
                    {
                        "policy_id": "stable-id",
                        "action": "observe|prefer|avoid|caution|ban",
                        "strength": "soft|observation|preference|caution|hard",
                        "reason": "string",
                        "confidence": 0.0,
                    }
                ],
            },
            "safety": [
                "Do not present this as financial advice.",
                "Do not create order instructions.",
                "Hard restrictions require repeated evidence; keep them candidate unless confidence is very high.",
            ],
        }

    async def _complete_json(self, prompt: dict[str, Any]) -> tuple[Any | None, str, str]:
        if not self.llm_bridge or not getattr(self.llm_bridge, "ready", False):
            return None, "deterministic", "llm_bridge_unavailable"
        payload = {
            "model": getattr(self.llm_bridge, "resolved_model", "gpt-5.5"),
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only JSON matching the requested schema."},
                {"role": "user", "content": _json_dumps(prompt)},
            ],
        }
        result = await self.llm_bridge.complete(payload)
        if not bool(result.get("ok")):
            return None, "deterministic", str(result.get("error") or "llm_failed")
        text = str(result.get("content") or "").strip()
        try:
            return json.loads(text), "llm", ""
        except json.JSONDecodeError as exc:
            return None, "deterministic", f"llm_json_error:{exc}"

    def _deterministic_ritual(
        self,
        *,
        slot: str,
        trading_day: str,
        context: dict[str, Any],
        error_message: str = "",
    ) -> dict[str, Any]:
        account = context.get("account") if isinstance(context.get("account"), dict) else {}
        blocks_payload = context.get("blocks") if isinstance(context.get("blocks"), dict) else {}
        blocks = list(blocks_payload.get("blocks") or []) if isinstance(blocks_payload, dict) else []
        open_blocks = [
            row
            for row in blocks
            if str(row.get("status") or "") in {"open", "entry_pending", "exit_pending"}
        ]
        cash = _safe_float(account.get("cash_krw"))
        position_count = int(_safe_float(account.get("position_count")))
        label = SLOT_LABELS.get(slot, slot)
        lines = [
            f"HERMES {label}",
            "",
            f"오늘 기준일: {trading_day}",
            f"국장1 현금: {cash:,.0f}원 · 보유 {position_count}종목 · 활성 블록 {len(open_blocks)}개",
            "",
            "오늘은 근거가 분명한 블록만 차분히 보고, 자료 공백이 있는 판단은 한 박자 늦춥니다.",
            "목표가와 손절가는 약속이고, 룰 실행기의 신호를 감정으로 덮지 않습니다.",
        ]
        if slot == "midday":
            lines.append("오전 판단이 아직 유효한지, 추격 매수 욕심이 생긴 블록은 없는지 확인합니다.")
        elif slot == "post_close":
            lines.append("마감 후에는 수익보다 과정 품질을 먼저 보고, 내일로 넘길 교훈만 남깁니다.")
        elif slot == "block_reflection":
            lines.append("닫힌 블록은 종목 탓으로 넘기지 않고, 진입 가설과 청산 규칙을 분리해서 복기합니다.")
        if error_message:
            lines.extend(["", f"LLM 메모리 생성은 실패했습니다: {error_message}"])
        lines.extend(["", "정보 제공용이며 매매 추천이 아닙니다."])
        return {
            "title": label,
            "message_md": "\n".join(lines),
            "memory_updates": {},
            "policy_changes": [],
        }

    def _apply_memory_updates(self, output: dict[str, Any], *, source_run_id: int) -> None:
        updates = output.get("memory_updates") if isinstance(output.get("memory_updates"), dict) else {}
        for row in list(updates.get("symbols") or []):
            if not isinstance(row, dict) or not _is_symbol(row.get("symbol")):
                continue
            symbol = str(row.get("symbol")).strip()
            summary = _clean_text(row.get("summary_md") or row.get("summary"), limit=3000)
            if not summary:
                continue
            confidence = _safe_float(row.get("confidence"))
            self.repository.save_insight(
                memory_type="symbol",
                key=symbol,
                status="active",
                confidence=confidence,
                summary_md=summary,
                evidence=list(row.get("evidence") or []),
                source_run_id=source_run_id,
            )
            self._append_memory_note("symbols", f"{symbol}.md", summary, source_run_id=source_run_id)

        for row in list(updates.get("blocks") or []):
            if not isinstance(row, dict):
                continue
            block_id = _clean_text(row.get("block_id"), limit=160)
            summary = _clean_text(row.get("summary_md") or row.get("summary"), limit=3000)
            if not block_id or not summary:
                continue
            confidence = _safe_float(row.get("confidence"))
            self.repository.save_insight(
                memory_type="block",
                key=block_id,
                status="active",
                confidence=confidence,
                summary_md=summary,
                evidence=list(row.get("evidence") or []),
                source_run_id=source_run_id,
            )
            self._append_memory_note("blocks", f"{block_id}.md", summary, source_run_id=source_run_id)

        for row in list(updates.get("notes") or []):
            if not isinstance(row, dict):
                continue
            key = _clean_text(row.get("key") or "general", limit=120)
            summary = _clean_text(row.get("summary_md") or row.get("summary"), limit=3000)
            if not summary:
                continue
            memory_type = "general"
            if key.startswith("sector:"):
                memory_type = "sector"
            elif key.startswith("regime:"):
                memory_type = "regime"
            self.repository.save_insight(
                memory_type=memory_type,
                key=key,
                status="active",
                confidence=_safe_float(row.get("confidence")),
                summary_md=summary,
                evidence=list(row.get("evidence") or []),
                source_run_id=source_run_id,
            )

        for row in list(output.get("policy_changes") or []):
            if not isinstance(row, dict):
                continue
            policy_id = _clean_text(row.get("policy_id") or row.get("id"), limit=160)
            if not policy_id:
                continue
            strength = str(row.get("strength") or "soft").strip().lower()
            action = str(row.get("action") or "observe").strip().lower()
            confidence = _safe_float(row.get("confidence"))
            status = self._policy_status(strength=strength, action=action, confidence=confidence)
            reason = _clean_text(row.get("reason"), limit=1200)
            self.repository.save_policy_change(
                policy_id=policy_id,
                action=action,
                strength=strength,
                status=status,
                reason=reason,
                confidence=confidence,
                source_run_id=source_run_id,
            )
            self.repository.save_insight(
                memory_type="policy",
                key=policy_id,
                status=status,
                confidence=confidence,
                summary_md=reason or action,
                evidence=[],
                source_run_id=source_run_id,
            )

    def _policy_status(self, *, strength: str, action: str, confidence: float) -> str:
        if self.config.policy_mode != "soft_auto":
            return "candidate"
        if strength in SOFT_POLICY_STRENGTHS and action not in {"ban", "block", "forbid"}:
            return "active" if confidence >= 0.35 else "candidate"
        return "active" if confidence >= 0.9 else "candidate"

    def _append_memory_note(
        self,
        directory: str,
        filename: str,
        summary: str,
        *,
        source_run_id: int,
    ) -> None:
        path = self.root / directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"# {filename}\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fp:
            fp.write(
                "\n".join(
                    [
                        f"\n## {utc_now_iso()} · run {source_run_id}",
                        "",
                        summary.strip(),
                        "",
                    ]
                )
            )

    def _write_journal_file(
        self,
        *,
        trading_day: str,
        slot: str,
        title: str,
        message_md: str,
        context: dict[str, Any],
        output: dict[str, Any],
    ) -> Path:
        directory = self.root / "journals" / trading_day
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{slot}.md"
        body = "\n".join(
            [
                "---",
                f"trading_day: {trading_day}",
                f"slot: {slot}",
                f"title: {title}",
                f"created_at: {utc_now_iso()}",
                "---",
                "",
                f"# {title}",
                "",
                message_md.strip(),
                "",
                "## Context Digest",
                "",
                "```json",
                _truncate(_json_dumps(context), 6000),
                "```",
                "",
                "## Memory Output",
                "",
                "```json",
                _truncate(_json_dumps(output), 6000),
                "```",
                "",
            ]
        )
        path.write_text(body, encoding="utf-8")
        return path

    async def _send_telegram_once(
        self,
        *,
        trading_day: str,
        slot: str,
        message: str,
    ) -> dict[str, Any]:
        if not self.config.telegram_enabled:
            result = {"ok": False, "detail": "investment_memory_telegram_disabled"}
            self.repository.record_telegram_send(
                trading_day=trading_day,
                slot=slot,
                status="disabled",
                result=result,
            )
            return result
        if self.telegram is None:
            result = {"ok": False, "detail": "telegram_bridge_missing"}
            self.repository.record_telegram_send(
                trading_day=trading_day,
                slot=slot,
                status="missing",
                result=result,
            )
            return result
        result = await self.telegram.send_message(message)
        self.repository.record_telegram_send(
            trading_day=trading_day,
            slot=slot,
            status="sent" if bool(result.get("ok")) else "failed",
            result=result,
        )
        return result
