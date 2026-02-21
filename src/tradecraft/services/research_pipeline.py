from __future__ import annotations

import asyncio
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from tradecraft.runtime.state_store import RuntimeStateStore, utc_now_iso
from tradecraft.services.llm_bridge import LLMBridge, LLMBridgeConfig
from tradecraft.services.naver_reports import NaverReportRepository
from tradecraft.services.rag_store import RAGStore, RAGStoreConfig


def _strip_html(raw: str) -> str:
    text = re.sub(r"<script[\\s\\S]*?</script>", " ", raw, flags=re.IGNORECASE)
    text = re.sub(r"<style[\\s\\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\\s+", " ", text).strip()
    return text


def _extract_title_from_html(raw: str) -> str:
    match = re.search(
        r"<title[^>]*>(.*?)</title>", raw, flags=re.IGNORECASE | re.DOTALL
    )
    if not match:
        return ""
    return _strip_html(match.group(1))


def _extract_krx_codes(text: str) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for match in re.finditer(r"(?<!\\d)(\\d{6})(?!\\d)", text):
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
        if len(codes) >= 10:
            break
    return codes


def _item_fingerprint(item: dict[str, Any]) -> str:
    source = str(item.get("source") or "").strip()
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    summary = str(item.get("summary") or "").strip()
    return "|".join([source, title, url, summary])


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


def _safe_non_negative_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        if not value == value:
            return 0
        return max(int(round(value)), 0)
    text = str(value).strip()
    if not text:
        return 0
    try:
        return max(int(round(float(text))), 0)
    except ValueError:
        return 0


