from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tradecraft.services.jue_wiki import JueWikiService


class JueWikiPerformanceProjector:
    def __init__(
        self,
        service: JueWikiService,
        *,
        performance_db_path: str | Path,
    ) -> None:
        self.service = service
        self.performance_db_path = Path(performance_db_path)

    def project_all(self) -> dict[str, Any]:
        if not self.performance_db_path.exists():
            return {"status": "ok", "updated_count": 0}

        try:
            outcomes = self._load_outcomes()
        except (sqlite3.Error, ValueError) as exc:
            return {
                "status": "error",
                "updated_count": 0,
                "error_message": str(exc),
            }

        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for outcome in outcomes:
            key = (
                str(outcome["metric_source"]),
                str(outcome["scope"]),
                str(outcome["playbook_id"]),
            )
            groups.setdefault(key, []).append(outcome)

        updated_count = 0
        for (_, scope, playbook_id), scoped_outcomes in groups.items():
            metric = self._metric_for(scope, playbook_id, scoped_outcomes)
            self.service.upsert_playbook_metric(metric)
            updated_count += 1
        self._write_live_performance_pages(outcomes)
        return {"status": "ok", "updated_count": updated_count}

    def _load_outcomes(self) -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        with sqlite3.connect(self.performance_db_path) as conn:
            conn.row_factory = sqlite3.Row
            tables = self._tables(conn)
            if "playbook_outcomes" in tables:
                outcomes.extend(self._load_playbook_outcomes(conn))
            if "live_block_performance" in tables:
                outcomes.extend(self._load_live_block_performance(conn))
        return outcomes

    def _load_playbook_outcomes(
        self,
        conn: sqlite3.Connection,
    ) -> list[dict[str, Any]]:
        columns = self._columns(conn, "playbook_outcomes")
        if not {"scope", "playbook_id", "pnl"} <= columns:
            raise ValueError("playbook_outcomes is missing required columns")
        select_columns = ["scope", "playbook_id", "pnl"]
        if "drawdown_pct" in columns:
            select_columns.append("drawdown_pct")
        order_clause = " ORDER BY created_at ASC" if "created_at" in columns else ""
        rows = conn.execute(
            f"SELECT {', '.join(select_columns)} FROM playbook_outcomes{order_clause}"
        ).fetchall()
        outcomes: list[dict[str, Any]] = []
        for row in rows:
            scope = self._clean_key(row["scope"]).lower()
            raw_playbook_id = str(row["playbook_id"] or "").strip()
            base_playbook_id = self._clean_key(raw_playbook_id)
            if not scope or not base_playbook_id:
                continue
            playbook_id = self._outcome_playbook_id(base_playbook_id)
            outcomes.append(
                {
                    "scope": scope,
                    "raw_scope": row["scope"],
                    "metric_source": "playbook_outcomes",
                    "playbook_id": playbook_id,
                    "base_playbook_id": base_playbook_id,
                    "raw_playbook_id": raw_playbook_id,
                    "venue": "",
                    "raw_venue": "",
                    "pnl": self._float(row["pnl"]),
                    "return_pct": None,
                    "drawdown_pct": abs(self._float(row["drawdown_pct"]))
                    if "drawdown_pct" in row.keys()
                    else None,
                }
            )
        return outcomes

    def _load_live_block_performance(
        self,
        conn: sqlite3.Connection,
    ) -> list[dict[str, Any]]:
        columns = self._columns(conn, "live_block_performance")
        if "net_pnl" not in columns or not ({"scope", "venue"} & columns):
            raise ValueError("live_block_performance is missing required columns")
        select_columns = [
            column
            for column in (
                "scope",
                "venue",
                "block_id",
                "symbol",
                "net_pnl",
                "gross_pnl",
                "cost_total",
                "pnl_pct",
                "include_in_jue_alpha",
                "include_in_risk_management",
                "source_json",
                "computed_at",
            )
            if column in columns
        ]
        order_clause = " ORDER BY computed_at ASC" if "computed_at" in columns else ""
        rows = conn.execute(
            f"SELECT {', '.join(select_columns)} FROM live_block_performance{order_clause}"
        ).fetchall()
        outcomes: list[dict[str, Any]] = []
        for row in rows:
            include_alpha = "include_in_jue_alpha" not in row.keys() or self._truthy(
                row["include_in_jue_alpha"]
            )
            include_risk_management = (
                "include_in_risk_management" in row.keys()
                and self._truthy(row["include_in_risk_management"])
            )
            if not include_alpha and not include_risk_management:
                continue
            venue = str(row["venue"] or "") if "venue" in row.keys() else ""
            scope = (
                self._clean_key(row["scope"]).lower()
                if "scope" in row.keys() and str(row["scope"] or "").strip()
                else self._scope_from_venue(venue)
            )
            payload = self._json_object(
                row["source_json"] if "source_json" in row.keys() else ""
            )
            base_playbook_id = self._playbook_id(payload)
            metric_source = "live_block_performance"
            if not include_alpha:
                metric_source = "live_block_risk_management"
                base_playbook_id = f"risk_management.{base_playbook_id}"
            venue_slug = self._clean_key(venue).lower()
            playbook_id = (
                f"live.{venue_slug}.{base_playbook_id}"
                if venue_slug
                else f"live.{base_playbook_id}"
            )
            outcomes.append(
                {
                    "scope": scope,
                    "raw_scope": row["scope"] if "scope" in row.keys() else "",
                    "metric_source": metric_source,
                    "playbook_id": playbook_id,
                    "base_playbook_id": base_playbook_id,
                    "venue": venue_slug,
                    "raw_venue": venue,
                    "block_id": str(row["block_id"] or "")
                    if "block_id" in row.keys()
                    else "",
                    "symbol": str(row["symbol"] or "")
                    if "symbol" in row.keys()
                    else "",
                    "pnl": self._float(row["net_pnl"]),
                    "gross_pnl": self._float(row["gross_pnl"])
                    if "gross_pnl" in row.keys()
                    else self._float(row["net_pnl"]),
                    "cost_total": self._float(row["cost_total"])
                    if "cost_total" in row.keys()
                    else 0.0,
                    "return_pct": self._float(row["pnl_pct"])
                    if "pnl_pct" in row.keys() and row["pnl_pct"] is not None
                    else None,
                    "drawdown_pct": None,
                    "computed_at": str(row["computed_at"] or "")
                    if "computed_at" in row.keys()
                    else "",
                    "include_in_jue_alpha": include_alpha,
                    "include_in_risk_management": include_risk_management,
                }
            )
        return outcomes

    def _write_live_performance_pages(self, outcomes: list[dict[str, Any]]) -> int:
        live_outcomes = [
            outcome
            for outcome in outcomes
            if outcome.get("metric_source")
            in {"live_block_performance", "live_block_risk_management"}
        ]
        by_scope: dict[str, list[dict[str, Any]]] = {}
        for outcome in live_outcomes:
            scope = self._clean_key(outcome.get("scope")).lower()
            if scope:
                by_scope.setdefault(scope, []).append(outcome)

        updated_count = 0
        for scope, rows in sorted(by_scope.items()):
            if not rows:
                continue
            rows = sorted(
                rows,
                key=lambda row: str(row.get("computed_at") or ""),
                reverse=True,
            )
            summary = self._live_performance_summary(rows)
            symbols = sorted(
                {
                    str(row.get("symbol") or "").strip().upper()
                    for row in rows
                    if str(row.get("symbol") or "").strip()
                }
            )
            source_refs = [
                {
                    "source_type": str(
                        row.get("metric_source") or "live_block_performance"
                    ),
                    "source_id": str(row.get("block_id") or ""),
                    "source_scope": scope,
                    "observed_at": str(row.get("computed_at") or ""),
                }
                for row in rows[:40]
                if str(row.get("block_id") or "").strip()
            ]
            latest_lines = [
                "- {block_id} / {symbol}: venue={venue}, playbook={playbook}, "
                "net_pnl={net_pnl:.4f}, pnl_pct={pnl_pct}, cost={cost:.4f}, "
                "at={computed_at}".format(
                    block_id=str(row.get("block_id") or "-"),
                    symbol=str(row.get("symbol") or "-"),
                    venue=str(row.get("raw_venue") or row.get("venue") or "-"),
                    playbook=str(row.get("playbook_id") or "-"),
                    net_pnl=self._float(row.get("pnl")),
                    pnl_pct=self._format_optional_pct(row.get("return_pct")),
                    cost=self._float(row.get("cost_total")),
                    computed_at=str(row.get("computed_at") or "-"),
                )
                for row in rows[:16]
            ]
            durable_facts = "\n".join(
                [
                    f"- scope={scope}",
                    f"- sample_count={summary['sample_count']}",
                    f"- win_rate={summary['win_rate']:.4f}",
                    f"- total_net_pnl={summary['total_net_pnl']:.4f}",
                    f"- total_gross_pnl={summary['total_gross_pnl']:.4f}",
                    f"- total_cost={summary['total_cost']:.4f}",
                    f"- profit_factor={summary['profit_factor']:.4f}",
                    f"- avg_return_pct={summary['avg_return_pct']:.4f}",
                    f"- latest_computed_at={summary['latest_computed_at'] or '-'}",
                ]
            )
            if summary["total_net_pnl"] > 0:
                stance = (
                    f"{scope} live outcomes currently show positive net PnL. "
                    "Jue can use winning lanes as candidates for controlled sizing "
                    "only when validation and execution gates agree."
                )
            else:
                stance = (
                    f"{scope} live outcomes currently need repair. Jue should keep "
                    "active probing but improve price geometry, costs, and exit quality "
                    "before scaling."
                )
            self.service.write_page(
                scope=scope,
                page_type="performance",
                key="live_outcomes",
                title=f"{scope.upper()} Live Performance Outcomes",
                symbols=symbols[:80],
                content_sections={
                    "Current Stance": stance,
                    "Durable Facts": durable_facts,
                    "Evidence Links": "\n".join(
                        f"- {ref['source_type']}:{ref['source_id']}"
                        for ref in source_refs
                    )
                    or "- No linked evidence.",
                    "Trading History": "\n".join(latest_lines)
                    or "- No live block outcome rows.",
                    "Lessons": (
                        "- Winning outcomes are sizing evidence only after repeatable "
                        "entry quality and cost precision are present.\n"
                        "- Losing outcomes are repair evidence: convert them into better "
                        "waiting entries, narrower candidate filters, or cleaner exits."
                    ),
                    "Contradictions": (
                        "- If gross PnL is positive but net PnL is weak, costs and spread "
                        "are eating the edge.\n"
                        "- If win rate improves while expectancy stays weak, reward/risk "
                        "or exit timing still needs repair."
                    ),
                    "Open Questions": (
                        "- Which lane produced the latest positive expectancy?\n"
                        "- Which symbols repeatedly lose after entry despite valid signals?\n"
                        "- Are costs recorded precisely enough for scaling decisions?"
                    ),
                    "Next Context Pack Summary": (
                        f"{scope} live performance: sample_count={summary['sample_count']}, "
                        f"total_net_pnl={summary['total_net_pnl']:.4f}, "
                        f"profit_factor={summary['profit_factor']:.4f}, "
                        f"win_rate={summary['win_rate']:.4f}. Use this as live edge "
                        "evidence before increasing aggression."
                    ),
                    "Performance Evidence": durable_facts,
                    "Cost Friction": (
                        f"- total_cost={summary['total_cost']:.4f}\n"
                        f"- cost_to_abs_gross_ratio={summary['cost_to_abs_gross_ratio']:.4f}"
                    ),
                    "Latest Blocks": "\n".join(latest_lines)
                    or "- No live block outcome rows.",
                    "Repair Actions": (
                        "- Feed losing blocks into reflection and validation repair.\n"
                        "- Prefer blocks whose live outcomes improve net PnL after costs, "
                        "not just gross direction calls."
                    ),
                },
                source_refs=source_refs,
                confidence=0.82,
                freshness="fresh",
            )
            self._upsert_live_performance_page_effectiveness(
                scope=scope,
                rows=rows,
                summary=summary,
            )
            updated_count += 1
        return updated_count

    def _upsert_live_performance_page_effectiveness(
        self,
        *,
        scope: str,
        rows: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> None:
        if not rows:
            return
        venues = sorted(
            {
                str(row.get("venue") or row.get("raw_venue") or "").strip().lower()
                for row in rows
                if str(row.get("venue") or row.get("raw_venue") or "").strip()
            }
        )
        metric_sources = sorted(
            {
                str(row.get("metric_source") or "").strip()
                for row in rows
                if str(row.get("metric_source") or "").strip()
            }
        )
        sample_count = int(summary.get("sample_count") or len(rows))
        win_rate = self._float(summary.get("win_rate"))
        avg_return_pct = self._float(summary.get("avg_return_pct"))
        expectancy = (
            self._float(summary.get("total_net_pnl")) / sample_count
            if sample_count > 0
            else 0.0
        )
        helpful_score = self._clamp(
            avg_return_pct + ((win_rate - 0.5) * 4.0),
            -10.0,
            10.0,
        )
        confidence = min(sample_count / 5.0, 1.0)
        status = "probe"
        if sample_count >= 5 and helpful_score > 0.5 and expectancy > 0:
            status = "active"
        elif sample_count >= 5 and (helpful_score < -0.5 or expectancy < 0):
            status = "degraded"
        page_id = self.service.page_id(
            scope=scope,
            page_type="performance",
            key="live_outcomes",
        )
        reasons = [
            "source=live_performance_page",
            f"page_id={page_id}",
            f"sample_count={sample_count}",
            f"win_rate={win_rate:.4f}",
            f"expectancy={expectancy:.4f}",
            f"avg_return_pct={avg_return_pct:.4f}",
            f"total_net_pnl={self._float(summary.get('total_net_pnl')):.4f}",
            f"profit_factor={self._float(summary.get('profit_factor')):.4f}",
        ]
        if metric_sources:
            reasons.append(f"metric_source={','.join(metric_sources)}")
        self.service.upsert_page_effectiveness(
            {
                "page_id": page_id,
                "decision_scope": scope,
                "venue": venues[0] if len(venues) == 1 else "",
                "horizon": "",
                "sample_count": sample_count,
                "win_rate": win_rate,
                "expectancy": expectancy,
                "avg_return_pct": avg_return_pct,
                "median_mae_pct": 0.0,
                "drawdown_pressure": max(0.0, -avg_return_pct),
                "helpful_score": helpful_score,
                "confidence": confidence,
                "status": status,
                "reasons": reasons,
                "updated_at": str(summary.get("latest_computed_at") or ""),
            }
        )

    def _live_performance_summary(
        self,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        pnl_values = [self._float(row.get("pnl")) for row in rows]
        gross_values = [self._float(row.get("gross_pnl")) for row in rows]
        cost_values = [self._float(row.get("cost_total")) for row in rows]
        return_pct_values = [
            self._float(row.get("return_pct"))
            for row in rows
            if row.get("return_pct") is not None
        ]
        wins = [value for value in pnl_values if value > 0]
        losses = [value for value in pnl_values if value < 0]
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        if gross_wins > 0 and gross_losses == 0:
            profit_factor = 999.0
        elif gross_losses > 0:
            profit_factor = gross_wins / gross_losses
        else:
            profit_factor = 0.0
        total_abs_gross = sum(abs(value) for value in gross_values)
        total_cost = sum(cost_values)
        return {
            "sample_count": len(rows),
            "win_rate": len(wins) / len(rows) if rows else 0.0,
            "total_net_pnl": sum(pnl_values),
            "total_gross_pnl": sum(gross_values),
            "total_cost": total_cost,
            "profit_factor": profit_factor,
            "avg_return_pct": (
                sum(return_pct_values) / len(return_pct_values)
                if return_pct_values
                else 0.0
            ),
            "cost_to_abs_gross_ratio": (
                total_cost / total_abs_gross if total_abs_gross > 0 else 0.0
            ),
            "latest_computed_at": str(rows[0].get("computed_at") or "")
            if rows
            else "",
        }

    def _metric_for(
        self,
        scope: str,
        playbook_id: str,
        outcomes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        pnl_values = [self._float(outcome.get("pnl")) for outcome in outcomes]
        sample_count = len(pnl_values)
        wins = [value for value in pnl_values if value > 0]
        losses = [value for value in pnl_values if value < 0]
        win_rate = len(wins) / sample_count if sample_count else 0.0
        expectancy = sum(pnl_values) / sample_count if sample_count else 0.0
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        if gross_wins > 0 and gross_losses == 0:
            profit_factor = 999.0
        elif gross_losses > 0:
            profit_factor = gross_wins / gross_losses
        else:
            profit_factor = 0.0
        max_drawdown_pct = self._max_drawdown_pct(outcomes)
        status = self._status(
            sample_count=sample_count,
            expectancy=expectancy,
            profit_factor=profit_factor,
            max_drawdown_pct=max_drawdown_pct,
        )
        reasons = [
            f"sample_count={sample_count}",
            f"expectancy={expectancy:.4f}",
            f"profit_factor={profit_factor:.4f}",
            f"max_drawdown_pct={max_drawdown_pct:.4f}",
            f"win_rate={win_rate:.4f}",
        ]
        raw_scopes = sorted(
            {
                str(outcome.get("raw_scope") or "")
                for outcome in outcomes
                if str(outcome.get("raw_scope") or "").strip()
            }
        )
        raw_venues = sorted(
            {
                str(outcome.get("raw_venue") or "")
                for outcome in outcomes
                if str(outcome.get("raw_venue") or "").strip()
            }
        )
        base_playbooks = sorted(
            {
                str(outcome.get("base_playbook_id") or "")
                for outcome in outcomes
                if str(outcome.get("base_playbook_id") or "").strip()
            }
        )
        raw_playbooks = sorted(
            {
                str(outcome.get("raw_playbook_id") or "")
                for outcome in outcomes
                if str(outcome.get("raw_playbook_id") or "").strip()
            }
        )
        metric_sources = sorted(
            {
                str(outcome.get("metric_source") or "")
                for outcome in outcomes
                if str(outcome.get("metric_source") or "").strip()
            }
        )
        if metric_sources:
            reasons.append(f"metric_source={','.join(metric_sources)}")
        if raw_scopes:
            reasons.append(f"raw_scope={','.join(raw_scopes)}")
        if raw_venues:
            reasons.append(f"raw_venue={','.join(raw_venues)}")
        if base_playbooks:
            reasons.append(f"base_playbook_id={','.join(base_playbooks)}")
        if raw_playbooks:
            reasons.append(f"raw_playbook_id={','.join(raw_playbooks)}")
        return {
            "page_id": f"{scope}.playbook.{playbook_id}",
            "scope": scope,
            "playbook_id": playbook_id,
            "sample_count": sample_count,
            "win_rate": win_rate,
            "expectancy": expectancy,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown_pct,
            "avg_holding_minutes": 0.0,
            "status": status,
            "reasons": reasons,
        }

    def _max_drawdown_pct(self, outcomes: list[dict[str, Any]]) -> float:
        return_pct_values = [
            self._float(outcome.get("return_pct"))
            for outcome in outcomes
            if outcome.get("return_pct") is not None
        ]
        if return_pct_values:
            cumulative = 0.0
            peak = 0.0
            max_drawdown = 0.0
            for value in return_pct_values:
                cumulative += value
                peak = max(peak, cumulative)
                max_drawdown = max(max_drawdown, peak - cumulative)
            return max_drawdown
        drawdown_pct_values = [
            abs(self._float(outcome.get("drawdown_pct")))
            for outcome in outcomes
            if outcome.get("drawdown_pct") is not None
        ]
        if drawdown_pct_values:
            return max(drawdown_pct_values)
        return 0.0

    def _status(
        self,
        *,
        sample_count: int,
        expectancy: float,
        profit_factor: float,
        max_drawdown_pct: float,
    ) -> str:
        if sample_count >= 10 and (max_drawdown_pct > 12 or profit_factor < 0.8):
            return "paused"
        if (
            sample_count >= 10
            and expectancy > 0
            and profit_factor >= 1.3
            and max_drawdown_pct <= 8
        ):
            return "active"
        if sample_count >= 10 and expectancy <= 0:
            return "degraded"
        return "probe"

    def _format_optional_pct(self, value: Any) -> str:
        if value is None:
            return "-"
        return f"{self._float(value):.4f}"

    def _tables(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {str(row["name"]) for row in rows}

    def _columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}

    def _scope_from_venue(self, venue: str) -> str:
        text = venue.strip().lower()
        if "binance" in text:
            return "binance"
        if "kis" in text or "kr" in text:
            return "kis"
        return self._clean_key(text).lower() or "unknown"

    def _playbook_id(self, payload: dict[str, Any]) -> str:
        metadata = payload.get("metadata")
        candidates: list[Any] = []
        if isinstance(metadata, dict):
            candidates.extend(
                metadata.get(key)
                for key in ("playbook_id", "lane", "horizon", "setup")
            )
        candidates.extend(
            payload.get(key) for key in ("playbook_id", "lane", "horizon", "setup")
        )
        for candidate in candidates:
            text = self._clean_key(candidate)
            if text:
                return text
        return "reflection_lessons"

    def _outcome_playbook_id(self, playbook_id: str) -> str:
        # `live`, `outcome`, and `source` are reserved persisted namespaces.
        # Outcome fixtures keep simple IDs unchanged, but namespace-like IDs are
        # escaped so they cannot overwrite live metrics.
        if self._is_reserved_outcome_playbook_id(playbook_id):
            return f"outcome.{playbook_id}"
        return playbook_id

    def _is_reserved_outcome_playbook_id(self, playbook_id: str) -> bool:
        parts = playbook_id.lower().split(".")
        if parts[0] in {"live", "outcome", "source"}:
            return True
        return parts[0] in {
            "binance",
            "binance_spot",
            "binance_futures",
            "kis",
            "kr",
            "krx",
        }

    def _json_object(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def _float(self, value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _clamp(self, value: float, low: float, high: float) -> float:
        return max(float(low), min(float(high), float(value)))

    def _clean_key(self, value: Any) -> str:
        text = str(value or "").strip()
        cleaned = "".join(
            character if character.isalnum() or character in "_.-" else "_"
            for character in text
        ).strip("._-")
        return cleaned or ""
