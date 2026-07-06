from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tradecraft.services.jue_wiki import JueWikiConfig, JueWikiService
from tradecraft.services.jue_wiki_performance import JueWikiPerformanceProjector


def _service(tmp_path: Path) -> JueWikiService:
    return JueWikiService(
        JueWikiConfig(
            root_path=tmp_path / "jue_wiki",
            db_path=tmp_path / "jue_wiki" / "wiki.db",
        )
    )


def test_project_playbook_outcomes_writes_profitable_metric(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "performance.db"
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE playbook_outcomes (
                scope TEXT,
                playbook_id TEXT,
                pnl REAL,
                drawdown_pct REAL,
                created_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO playbook_outcomes VALUES (
                'kis', 'reflection_lessons', ?, ?, ?
            )
            """,
            [
                (1.0, 1.0, "2026-06-01T00:00:00+00:00"),
                (1.5, 1.2, "2026-06-02T00:00:00+00:00"),
                (-0.2, 2.5, "2026-06-03T00:00:00+00:00"),
                (0.8, 2.0, "2026-06-04T00:00:00+00:00"),
                (0.4, 1.0, "2026-06-05T00:00:00+00:00"),
                (1.1, 1.0, "2026-06-06T00:00:00+00:00"),
            ],
        )
    service = _service(tmp_path)
    projector = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    )

    outcomes = projector._load_outcomes()
    direct_metric = projector._metric_for("kis", "reflection_lessons", outcomes)

    result = projector.project_all()

    assert direct_metric["sample_count"] == 6
    assert result == {"status": "ok", "updated_count": 1}
    metric = service.playbook_metric("kis.playbook.reflection_lessons")
    assert metric["page_id"] == "kis.playbook.reflection_lessons"
    assert metric["playbook_id"] == "reflection_lessons"
    assert metric["sample_count"] == 6
    assert metric["win_rate"] > 0.6
    assert metric["status"] in {"active", "probe"}
    assert "base_playbook_id=reflection_lessons" in metric["reasons"]


def test_project_missing_db_or_table_returns_ok_zero_updates(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    missing_result = JueWikiPerformanceProjector(
        service,
        performance_db_path=tmp_path / "missing.db",
    ).project_all()
    assert missing_result == {"status": "ok", "updated_count": 0}

    empty_db = tmp_path / "empty.db"
    with sqlite3.connect(empty_db):
        pass

    missing_table_result = JueWikiPerformanceProjector(
        service,
        performance_db_path=empty_db,
    ).project_all()
    assert missing_table_result == {"status": "ok", "updated_count": 0}


def test_project_malformed_supported_table_returns_explicit_error(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "performance.db"
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE playbook_outcomes (
                scope TEXT,
                playbook_id TEXT
            )
            """
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    assert result["status"] == "error"
    assert result["updated_count"] == 0
    assert "playbook_outcomes" in result["error_message"]


def test_metric_for_keeps_small_high_drawdown_sample_in_probe(
    tmp_path: Path,
) -> None:
    projector = JueWikiPerformanceProjector(
        _service(tmp_path),
        performance_db_path=tmp_path / "unused.db",
    )

    metric = projector._metric_for(
        "kis",
        "small_sample",
        [
            {
                "scope": "kis",
                "playbook_id": "small_sample",
                "metric_source": "playbook_outcomes",
                "pnl": 1.0,
                "return_pct": None,
                "drawdown_pct": 99.0,
            }
        ],
    )

    assert metric["sample_count"] == 1
    assert metric["max_drawdown_pct"] == 99.0
    assert metric["status"] == "probe"


def test_project_live_block_performance_uses_alpha_source_json_playbook(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "live_performance.db"
    source_json = json.dumps(
        {"metadata": {"playbook_id": "breakout_lane"}},
        ensure_ascii=False,
    )
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                gross_pnl REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                source_json TEXT,
                computed_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO live_block_performance VALUES (
                'binance', 'unknown_venue', ?, 'BTCUSDT', ?, ?, ?, ?, ?, ?
            )
            """,
            [
                (
                    "bn_1",
                    12.0,
                    13.0,
                    1.1,
                    1,
                    source_json,
                    "2026-06-01T00:00:00+00:00",
                ),
                (
                    "bn_2",
                    -3.0,
                    -4.0,
                    -0.4,
                    1,
                    source_json,
                    "2026-06-02T00:00:00+00:00",
                ),
                (
                    "bn_ignored",
                    999.0,
                    999.0,
                    9.9,
                    0,
                    source_json,
                    "2026-06-03T00:00:00+00:00",
                ),
            ],
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    assert result == {"status": "ok", "updated_count": 1}
    metric = service.playbook_metric("binance.playbook.live.unknown_venue.breakout_lane")
    assert metric["page_id"] == "binance.playbook.live.unknown_venue.breakout_lane"
    assert metric["scope"] == "binance"
    assert metric["playbook_id"] == "live.unknown_venue.breakout_lane"
    assert metric["sample_count"] == 2
    assert "raw_scope=binance" in metric["reasons"]
    assert "raw_venue=unknown_venue" in metric["reasons"]


def test_project_live_block_performance_keeps_risk_management_non_alpha_rows(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "live_performance.db"
    source_json = json.dumps(
        {
            "metadata": {
                "horizon": "mid",
                "entry_attribution": "external_existing_position",
                "management_attribution": "jue_block_management",
                "scorecard_attribution": "risk_management_only",
            }
        },
        ensure_ascii=False,
    )
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                gross_pnl REAL,
                cost_total REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                include_in_risk_management INTEGER,
                source_json TEXT,
                computed_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_block_performance VALUES (
                'kis', 'kis', 'kis_adopted_1', '277810',
                19000.0, 24000.0, 5000.0, 9.5, 0, 1, ?, ?
            )
            """,
            (source_json, "2026-07-02T06:00:00+00:00"),
        )
    service = _service(tmp_path)
    projector = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    )

    outcomes = projector._load_outcomes()
    result = projector.project_all()

    assert outcomes[0]["metric_source"] == "live_block_risk_management"
    assert outcomes[0]["base_playbook_id"] == "risk_management.mid"
    assert outcomes[0]["playbook_id"] == "live.kis.risk_management.mid"
    assert result == {"status": "ok", "updated_count": 1}
    metric = service.playbook_metric("kis.playbook.live.kis.risk_management.mid")
    assert metric["sample_count"] == 1
    assert "metric_source=live_block_risk_management" in metric["reasons"]
    page = service.read_page("kis.performance.live_outcomes")
    assert page["status"] == "ok"
    assert "kis_adopted_1 / 277810" in page["content"]
    assert "sample_count=1" in page["content"]


def test_project_live_block_performance_scores_live_outcomes_page_for_selector(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "live_performance.db"
    source_json = json.dumps(
        {
            "metadata": {
                "horizon": "mid",
                "entry_attribution": "external_existing_position",
                "management_attribution": "jue_block_management",
                "scorecard_attribution": "risk_management_only",
            }
        },
        ensure_ascii=False,
    )
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                gross_pnl REAL,
                cost_total REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                include_in_risk_management INTEGER,
                source_json TEXT,
                computed_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_block_performance VALUES (
                'kis', 'kis', 'kis_existing_1', '005930',
                12000.0, 14000.0, 2000.0, 3.4, 0, 1, ?, ?
            )
            """,
            (source_json, "2026-07-02T06:00:00+00:00"),
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    selector_metrics = service.page_effectiveness_map(
        decision_scope="kis",
        horizons=["mid"],
    )
    metric = selector_metrics["kis.performance.live_outcomes"]
    reasons = json.loads(metric["reasons_json"])
    assert result == {"status": "ok", "updated_count": 1}
    assert metric["sample_count"] == 1
    assert metric["venue"] == "kis"
    assert metric["horizon"] == ""
    assert metric["fallback_reason"] == "general_horizon_metric"
    assert "metric_source=live_block_risk_management" in reasons
    assert "page_id=kis.performance.live_outcomes" in reasons


def test_project_live_block_performance_writes_scope_performance_pages(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "live_performance.db"
    source_json = json.dumps({"metadata": {"lane": "mid"}}, ensure_ascii=False)
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                gross_pnl REAL,
                cost_total REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                source_json TEXT,
                computed_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO live_block_performance VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?
            )
            """,
            [
                (
                    "kis",
                    "kis",
                    "kis_1",
                    "005930",
                    1200.0,
                    1250.0,
                    50.0,
                    1.2,
                    source_json,
                    "2026-07-01T01:00:00+00:00",
                ),
                (
                    "kis",
                    "kis",
                    "kis_2",
                    "000660",
                    -400.0,
                    -350.0,
                    50.0,
                    -0.4,
                    source_json,
                    "2026-07-01T02:00:00+00:00",
                ),
                (
                    "binance",
                    "binance_futures",
                    "bnb_1",
                    "BTCUSDT",
                    2.5,
                    3.0,
                    0.5,
                    0.5,
                    source_json,
                    "2026-07-01T03:00:00+00:00",
                ),
            ],
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    assert result == {"status": "ok", "updated_count": 2}
    kis_page = service.read_page("kis.performance.live_outcomes")
    binance_page = service.read_page("binance.performance.live_outcomes")
    kis_sources = service.page_sources("kis.performance.live_outcomes")
    assert kis_page["status"] == "ok"
    assert "sample_count=2" in kis_page["content"]
    assert "total_net_pnl=800.0000" in kis_page["content"]
    assert "total_cost=100.0000" in kis_page["content"]
    assert "profit_factor=3.0000" in kis_page["content"]
    assert "kis_2 / 000660" in kis_page["content"]
    assert binance_page["status"] == "ok"
    assert "sample_count=1" in binance_page["content"]
    assert "total_net_pnl=2.5000" in binance_page["content"]
    assert any(
        row["source_type"] == "live_block_performance"
        and row["source_id"] == "kis_1"
        for row in kis_sources["source_refs"]
    )


@pytest.mark.parametrize(
    ("source_payload", "expected_playbook_id"),
    [
        ({"metadata": {"playbook_id": "meta_playbook"}}, "meta_playbook"),
        ({"metadata": {"lane": "meta_lane"}}, "meta_lane"),
        ({"metadata": {"horizon": "meta_horizon"}}, "meta_horizon"),
        ({"metadata": {"setup": "meta_setup"}}, "meta_setup"),
        ({"playbook_id": "top_playbook"}, "top_playbook"),
        ({"lane": "top_lane"}, "top_lane"),
        ({"horizon": "top_horizon"}, "top_horizon"),
        ({"setup": "top_setup"}, "top_setup"),
        ({}, "reflection_lessons"),
    ],
)
def test_live_source_json_infers_all_playbook_paths(
    tmp_path: Path,
    source_payload: dict[str, object],
    expected_playbook_id: str,
) -> None:
    performance_db = tmp_path / "live_performance.db"
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                source_json TEXT,
                computed_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_block_performance VALUES (
                'binance', 'binance', 'bn_1', 'BTCUSDT', 2.0, 0.3, 1, ?, ''
            )
            """,
            (json.dumps(source_payload, ensure_ascii=False),),
        )
    service = _service(tmp_path)
    projector = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    )

    outcomes = projector._load_outcomes()
    result = projector.project_all()

    assert outcomes[0]["base_playbook_id"] == expected_playbook_id
    assert outcomes[0]["playbook_id"] == f"live.binance.{expected_playbook_id}"
    assert result == {"status": "ok", "updated_count": 1}
    metric = service.playbook_metric(
        f"binance.playbook.live.binance.{expected_playbook_id}"
    )
    assert metric["status"] == "probe"
    assert metric["sample_count"] == 1


def test_live_scope_and_venue_keep_same_playbook_in_separate_metrics(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "live_performance.db"
    source_json = json.dumps({"metadata": {"lane": "trend"}}, ensure_ascii=False)
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                source_json TEXT,
                computed_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO live_block_performance VALUES (
                'binance', ?, ?, 'BTCUSDT', ?, 0.2, 1, ?, ?
            )
            """,
            [
                (
                    "binance_spot",
                    "spot_1",
                    1.0,
                    source_json,
                    "2026-06-01T00:00:00+00:00",
                ),
                (
                    "binance_futures",
                    "futures_1",
                    2.0,
                    source_json,
                    "2026-06-02T00:00:00+00:00",
                ),
            ],
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    spot_metric = service.playbook_metric("binance.playbook.live.binance_spot.trend")
    futures_metric = service.playbook_metric(
        "binance.playbook.live.binance_futures.trend"
    )
    merged_metric = service.playbook_metric("binance.playbook.trend")
    assert result == {"status": "ok", "updated_count": 2}
    assert spot_metric["sample_count"] == 1
    assert spot_metric["playbook_id"] == "live.binance_spot.trend"
    assert "raw_venue=binance_spot" in spot_metric["reasons"]
    assert futures_metric["sample_count"] == 1
    assert futures_metric["playbook_id"] == "live.binance_futures.trend"
    assert "raw_venue=binance_futures" in futures_metric["reasons"]
    assert merged_metric == {"status": "not_found", "page_id": "binance.playbook.trend"}


def test_playbook_outcomes_and_live_same_playbook_do_not_blend(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "performance.db"
    source_json = json.dumps(
        {"metadata": {"playbook_id": "volatile_attack"}},
        ensure_ascii=False,
    )
    with sqlite3.connect(performance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE playbook_outcomes (
                scope TEXT,
                playbook_id TEXT,
                pnl REAL,
                drawdown_pct REAL,
                created_at TEXT
            );
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                source_json TEXT,
                computed_at TEXT
            );
            INSERT INTO playbook_outcomes VALUES (
                'binance', 'volatile_attack', 1.0, 0.0, ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO live_block_performance VALUES (
                'binance', 'binance', 'bn_1', 'BTCUSDT', 2.0, 0.2, 1, ?, ''
            )
            """,
            (source_json,),
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    backtest_metric = service.playbook_metric("binance.playbook.volatile_attack")
    live_metric = service.playbook_metric(
        "binance.playbook.live.binance.volatile_attack"
    )
    assert result == {"status": "ok", "updated_count": 2}
    assert backtest_metric["sample_count"] == 1
    assert backtest_metric["expectancy"] == 1.0
    assert "metric_source=playbook_outcomes" in backtest_metric["reasons"]
    assert live_metric["sample_count"] == 1
    assert live_metric["expectancy"] == 2.0
    assert "metric_source=live_block_performance" in live_metric["reasons"]


def test_live_namespace_prevents_collision_with_fixture_live_prefix(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "performance.db"
    source_json = json.dumps({"metadata": {"playbook_id": "edge"}}, ensure_ascii=False)
    with sqlite3.connect(performance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE playbook_outcomes (
                scope TEXT,
                playbook_id TEXT,
                pnl REAL,
                drawdown_pct REAL,
                created_at TEXT
            );
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                source_json TEXT,
                computed_at TEXT
            );
            INSERT INTO playbook_outcomes VALUES (
                'binance', 'live.edge', 1.0, 0.0, ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO live_block_performance VALUES (
                'binance', 'binance', 'bn_1', 'BTCUSDT', 2.0, 0.2, 1, ?, ''
            )
            """,
            (source_json,),
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    fixture_metric = service.playbook_metric("binance.playbook.outcome.live.edge")
    live_metric = service.playbook_metric("binance.playbook.live.binance.edge")
    assert result == {"status": "ok", "updated_count": 2}
    assert fixture_metric["sample_count"] == 1
    assert fixture_metric["expectancy"] == 1.0
    assert fixture_metric["playbook_id"] == "outcome.live.edge"
    assert "base_playbook_id=live.edge" in fixture_metric["reasons"]
    assert "metric_source=playbook_outcomes" in fixture_metric["reasons"]
    assert live_metric["sample_count"] == 1
    assert live_metric["expectancy"] == 2.0
    assert live_metric["playbook_id"] == "live.binance.edge"
    assert "base_playbook_id=edge" in live_metric["reasons"]
    assert "metric_source=live_block_performance" in live_metric["reasons"]


def test_outcome_reserved_live_venue_prefix_cannot_overwrite_live_metric(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "performance.db"
    source_json = json.dumps({"metadata": {"playbook_id": "edge"}}, ensure_ascii=False)
    with sqlite3.connect(performance_db) as conn:
        conn.executescript(
            """
            CREATE TABLE playbook_outcomes (
                scope TEXT,
                playbook_id TEXT,
                pnl REAL,
                drawdown_pct REAL,
                created_at TEXT
            );
            CREATE TABLE live_block_performance (
                scope TEXT,
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                source_json TEXT,
                computed_at TEXT
            );
            INSERT INTO playbook_outcomes VALUES (
                'binance', 'live.binance.edge', 1.0, 0.0, ''
            );
            """
        )
        conn.execute(
            """
            INSERT INTO live_block_performance VALUES (
                'binance', 'binance', 'bn_1', 'BTCUSDT', 2.0, 0.2, 1, ?, ''
            )
            """,
            (source_json,),
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    outcome_metric = service.playbook_metric(
        "binance.playbook.outcome.live.binance.edge"
    )
    live_metric = service.playbook_metric("binance.playbook.live.binance.edge")
    assert result == {"status": "ok", "updated_count": 2}
    assert outcome_metric["sample_count"] == 1
    assert outcome_metric["expectancy"] == 1.0
    assert outcome_metric["playbook_id"] == "outcome.live.binance.edge"
    assert "raw_playbook_id=live.binance.edge" in outcome_metric["reasons"]
    assert "metric_source=playbook_outcomes" in outcome_metric["reasons"]
    assert live_metric["sample_count"] == 1
    assert live_metric["expectancy"] == 2.0
    assert live_metric["playbook_id"] == "live.binance.edge"
    assert "metric_source=live_block_performance" in live_metric["reasons"]


def test_live_block_performance_without_scope_derives_scope_from_venue(
    tmp_path: Path,
) -> None:
    performance_db = tmp_path / "live_performance.db"
    source_json = json.dumps({"metadata": {"lane": "momentum"}}, ensure_ascii=False)
    with sqlite3.connect(performance_db) as conn:
        conn.execute(
            """
            CREATE TABLE live_block_performance (
                venue TEXT,
                block_id TEXT,
                symbol TEXT,
                net_pnl REAL,
                pnl_pct REAL,
                include_in_jue_alpha INTEGER,
                source_json TEXT,
                computed_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO live_block_performance VALUES (
                'binance_futures', 'bn_1', 'BTCUSDT', 3.0, 0.4, 1, ?, ''
            )
            """,
            (source_json,),
        )
    service = _service(tmp_path)

    result = JueWikiPerformanceProjector(
        service,
        performance_db_path=performance_db,
    ).project_all()

    metric = service.playbook_metric("binance.playbook.live.binance_futures.momentum")
    assert result == {"status": "ok", "updated_count": 1}
    assert metric["scope"] == "binance"
    assert metric["playbook_id"] == "live.binance_futures.momentum"
    assert metric["sample_count"] == 1
    assert "raw_venue=binance_futures" in metric["reasons"]