def _normalize_score_100(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        score = int(round(float(value)))
        return max(0, min(score, 100))
    text = str(value).strip()
    if not text:
        return None
    try:
        score = int(round(float(text)))
    except ValueError:
        return None
    return max(0, min(score, 100))


@dataclass(slots=True)
class ResearchPipelineConfig:
    state_path: str
    strategy_md_path: str
    market_scope: str
    codex_command: str
    codex_query: str
    codex_timeout_sec: int
    report_urls: list[str]
    trader_state_path: str = ""
    report_db_path: str = ""
    report_db_top_k: int = 16
    rag_enabled: bool = False
    rag_persist_path: str = ".runtime/rag_chroma"
    rag_collection_name: str = "naver_reports"
    rag_query_top_k: int = 8
    max_items: int = 20
    knowledge_max_chars: int = 28000
    llm_bridge_command: str = ""
    llm_bridge_args: str = ""
    llm_bridge_url: str = ""
    llm_bridge_token: str = ""
    llm_bridge_timeout_ms: int = 60000
    llm_model: str = "gpt-5.3-codex"


class ResearchPipeline:
    def __init__(self, config: ResearchPipelineConfig) -> None:
        self.config = config
        self.store = RuntimeStateStore(config.state_path)
        trader_state = str(config.trader_state_path or "").strip()
        self.trader_store = RuntimeStateStore(trader_state) if trader_state else None
        db_path = str(config.report_db_path or "").strip()
        self.report_repo = NaverReportRepository(db_path) if db_path else None
        self.rag_store = (
            RAGStore(
                RAGStoreConfig(
                    persist_path=config.rag_persist_path,
                    collection_name=config.rag_collection_name,
                )
            )
            if bool(config.rag_enabled)
            else None
        )
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

    async def _bridge_complete_with_retry_once(
        self,
        payload: dict[str, Any],
        *,
        timeout_ms: int,
        retry_on_empty: bool,
    ) -> dict[str, Any]:
        result = await self.llm_bridge.complete(payload, timeout_ms=timeout_ms)
        ok = bool(result.get("ok"))
        content = str(result.get("content") or "").strip()
        should_retry = (not ok) or (retry_on_empty and not content)
        if not should_retry:
            return result

        await asyncio.sleep(0.2)
        return await self.llm_bridge.complete(payload, timeout_ms=timeout_ms)

    async def _request_self_score_via_bridge(
        self,
        *,
        query: str,
        summary: str,
        picks: list[str],
    ) -> tuple[int | None, str]:
        if not self.llm_bridge.ready:
            return None, ""

        prompt = {
            "query": query,
            "summary": summary[:1800],
            "picks": picks[:10],
            "task": "Score your current investment-helper capability for this cycle.",
            "output_schema": {
                "self_score_100": "integer 0-100",
                "self_score_reason": "string under 180 chars",
            },
        }
        payload = {
            "model": self.llm_bridge.resolved_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only one JSON object with self_score_100 and self_score_reason.",
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False),
                },
            ],
        }

        timeout_ms = max(int(self.config.codex_timeout_sec), 1) * 1000
        result = await self.llm_bridge.complete(payload, timeout_ms=timeout_ms)
        if not bool(result.get("ok")):
            return None, ""

        out_text = str(result.get("content") or "").strip()
        if not out_text:
            return None, ""

        try:
            maybe_json = json.loads(out_text)
        except json.JSONDecodeError:
            return None, ""
        if not isinstance(maybe_json, dict):
            return None, ""

        score = _normalize_score_100(maybe_json.get("self_score_100"))
        reason = str(maybe_json.get("self_score_reason") or "").strip()[:260]
        return score, reason

    async def _collect_codex_item_via_bridge(self) -> dict[str, Any] | None:
        if not self.llm_bridge.ready:
            return None

        query = self.config.codex_query.strip() or "KRX overview"
        prompt = {
            "market": self.config.market_scope,
            "query": query,
            "task": "Summarize KRX market context and suggest candidate 6-digit stock codes.",
            "output_schema": {
                "query": "string",
                "summary": "string",
                "picks": ["6-digit string"],
                "self_score_100": "integer 0-100",
                "self_score_reason": "string",
            },
        }
        payload = {
            "model": self.llm_bridge.resolved_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only one JSON object with query, summary, picks, self_score_100, and self_score_reason.",
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False),
                },
            ],
        }

        timeout_ms = max(int(self.config.codex_timeout_sec), 1) * 1000
        result = await self._bridge_complete_with_retry_once(
            payload,
            timeout_ms=timeout_ms,
            retry_on_empty=True,
        )
        if not bool(result.get("ok")):
            return {
                "source": "codex_cli",
                "status": "error",
                "title": "Codex research failed",
                "summary": str(result.get("error") or "bridge request failed")[:1200],
            }

        out_text = str(result.get("content") or "").strip()
        if not out_text:
            return {
                "source": "codex_cli",
                "status": "error",
                "title": "Codex research failed",
                "summary": "no output",
            }

        summary = out_text[:4000]
        parsed_query = query
        picks: list[str] = []
        self_score_100: int | None = None
        self_score_reason = ""
        try:
            maybe_json = json.loads(out_text)
            if isinstance(maybe_json, dict):
                parsed_query = str(maybe_json.get("query") or parsed_query)
                summary = str(
                    maybe_json.get("summary")
                    or maybe_json.get("analysis")
                    or maybe_json.get("result")
                    or summary
                )[:4000]
                picks = [
                    str(code).strip()
                    for code in list(maybe_json.get("picks") or [])
                    if str(code).strip().isdigit() and len(str(code).strip()) == 6
                ]
                self_score_100 = _normalize_score_100(maybe_json.get("self_score_100"))
                self_score_reason = str(
                    maybe_json.get("self_score_reason") or ""
                ).strip()[:260]
        except json.JSONDecodeError:
            pass

        if not picks:
            picks = _extract_krx_codes(summary)

        if self_score_100 is None:
            fallback_score, fallback_reason = await self._request_self_score_via_bridge(
                query=parsed_query,
                summary=summary,
                picks=picks,
            )
            if fallback_score is not None:
                self_score_100 = fallback_score
                if fallback_reason:
                    self_score_reason = fallback_reason

        return {
            "source": "codex_cli",
            "status": "ok",
            "title": f"Codex KRX Research: {parsed_query}",
            "summary": summary,
            "query": parsed_query,
            "picks": picks,
            "self_score_100": self_score_100,
            "self_score_reason": self_score_reason,
        }

    async def _collect_codex_item(self) -> dict[str, Any] | None:
        bridge_item = await self._collect_codex_item_via_bridge()
        if bridge_item is not None:
            if isinstance(bridge_item, dict):
                status = str(bridge_item.get("status") or "").strip().lower()
                summary = str(bridge_item.get("summary") or "").strip().lower()
                if status == "error" and "timed out" in summary:
                    return None
            return bridge_item

        command_template = self.config.codex_command.strip()
        if not command_template:
            return None

        query = self.config.codex_query.strip()
        command = command_template.format(
            query=query,
            market=self.config.market_scope,
        )
        if "{query}" not in command_template and query:
            command = f"{command} {shlex.quote(query)}"

        try:
            argv = shlex.split(command)
        except ValueError:
            return {
                "source": "codex_cli",
                "status": "error",
                "title": "Codex command parse failed",
                "summary": command,
            }
        if not argv:
            return None

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=max(int(self.config.codex_timeout_sec), 1)
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "source": "codex_cli",
                "status": "timeout",
                "title": "Codex research timeout",
                "summary": "Command exceeded timeout",
            }

        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return {
                "source": "codex_cli",
                "status": "error",
                "title": f"Codex research failed ({proc.returncode})",
                "summary": (err_text or out_text or "no output")[:1200],
            }

        summary = out_text[:4000] if out_text else "no output"
        parsed_query = query or "KRX overview"
        self_score_100: int | None = None
        self_score_reason = ""
        try:
            maybe_json = json.loads(out_text)
            if isinstance(maybe_json, dict):
                parsed_query = str(maybe_json.get("query") or parsed_query)
                summary = str(
                    maybe_json.get("summary")
                    or maybe_json.get("analysis")
                    or maybe_json.get("result")
                    or summary
                )[:4000]
                self_score_100 = _normalize_score_100(maybe_json.get("self_score_100"))
                self_score_reason = str(
                    maybe_json.get("self_score_reason") or ""
                ).strip()[:260]
        except json.JSONDecodeError:
            pass

        picks = _extract_krx_codes(summary)
        if self_score_100 is None:
            fallback_score, fallback_reason = await self._request_self_score_via_bridge(
                query=parsed_query,
                summary=summary,
                picks=picks,
            )
            if fallback_score is not None:
                self_score_100 = fallback_score
                if fallback_reason:
                    self_score_reason = fallback_reason
        return {
            "source": "codex_cli",
            "status": "ok",
            "title": f"Codex KRX Research: {parsed_query}",
            "summary": summary,
            "query": parsed_query,
            "picks": picks,
            "self_score_100": self_score_100,
            "self_score_reason": self_score_reason,
        }

    def _resolve_agent_self_score(self, items: list[dict[str, Any]]) -> tuple[int, str]:
        for item in items:
            if not isinstance(item, dict):
                continue
            score = _normalize_score_100(item.get("self_score_100"))
            if score is None:
                continue
            note = str(item.get("self_score_reason") or "").strip()[:260]
            if note:
                return score, note
            return score, "자가평가 점수"

        typed_items = [item for item in items if isinstance(item, dict)]
        if typed_items:
            ok_count = sum(
                1
                for item in typed_items
                if str(item.get("status") or "ok").strip().lower() == "ok"
            )
            error_count = max(len(typed_items) - ok_count, 0)
            picks_count = sum(
                1
                for item in typed_items
                if isinstance(item.get("picks"), list)
                and any(str(code or "").strip() for code in item.get("picks") or [])
            )
            score = (
                40
                + min(ok_count, 20) * 2
                + min(picks_count, 10) * 2
                - min(error_count, 10) * 4
            )
            score = max(0, min(score, 100))
            timeout_seen = any(
                "timed out" in str(item.get("summary") or "").lower()
                for item in typed_items
            )
            if timeout_seen:
                return score, "LLM 응답 지연이 있어 규칙기반 보정 점수 적용"
            return score, "LLM 점수 누락으로 규칙기반 보정 점수 적용"

        previous = self.store.read_snapshot() or {}
        prev_score = _normalize_score_100(previous.get("agent_self_score_100"))
        if prev_score is not None:
            prev_note = str(previous.get("agent_self_score_note") or "").strip()[:260]
            if prev_note:
                return prev_score, prev_note
            return prev_score, "이전 자가평가 점수 유지"
        return 0, "자가평가 정보 없음"

    async def _collect_report_item(self, url: str) -> dict[str, Any] | None:
        clean = url.strip()
        if not clean:
            return None
        timeout = httpx.Timeout(20.0, connect=10.0)
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                response = await client.get(clean)
        except Exception:
            return {
                "source": "report_crawl",
                "status": "error",
                "url": clean,
                "title": "Report fetch failed",
                "summary": "request failed",
            }

        if response.status_code >= 400:
            return {
                "source": "report_crawl",
                "status": "error",
                "url": clean,
                "title": f"Report fetch failed ({response.status_code})",
                "summary": "non-success response",
            }

        body = response.text
        title = _extract_title_from_html(body) or "Securities report page"
        summary = _strip_html(body)[:1200]
        picks = _extract_krx_codes(summary)
        return {
            "source": "report_crawl",
            "status": "ok",
            "url": str(response.url),
            "title": title,
            "summary": summary,
            "picks": picks,
        }

    def _dedupe_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            fp = _item_fingerprint(item)
            if not fp or fp in seen:
                continue
            seen.add(fp)
            payload = dict(item)
            payload.setdefault("fingerprint", fp)
            out.append(payload)
            if len(out) >= max(int(self.config.max_items), 1):
                break
        return out

    def _collect_report_db_items(self) -> list[dict[str, Any]]:
        repo = self.report_repo
        if repo is None:
            return []

        top_k = max(min(int(self.config.report_db_top_k), 50), 1)
        query = str(self.config.codex_query or "").strip()
        try:
            rows = repo.search(query=query, symbol="", limit=top_k)
            if not rows and query:
                rows = repo.search(query="", symbol="", limit=top_k)
        except Exception:
            return []

        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "Naver report").strip()
            snippet = re.sub(r"\s+", " ", str(row.get("snippet") or "").strip())
            if not snippet:
                continue
            url = str(row.get("detail_url") or row.get("pdf_url") or "").strip()
            picks = _extract_krx_codes(f"{title} {snippet}")
            out.append(
                {
                    "source": "naver_report_db",
                    "status": "ok",
                    "title": title,
                    "url": url,
                    "summary": snippet[:400],
                    "picks": picks,
                }
            )
        return out

    def _collect_rag_items(self) -> list[dict[str, Any]]:
        rag = self.rag_store
        if rag is None or not rag.available:
            return []

        top_k = max(min(int(self.config.rag_query_top_k), 50), 1)
        query = str(self.config.codex_query or "").strip()
        rows = rag.query(query=query, symbol="", limit=top_k)
        if not rows and query:
            rows = rag.query(query="KRX", symbol="", limit=top_k)

        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "Naver report").strip()
            snippet = re.sub(r"\s+", " ", str(row.get("content") or "").strip())
            if not snippet:
                continue
            url = str(row.get("detail_url") or row.get("pdf_url") or "").strip()
            picks = _extract_krx_codes(f"{title} {snippet}")
            out.append(
                {
                    "source": "naver_report_rag",
                    "status": "ok",
                    "title": title,
                    "url": url,
                    "summary": snippet[:400],
                    "picks": picks,
                }
            )
        return out

    def _read_existing_lessons(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return []

        out: list[str] = []
        in_section = False
        for raw in text.splitlines():
            line = raw.strip()
            if line == "## Persistent Lessons":
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section and line.startswith("- "):
                bullet = line[2:].strip()
                if bullet:
                    out.append(bullet)
        return out

    def _new_lessons(self, items: list[dict[str, Any]]) -> list[str]:
        out: list[str] = []
        for item in items[:40]:
            status = str(item.get("status") or "").strip().lower()
            if status and status != "ok":
                continue
            title = str(item.get("title") or "").strip()
            summary = re.sub(r"\s+", " ", str(item.get("summary") or "").strip())
            if not title and not summary:
                continue
            lesson = f"{title[:90]} | {summary[:220]}".strip(" |")
            if lesson:
                out.append(lesson)
        return out

    def _trader_feedback_lessons(self) -> list[str]:
        store = self.trader_store
        if store is None:
            return []

        snapshot = store.read_snapshot()
        if snapshot is None:
            return []

        out: list[str] = []
        decisions = list(snapshot.get("decisions") or [])
        orders = list(snapshot.get("orders") or [])

        for row in decisions[:8]:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip()
            side = str(row.get("side") or "").strip().lower()
            confidence = _safe_float(row.get("confidence"))
            reason = re.sub(r"\s+", " ", str(row.get("reason") or "").strip())
            if not symbol or side not in {"buy", "sell", "hold"}:
                continue
            out.append(
                f"Decision {symbol} {side} conf={confidence:.2f} reason={reason[:160]}"
            )

        for row in orders[:8]:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "").strip().lower()
            symbol = str(row.get("symbol") or "").strip()
            side = str(row.get("side") or "").strip().lower()
            reason = re.sub(r"\s+", " ", str(row.get("reason") or "").strip())
            if status != "sent" or not symbol or side not in {"buy", "sell"}:
                continue
            qty = int(_safe_float(row.get("qty")))
            out.append(f"Executed {symbol} {side} qty={qty} reason={reason[:160]}")

        return out

    def _write_strategy_markdown(self, snapshot: dict[str, Any]) -> None:
        items = list(snapshot.get("items") or [])
        all_picks: list[str] = []
        for item in items:
            picks = item.get("picks")
            if not isinstance(picks, list):
                continue
            for code in picks:
                text = str(code).strip()
                if text and text not in all_picks:
                    all_picks.append(text)

        path = Path(self.config.strategy_md_path)
        existing_lessons = self._read_existing_lessons(path)
        new_lessons = self._new_lessons(items)
        trader_lessons = self._trader_feedback_lessons()

        merged_lessons: list[str] = []
        seen_lessons: set[str] = set()
        for lesson in [*trader_lessons, *new_lessons, *existing_lessons]:
            key = lesson.casefold().strip()
            if not key or key in seen_lessons:
                continue
            seen_lessons.add(key)
            merged_lessons.append(lesson)

        max_lessons = max(min(int(self.config.knowledge_max_chars) // 260, 120), 24)
        merged_lessons = merged_lessons[:max_lessons]

        lines: list[str] = []
        lines.append("# KRX Knowledge Memory")
        lines.append("")
        lines.append(f"- Updated UTC: {snapshot.get('updated_at')}")
        lines.append(f"- Market: {snapshot.get('market')}")
        lines.append(f"- Focus Query: {snapshot.get('query')}")
        if all_picks:
            lines.append(f"- Watchlist Codes: {', '.join(all_picks[:12])}")
        lines.append("")
        lines.append("## Core Insights")
        lines.append("")

        for item in items[:12]:
            title = str(item.get("title") or "Untitled").strip()
            source = str(item.get("source") or "unknown").strip()
            status = str(item.get("status") or "ok").strip()
            url = str(item.get("url") or "").strip()
            summary = re.sub(r"\s+", " ", str(item.get("summary") or "").strip())
            lines.append(f"### {title[:120]}")
            lines.append(f"- Source: {source[:40]}")
            lines.append(f"- Status: {status[:20]}")
            if url:
                lines.append(f"- URL: {url}")
            if summary:
                lines.append(f"- Summary: {summary[:260]}")
            lines.append("")

        lines.append("## Persistent Lessons")
        lines.append("")
        for lesson in merged_lessons:
            lines.append(f"- {lesson}")
        lines.append("")

        lines.append("## Advisor Playbook")
        lines.append("")
        lines.append("- Prefer high-liquidity KRX names with clear catalyst alignment.")
        lines.append(
            "- Keep cash buffer and avoid concentration when confidence is weak."
        )
        lines.append("- Re-check risk events before open, noon, and close decisions.")
        lines.append("")

        text = "\n".join(lines).strip() + "\n"
        limit = max(int(self.config.knowledge_max_chars), 300)
        if len(text) > limit:
            note = "\n\n## Compression Note\n- Auto-trimmed to fit context budget.\n"
            keep = max(limit - len(note), 100)
            text = text[:keep].rstrip() + note

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    async def run_once(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []

        previous_snapshot = self.store.read_snapshot() or {}
        previous_total_learning = _safe_non_negative_int(
            previous_snapshot.get("learning_total_count")
        )

        codex_item = await self._collect_codex_item()
        if codex_item:
            items.append(codex_item)

        rag_items = self._collect_rag_items()
        if rag_items:
            items.extend(rag_items)
        else:
            items.extend(self._collect_report_db_items())

        for url in self.config.report_urls:
            item = await self._collect_report_item(url)
            if item:
                items.append(item)

        deduped = self._dedupe_items(items)
        agent_self_score_100, agent_self_score_note = self._resolve_agent_self_score(
            deduped
        )
        snapshot = {
            "updated_at": utc_now_iso(),
            "source": "research_runner",
            "query": self.config.codex_query or "KRX research",
            "market": self.config.market_scope,
            "status": "ok",
            "count": len(deduped),
            "learning_total_count": previous_total_learning + 1,
            "items": deduped,
            "agent_self_score_100": agent_self_score_100,
            "agent_self_score_note": agent_self_score_note,
        }
        self.store.write_snapshot(snapshot)
        self._write_strategy_markdown(snapshot)
        return snapshot
