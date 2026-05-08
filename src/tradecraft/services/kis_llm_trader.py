from __future__ import annotations

import asyncio
import json
import math
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
from tradecraft.services.kis import KISAdapter
from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig
from tradecraft.services.naver_reports import NaverReportRepository
from tradecraft.services.rag_store import RAGStore
from tradecraft.services.runtime_bridge import ResearchSnapshotReader


def _extract_codes(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", text):
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _published_rank(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    digits = re.sub(r"\D", "", text)
    if len(digits) < 8:
        return 0
    try:
        return int(digits[:8])
    except ValueError:
        return 0


def _context_sort_key(row: dict[str, Any]) -> tuple[float, int]:
    distance_raw = row.get("distance")
    distance = _safe_float(distance_raw)
    if distance_raw is None:
        distance = 9999.0
    published = _published_rank(row.get("published_at"))
    return (distance, -published)


@dataclass(slots=True)
class KISLLMTraderConfig:
    research_state_path: str
    trader_state_path: str
    llm_command: str
    persona: str
    llm_bridge_command: str = ""
    llm_bridge_args: str = ""
    llm_bridge_url: str = ""
    llm_bridge_token: str = ""
    llm_bridge_timeout_ms: int = 60000
    llm_model: str = "gpt-5.5"
    execute_orders: bool = False
    max_orders_per_cycle: int = 3
    max_budget_per_order_krw: float = 1_000_000.0
    min_confidence: float = 0.65
    default_order_type: str = "01"
    allow_sell: bool = True
    max_candidate_codes: int = 10
    report_context_top_k: int = 6


class KISLLMTrader:
    def __init__(
        self,
        config: KISLLMTraderConfig,
        kis: KISAdapter,
        report_repo: NaverReportRepository | None = None,
        rag_store: RAGStore | None = None,
    ) -> None:
        self.config = config
        self.kis = kis
        self.report_repo = report_repo
        self.rag_store = rag_store
        self.store = RuntimeStateStore(config.trader_state_path)
        self.research_reader = ResearchSnapshotReader(config.research_state_path)
        self.llm_bridge = LLMBridge(
            LLMBridgeConfig(
                command=config.llm_bridge_command,
                args=config.llm_bridge_args,
                url=config.llm_bridge_url,
                token=config.llm_bridge_token,
                timeout_ms=config.llm_bridge_timeout_ms,
                model=config.llm_model,
            )
        )

    async def run_once(self) -> dict[str, Any]:
        research, research_status = self.research_reader.read_feed()
        if research is None:
            snapshot = {
                "updated_at": utc_now_iso(),
                "status": "skipped",
                "reason": f"research_unavailable:{research_status}",
                "decisions": [],
                "orders": [],
            }
            self.store.write_snapshot(snapshot)
            return snapshot

        target_symbols = self._collect_target_symbols_from_state()
        candidates = self._collect_candidates(research, forced_targets=target_symbols)
        quotes = await self._collect_quotes(candidates)
        account_assets = await self.kis.fetch_balance_assets()
        cash_krw, positions = self._account_state(account_assets)
        report_context = self._collect_report_context(candidates, research)
        decisions = await self._build_decisions(
            research,
            quotes,
            cash_krw,
            positions,
            report_context,
        )
        orders = await self._execute_decisions(decisions, quotes, cash_krw, positions)

        snapshot = {
            "updated_at": utc_now_iso(),
            "status": "ok",
            "execution_mode": "live" if self.config.execute_orders else "dry_run",
            "research_updated_at": str(research.get("updated_at") or ""),
            "cash_krw": cash_krw,
            "target_symbols": target_symbols,
            "candidates": candidates,
            "quotes": quotes,
            "report_context": report_context,
            "decisions": decisions,
            "orders": orders,
        }
        self.store.write_snapshot(snapshot)
        return snapshot

    def _collect_target_symbols_from_state(self) -> list[str]:
        snapshot = self.store.read_snapshot() or {}
        if not isinstance(snapshot, dict):
            return []
        rows = list(
            snapshot.get("target_symbols") or snapshot.get("target_codes") or []
        )
        out: list[str] = []
        seen: set[str] = set()
        for value in rows:
            code = str(value).strip()
            if len(code) == 6 and code.isdigit() and code not in seen:
                seen.add(code)
                out.append(code)
            if len(out) >= self.config.max_candidate_codes:
                break
        return out

    def _collect_candidates(
        self,
        research: dict[str, Any],
        *,
        forced_targets: list[str] | None = None,
    ) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()

        for value in list(forced_targets or []):
            code = str(value).strip()
            if len(code) == 6 and code.isdigit() and code not in seen:
                seen.add(code)
                out.append(code)
            if len(out) >= self.config.max_candidate_codes:
                return out[: self.config.max_candidate_codes]

        for item in list(research.get("items") or []):
            if not isinstance(item, dict):
                continue
            picks = item.get("picks")
            if isinstance(picks, list):
                for value in picks:
                    code = str(value).strip()
                    if len(code) == 6 and code.isdigit() and code not in seen:
                        seen.add(code)
                        out.append(code)

            summary = str(item.get("summary") or "")
            for code in _extract_codes(summary):
                if code in seen:
                    continue
                seen.add(code)
                out.append(code)

            if len(out) >= self.config.max_candidate_codes:
                break

        return out[: self.config.max_candidate_codes]

    async def _collect_quotes(self, candidates: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for code in candidates:
            try:
                out[code] = await self.kis.fetch_domestic_quote(code)
            except Exception:
                continue
        return out

    def _account_state(
        self, assets: list[dict[str, Any]]
    ) -> tuple[float, dict[str, float]]:
        cash_krw = 0.0
        positions: dict[str, float] = {}
        for row in assets:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "")
            symbol = str(row.get("asset") or "").strip()
            qty = _safe_float(row.get("available") or row.get("qty"))
            if kind == "cash" and symbol == "KRW":
                cash_krw += max(
                    _safe_float(row.get("value_krw") or row.get("qty")), 0.0
                )
            elif (
                kind == "position" and len(symbol) == 6 and symbol.isdigit() and qty > 0
            ):
                positions[symbol] = qty
        return cash_krw, positions

    async def _build_decisions(
        self,
        research: dict[str, Any],
        quotes: dict[str, dict[str, Any]],
        cash_krw: float,
        positions: dict[str, float],
        report_context: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self.llm_bridge.ready or self.config.llm_command.strip():
            llm = await self._run_llm(
                research,
                quotes,
                cash_krw,
                positions,
                report_context,
            )
            if llm:
                return llm

        out: list[dict[str, Any]] = []
        for code in quotes:
            if code in positions:
                continue
            out.append(
                {
                    "symbol": code,
                    "side": "buy",
                    "confidence": 0.7,
                    "budget_krw": self.config.max_budget_per_order_krw,
                    "reason": "fallback_candidate_buy",
                }
            )
            if len(out) >= self.config.max_orders_per_cycle:
                break
        return out

    async def _run_llm(
        self,
        research: dict[str, Any],
        quotes: dict[str, dict[str, Any]],
        cash_krw: float,
        positions: dict[str, float],
        report_context: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prompt = {
            "persona": self.config.persona,
            "task": "Select KRX stocks and return JSON decisions only",
            "constraints": {
                "allowed_symbols": list(quotes.keys()),
                "max_orders_per_cycle": self.config.max_orders_per_cycle,
                "min_confidence": self.config.min_confidence,
                "max_budget_per_order_krw": self.config.max_budget_per_order_krw,
                "allow_sell": self.config.allow_sell,
            },
            "account": {
                "cash_krw": cash_krw,
                "positions": positions,
            },
            "research": research,
            "market_intelligence_policy": {
                "sources": list(research.get("market_intelligence_sources") or []),
                "rule": (
                    "Use whale-position and after-close flow sources as 참고 신호 only. "
                    "Do not treat them as live data unless concrete rows are present "
                    "in research or report_context. Combine them with account state, "
                    "report evidence, liquidity, and the user's strategy rules."
                ),
            },
            "report_context": report_context,
            "quotes": quotes,
            "output_schema": {
                "decisions": [
                    {
                        "symbol": "6-digit",
                        "side": "buy|sell|hold",
                        "confidence": "0.0-1.0",
                        "budget_krw": "number (buy only)",
                        "sell_qty": "number (sell only, optional)",
                        "reason": "string",
                    }
                ]
            },
        }

        parsed: Any | None = None
        if self.llm_bridge.ready:
            parsed = await self._run_llm_bridge(prompt)
        elif self.config.llm_command.strip():
            parsed = await self._run_llm_command(prompt)
        if parsed is None:
            return []

        rows: list[Any]
        if isinstance(parsed, dict):
            decisions = parsed.get("decisions")
            rows = decisions if isinstance(decisions, list) else []
        elif isinstance(parsed, list):
            rows = parsed
        else:
            rows = []

        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            side = str(row.get("side") or "").strip().lower()
            confidence = _safe_float(row.get("confidence"))
            if confidence > 1.0:
                confidence = confidence / 100.0
            budget_krw = _safe_float(row.get("budget_krw"))
            sell_qty = _safe_float(row.get("sell_qty"))
            reason = str(row.get("reason") or "").strip()

            if len(symbol) != 6 or not symbol.isdigit() or symbol not in quotes:
                continue
            if side not in {"buy", "sell", "hold"}:
                continue

            out.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "confidence": confidence,
                    "budget_krw": budget_krw,
                    "sell_qty": sell_qty,
                    "reason": reason,
                }
            )
            if len(out) >= max(self.config.max_orders_per_cycle * 2, 10):
                break
        return out

    async def _run_llm_bridge(self, prompt: dict[str, Any]) -> Any | None:
        payload = {
            "model": self.llm_bridge.resolved_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only JSON matching the provided output schema.",
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False),
                },
            ],
        }
        result = await self.llm_bridge.complete(payload)
        if not bool(result.get("ok")):
            return None

        text = str(result.get("content") or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def _run_llm_command(self, prompt: dict[str, Any]) -> Any | None:
        cmd = self.config.llm_command.strip()
        payload = json.dumps(prompt, ensure_ascii=False)
        if "{prompt}" in cmd:
            final = cmd.format(prompt=payload)
        else:
            final = f"{cmd} {shlex.quote(payload)}"

        try:
            argv = shlex.split(final)
        except ValueError:
            return None
        if not argv:
            return None

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None

        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _collect_report_context(
        self,
        candidates: list[str],
        research: dict[str, Any],
    ) -> list[dict[str, Any]]:
        rag = self.rag_store
        query = str(research.get("query") or "").strip()
        limit = max(int(self.config.report_context_top_k), 1)
        expanded = max(limit * 3, limit)
        if rag is not None and rag.available:
            out: list[dict[str, Any]] = []
            seen: set[tuple[int, int, str]] = set()
            for symbol in candidates:
                rows = rag.query(query=query, symbol=symbol, limit=expanded)
                for row in rows:
                    content = str(row.get("content") or "").strip()
                    key = (
                        int(row.get("report_id") or 0),
                        int(row.get("chunk_index") or 0),
                        content[:80],
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(row)
            if not out:
                rows = rag.query(query=query, symbol="", limit=expanded)
                for row in rows:
                    content = str(row.get("content") or "").strip()
                    key = (
                        int(row.get("report_id") or 0),
                        int(row.get("chunk_index") or 0),
                        content[:80],
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(row)
            if out:
                return sorted(out, key=_context_sort_key)[:limit]

        repo = self.report_repo
        if repo is None:
            return []

        out: list[dict[str, Any]] = []
        seen_report_ids: set[int] = set()

        for symbol in candidates:
            rows = repo.search(query=query, symbol=symbol, limit=expanded)
            for row in rows:
                report_id = int(row.get("report_id") or 0)
                if report_id <= 0 or report_id in seen_report_ids:
                    continue
                seen_report_ids.add(report_id)
                out.append(row)

        if not out:
            rows = repo.search(query=query, symbol="", limit=expanded)
            for row in rows:
                report_id = int(row.get("report_id") or 0)
                if report_id <= 0 or report_id in seen_report_ids:
                    continue
                seen_report_ids.add(report_id)
                out.append(row)

        return sorted(out, key=_context_sort_key)[:limit]

    async def _execute_decisions(
        self,
        decisions: list[dict[str, Any]],
        quotes: dict[str, dict[str, Any]],
        cash_krw: float,
        positions: dict[str, float],
    ) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        market_open = self._is_krx_open()
        if self.config.execute_orders and not market_open:
            return [
                {
                    "status": "skipped",
                    "reason": "market_closed",
                    "execution_mode": "live",
                }
            ]

        order_count = 0
        remaining_cash = cash_krw
        max_orders = max(int(self.config.max_orders_per_cycle), 1)

        for row in decisions:
            if order_count >= max_orders:
                break
            symbol = str(row.get("symbol") or "")
            side = str(row.get("side") or "").lower()
            confidence = _safe_float(row.get("confidence"))
            if confidence > 1.0:
                confidence = confidence / 100.0
            if confidence < self.config.min_confidence:
                continue
            if side == "hold":
                continue

            quote = quotes.get(symbol) or {}
            price = max(int(_safe_float(quote.get("price"))), 0)
            if price <= 0:
                continue

            if side == "buy":
                budget = _safe_float(row.get("budget_krw"))
                if budget <= 0:
                    budget = self.config.max_budget_per_order_krw
                budget = min(
                    budget, self.config.max_budget_per_order_krw, remaining_cash
                )
                qty = int(math.floor(budget / price))
                if qty <= 0:
                    continue
                spend = qty * price
                if not self.config.execute_orders:
                    remaining_cash = max(remaining_cash - spend, 0.0)
                    order_count += 1
                    orders.append(
                        {
                            "status": "planned",
                            "execution_mode": "dry_run",
                            "market_open": market_open,
                            "symbol": symbol,
                            "side": "buy",
                            "qty": qty,
                            "budget_krw": spend,
                            "confidence": confidence,
                            "reason": str(row.get("reason") or ""),
                        }
                    )
                    continue
                try:
                    result = await self.kis.submit_domestic_order(
                        symbol=symbol,
                        side="buy",
                        quantity=qty,
                        price=0,
                        order_type=self.config.default_order_type,
                    )
                except Exception as exc:
                    orders.append(
                        {
                            "status": "failed",
                            "symbol": symbol,
                            "side": side,
                            "reason": str(exc),
                        }
                    )
                    continue
                remaining_cash = max(remaining_cash - spend, 0.0)
                order_count += 1
                orders.append(
                    {
                        "status": "sent",
                        "symbol": symbol,
                        "side": "buy",
                        "qty": qty,
                        "budget_krw": spend,
                        "confidence": confidence,
                        "reason": str(row.get("reason") or ""),
                        "order_no": str(result.get("order_no") or ""),
                    }
                )
                continue

            if side == "sell":
                if not self.config.allow_sell:
                    continue
                available = _safe_float(positions.get(symbol))
                if available <= 0:
                    continue
                requested = _safe_float(row.get("sell_qty"))
                qty = (
                    int(math.floor(requested))
                    if requested > 0
                    else int(math.floor(available))
                )
                qty = min(qty, int(math.floor(available)))
                if qty <= 0:
                    continue
                if not self.config.execute_orders:
                    order_count += 1
                    orders.append(
                        {
                            "status": "planned",
                            "execution_mode": "dry_run",
                            "market_open": market_open,
                            "symbol": symbol,
                            "side": "sell",
                            "qty": qty,
                            "confidence": confidence,
                            "reason": str(row.get("reason") or ""),
                        }
                    )
                    continue
                try:
                    result = await self.kis.submit_domestic_order(
                        symbol=symbol,
                        side="sell",
                        quantity=qty,
                        price=0,
                        order_type=self.config.default_order_type,
                    )
                except Exception as exc:
                    orders.append(
                        {
                            "status": "failed",
                            "symbol": symbol,
                            "side": side,
                            "reason": str(exc),
                        }
                    )
                    continue
                order_count += 1
                orders.append(
                    {
                        "status": "sent",
                        "symbol": symbol,
                        "side": "sell",
                        "qty": qty,
                        "confidence": confidence,
                        "reason": str(row.get("reason") or ""),
                        "order_no": str(result.get("order_no") or ""),
                    }
                )

        if not orders:
            orders.append({"status": "skipped", "reason": "no_valid_decision"})
        return orders

    @staticmethod
    def _is_krx_open() -> bool:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        if now.weekday() >= 5:
            return False
        hhmm = now.hour * 100 + now.minute
        return 900 <= hhmm <= 1520
