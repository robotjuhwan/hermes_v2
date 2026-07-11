from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _html() -> str:
    return (ROOT / "src/tradecraft/web/static/index.html").read_text()


def _js() -> str:
    return (ROOT / "src/tradecraft/web/static/app.js").read_text()


def _css() -> str:
    return (ROOT / "src/tradecraft/web/static/style.css").read_text()


def _tabs_js() -> str:
    return (ROOT / "src/tradecraft/web/static/tabs.js").read_text()


def _ui_shared_js() -> str:
    return (ROOT / "src/tradecraft/web/static/ui_shared.js").read_text()


def _ui_formatters_js() -> str:
    return (ROOT / "src/tradecraft/web/static/ui_formatters.js").read_text()


def _ui_ops_js() -> str:
    return (ROOT / "src/tradecraft/web/static/ui_ops.js").read_text()


def _ui_auth_js() -> str:
    return (ROOT / "src/tradecraft/web/static/ui_auth.js").read_text()


def _system_metrics_widget_js() -> str:
    return (ROOT / "src/tradecraft/web/static/system_metrics_widget.js").read_text()


def _kis_quick_view_js() -> str:
    return (ROOT / "src/tradecraft/web/static/kis_quick_view.js").read_text()


def _ui_live_authority_js() -> str:
    return (ROOT / "src/tradecraft/web/static/ui_live_authority.js").read_text()


def _ui_shell_js() -> str:
    return (ROOT / "src/tradecraft/web/static/ui_shell.js").read_text()


def _live_authority_panel_body() -> str:
    live_js = _ui_live_authority_js()
    start = live_js.index("function renderLiveAuthorityPanel")
    end = live_js.index("window.HERMES_UI_LIVE_AUTHORITY", start)
    return live_js[start:end]


def _binance_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/binance_tab.js").read_text()


def _settings_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/settings_tab.js").read_text()


def _crypto_research_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/crypto_research_tab.js").read_text()


def _strategy_intel_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/strategy_intel_tab.js").read_text()


def _market_judge_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/market_judge_tab.js").read_text()


def _runtime_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/runtime_tab.js").read_text()


def _rebalance_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/rebalance_tab.js").read_text()


def _backtest_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/backtest_tab.js").read_text()


def _kis_trader_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/kis_trader_tab.js").read_text()


def _script_cache_busted(html: str, script_name: str) -> bool:
    return f'/static/{script_name}?v=' in html


def _etf_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/etf_tab.js").read_text()


def _memory_tab_js() -> str:
    return (ROOT / "src/tradecraft/web/static/memory_tab.js").read_text()


def test_static_ui_loads_tab_registry_before_main_app() -> None:
    html = _html()

    assert "/static/tabs.js" in html
    assert html.index("/static/tabs.js") < html.index("/static/app.js")
    tabs_js = _tabs_js()
    js = _js()
    assert "window.HERMES_UI_TABS" in tabs_js
    assert "helperTabs" in tabs_js
    assert "activeBlockTabs" in tabs_js
    assert "window.HERMES_UI_TABS" in js


def test_system_metrics_widget_uses_lightweight_polling_interval() -> None:
    html = _html()
    js = _js()
    widget_js = _system_metrics_widget_js()

    assert "/static/system_metrics_widget.js" in html
    assert html.index("/static/system_metrics_widget.js") < html.index("/static/app.js")
    assert "window.HERMES_SYSTEM_METRICS_WIDGET" in widget_js
    assert "function renderSystemMetricsWidget(" in widget_js
    assert "HERMES 프로세스 감지 대기" in widget_js
    assert "const SYSTEM_METRICS_REFRESH_MS = 60_000;" in js
    assert "const SYSTEM_METRICS_COLLAPSED_REFRESH_MS = 300_000;" in js
    assert "const SYSTEM_METRICS_MIN_REQUEST_GAP_MS = 30_000;" in js
    assert "const SYSTEM_METRICS_WIDGET = window.HERMES_SYSTEM_METRICS_WIDGET || {};" in js
    assert "inFlight: false" in js
    assert "if (state.systemMetrics.inFlight) return;" in js


def test_kis_quick_view_module_loads_before_main_app() -> None:
    html = _html()
    js = _js()
    kis_js = _kis_quick_view_js()

    assert "/static/kis_quick_view.js" in html
    assert html.index("/static/kis_quick_view.js") < html.index("/static/app.js")
    assert "window.HERMES_KIS_QUICK_VIEW" in kis_js
    assert "renderKisQuickStripHtml" in kis_js
    assert "renderKisAccountHoldingsPanel" in kis_js
    assert "KIS 장애나 국장 데이터 공백이 아니라 보호 API 인증 대기입니다" in kis_js
    assert "const KIS_QUICK_VIEW = window.HERMES_KIS_QUICK_VIEW || {};" in js
    assert "KIS_QUICK_VIEW.renderKisQuickStripHtml" in js
    assert "function systemMetricsRefreshIntervalMs()" in js
    assert "state.systemMetrics.collapsed ? SYSTEM_METRICS_COLLAPSED_REFRESH_MS : SYSTEM_METRICS_REFRESH_MS" in js
    assert "}, systemMetricsRefreshIntervalMs());" in js
    assert "syncSystemMetricsRefresh();" in js
    assert "loadSystemMetrics({ force: true });" in js


def test_kis_quick_view_treats_missing_token_as_auth_gated() -> None:
    js_path = ROOT / "src/tradecraft/web/static/kis_quick_view.js"
    script = f"""
global.window = {{}};
require({json.dumps(str(js_path))});
const html = window.HERMES_KIS_QUICK_VIEW.renderKisQuickStripHtml({{
  authRequired: false,
  hasAdminToken: false,
  authMessage: "운영 토큰을 입력하면 국장/블록/운영 데이터를 불러옵니다.",
  dashboardVenues: [],
  kisBlockStatus: null,
}}, {{
  escapeHTML: (value) => String(value ?? ""),
  fmtKRW: (value) => String(value),
  fmtNum: (value) => String(value),
  orderedVenuesForDisplay: (venues) => Array.isArray(venues) ? venues : [],
}});
console.log(html);
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "국장 계좌 인증 대기" in result.stdout
    assert "KIS 장애나 국장 데이터 공백이 아니라 보호 API 인증 대기입니다" in result.stdout
    assert "재부팅, 새 브라우저, 세션 삭제 후에는 다시 입력해야 합니다" in result.stdout
    assert "국장 계좌 대기" not in result.stdout
    assert "KIS 연결 상태를 확인" not in result.stdout


def test_kis_quick_view_prefers_kis_account_when_dashboard_kis_venue_is_stale() -> None:
    js_path = ROOT / "src/tradecraft/web/static/kis_quick_view.js"
    script = f"""
global.window = {{}};
require({json.dumps(str(js_path))});
const html = window.HERMES_KIS_QUICK_VIEW.renderKisQuickStripHtml({{
  hasAdminToken: true,
  dashboardVenues: [{{
    id: "kr_stock",
    label: "국장1",
    status: "stale",
    cache_status: "stale",
    cash_krw: 4010886,
    total_krw: 0,
    invested_krw: 0,
    assets: [],
    position_count: 0,
  }}],
  kisBlockStatus: {{
    account: {{
      status: "ok",
      account_label: "국장1",
      cash_krw: 4010886,
      orderable_cash_krw: 4010886,
      position_value_krw: 421745,
      total_value_krw: 4432631,
      unrealized_pnl_krw: 1234,
      position_count: 1,
      positions: [{{
        symbol: "360750",
        name: "TIGER 미국S&P500",
        qty: 4,
        market_value_krw: 114560,
        unrealized_pnl_krw: 1200,
      }}],
    }},
  }},
}}, {{
  escapeHTML: (value) => String(value ?? ""),
  fmtKRW: (value) => String(Math.round(Number(value || 0))),
  fmtNum: (value) => String(Number(value || 0)),
  orderedVenuesForDisplay: (venues) => Array.isArray(venues) ? venues : [],
}});
console.log(html);
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "TIGER 미국S&P500 4주" in result.stdout
    assert "총 4432631" in result.stdout
    assert "KIS 블록 계좌 스냅샷" in result.stdout
    assert "보유 종목 없음" not in result.stdout


def test_kis_quick_view_labels_stale_kis_dashboard_cache_as_last_good_balance() -> None:
    js_path = ROOT / "src/tradecraft/web/static/kis_quick_view.js"
    script = f"""
global.window = {{}};
require({json.dumps(str(js_path))});
const html = window.HERMES_KIS_QUICK_VIEW.renderKisQuickStripHtml({{
  hasAdminToken: true,
  dashboardVenues: [{{
    id: "kr_stock",
    label: "국장1",
    status: "stale",
    cache_status: "stale",
    cached_at: "2026-07-01T10:28:10.000Z",
    cash_krw: 4010886,
    total_krw: 4435546,
    invested_krw: 424660,
    unrealized_pnl_krw: 9194,
    total_value_basis: "broker_net_asset",
    assets: [
      {{ asset: "KRW", asset_name: "KRW", kind: "cash", qty: 4010886, value_krw: 4010886 }},
      {{ asset: "360750", asset_name: "TIGER 미국S&P500", kind: "stock", qty: 4, value_krw: 115460 }},
    ],
    position_count: 1,
  }}],
  kisBlockStatus: null,
}}, {{
  escapeHTML: (value) => String(value ?? ""),
  fmtKRW: (value) => String(Math.round(Number(value || 0))),
  fmtNum: (value) => String(Number(value || 0)),
  orderedVenuesForDisplay: (venues) => Array.isArray(venues) ? venues : [],
  nowMs: Date.parse("2026-07-01T11:00:07.000Z"),
}});
console.log(html);
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "TIGER 미국S&P500 4주" in result.stdout
    assert "최근 성공 잔고 · 32분 전" in result.stdout
    assert "stale-cache" in result.stdout
    assert "오래된 대시보드 캐시" not in result.stdout


def test_kis_quick_view_prefers_kis_account_when_dashboard_positions_have_only_codes() -> None:
    js_path = ROOT / "src/tradecraft/web/static/kis_quick_view.js"
    script = f"""
global.window = {{}};
require({json.dumps(str(js_path))});
const html = window.HERMES_KIS_QUICK_VIEW.renderKisQuickStripHtml({{
  hasAdminToken: true,
  dashboardVenues: [{{
    id: "kr_stock",
    label: "국장1",
    status: "fresh",
    cache_status: "fresh",
    cash_krw: 4010886,
    total_krw: 4435546,
    invested_krw: 424660,
    assets: [
      {{ asset: "KRW", asset_name: "KRW", kind: "cash", qty: 4010886, value_krw: 4010886 }},
      {{ asset: "360750", symbol: "360750", kind: "stock", qty: 4, value_krw: 115280 }},
      {{ asset: "379800", symbol: "379800", kind: "stock", qty: 4, value_krw: 105080 }},
    ],
    position_count: 2,
  }}],
  kisBlockStatus: {{
    account: {{
      status: "ok",
      account_label: "국장1",
      cash_krw: 4010886,
      orderable_cash_krw: 4010886,
      position_value_krw: 424660,
      total_value_krw: 4435546,
      position_count: 2,
      positions: [
        {{ symbol: "360750", name: "TIGER 미국S&P500", qty: 4, market_value_krw: 115280 }},
        {{ symbol: "379800", name: "KODEX 미국S&P500", qty: 4, market_value_krw: 105080 }},
      ],
    }},
  }},
}}, {{
  escapeHTML: (value) => String(value ?? ""),
  fmtKRW: (value) => String(Math.round(Number(value || 0))),
  fmtNum: (value) => String(Number(value || 0)),
  orderedVenuesForDisplay: (venues) => Array.isArray(venues) ? venues : [],
}});
console.log(html);
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "TIGER 미국S&P500 4주" in result.stdout
    assert "KODEX 미국S&P500 4주" in result.stdout
    assert "360750 4주" not in result.stdout
    assert "379800 4주" not in result.stdout


def test_static_ui_preloads_kis_quick_summary_independent_of_dashboard() -> None:
    js = _js()
    load_kis_body = js[js.index("async function loadKisBlocks"):js.index("function mergeKisBlockRows")]
    init_body = js[js.index("async function init()"):js.index("init();")]

    assert "renderKisQuickStrip();" in load_kis_body
    assert "renderGlobalExecutionMode();" in load_kis_body
    assert "const kisQuickPreload =" in init_body
    assert "loadKisBlocks({" in init_body
    assert "activeOnly: true" in init_body
    assert "silent: true" in init_body
    assert "skipKisBlocks: prioritizeKisBlocks || Boolean(kisQuickPreload)" in init_body


def test_nav_execution_mode_is_dynamic_not_hardcoded_paper() -> None:
    html = _html()
    js = _js()

    assert "Paper · 실주문 잠금</strong>" not in html
    assert 'id="globalExecutionModeText"' in html
    assert "function renderGlobalExecutionMode()" in js
    assert "state.kisBlockStatus" in js
    assert "state.binanceTrader.status" in js
    assert "state.opsReadiness?.live_trading_enabled" in js
    assert "LIVE · 실주문 활성" in js


def test_main_fetch_marks_auth_required_even_without_saved_token() -> None:
    js = _js()

    auth_branch = (
        "if (response.status === 401 || response.status === 403) {\n"
        "      error.authRequired = true;\n"
        "      markAuthRequired(message);"
    )
    assert auth_branch in js


def test_static_ui_does_not_keep_retired_kis_llm_trader_state() -> None:
    js = _js()

    assert "kisTraderStatus" not in js
    assert "kisTraderError" not in js


def test_runtime_tab_surfaces_cleanup_candidate_totals() -> None:
    html = _html()
    js = _js()
    runtime_js = _runtime_tab_js()

    assert "cleanup candidates" in runtime_js
    assert "storage.cleanup_candidate_count" in runtime_js
    assert "storage.cleanup_candidate_bytes" in runtime_js
    assert "storage.total_size_mb" in runtime_js
    assert "storage.large_file_count" in runtime_js
    assert "storage.cleanup_candidate_size_mb" in runtime_js
    assert "large files" in runtime_js
    assert "runtimeStorageCleanup" in js
    assert "runRuntimeStorageCleanup" in js
    assert "/runtime/storage/cleanup?dry_run=" in js
    assert "would_delete_count" in runtime_js
    assert "actual_deleted_count" in runtime_js
    assert '"정리 후보"' in runtime_js
    assert "data-runtime-storage-cleanup" in runtime_js
    assert 'data-runtime-storage-cleanup="dry-run"' in runtime_js
    assert 'data-runtime-storage-cleanup="apply"' in runtime_js
    assert "Runtime 정리" in runtime_js
    assert _script_cache_busted(html, "runtime_tab.js")


def test_runtime_tab_surfaces_rag_storage_diagnostics() -> None:
    html = _html()
    runtime_js = _runtime_tab_js()

    assert "RAG 저장소 진단" in runtime_js
    assert "ragChroma.diagnostics" in runtime_js
    assert "duplicate_embedding_ids" in runtime_js
    assert "document_metadata_bytes" in runtime_js
    assert "fulltext_document_bytes" in runtime_js
    assert "metadata_key_bytes" in runtime_js
    assert "embedding_metadata.string_value" in runtime_js
    assert "embedding_fulltext_search_content.c0" in runtime_js
    assert _script_cache_busted(html, "runtime_tab.js")


def test_runtime_tab_surfaces_database_growth_pressure() -> None:
    html = _html()
    runtime_js = _runtime_tab_js()

    assert "DB 성장 압력" in runtime_js
    assert "storage.database_growth_pressure" in runtime_js
    assert "storage.database_growth_pressure_count" in runtime_js
    assert "storage.database_growth_pressure_bytes" in runtime_js
    assert "storage.database_growth_pressure_archive_rows" in runtime_js
    assert "largest_tables" in runtime_js
    assert "row.action_label" in runtime_js
    assert "row.reclaimability" in runtime_js
    assert "row.archive_retention_status" in runtime_js
    assert "row.archive_retention_days" in runtime_js
    assert "DB growth pressure" in runtime_js
    assert "archive rows" in runtime_js
    assert _script_cache_busted(html, "runtime_tab.js")


def test_static_ui_renders_ops_advisory_details() -> None:
    js = _js()
    ops_js = _ui_ops_js()
    destructuring_start = js.index("const {\n  costEvidenceTone,")
    destructuring_end = js.index("} = UI_OPS;", destructuring_start)
    ops_destructuring = js[destructuring_start:destructuring_end]

    assert "function renderOpsAdvisoryDetails(" in ops_js
    assert "renderOpsAdvisoryDetails," in ops_js
    assert "renderOpsAdvisoryDetails," in ops_destructuring
    assert "renderOpsAdvisoryDetails(ops.advisory_details" in js
    assert "ops-advisory-detail" in ops_js
    assert "row.top_bottlenecks" in ops_js
    assert "병목 ${escapeHTML(bottleneckText)}" in ops_js


def test_static_ui_uses_compact_ops_readiness_endpoint() -> None:
    js = _js()

    assert 'getJSON("/ops/readiness?compact=true")' in js
    assert 'getJSON("/ops/readiness"),' not in js


def test_static_ui_auto_refreshes_stale_dashboard_cache_in_background() -> None:
    js = _js()
    auto_refresh_start = js.index("function scheduleStaleDashboardRefresh(options = {})")
    auto_refresh_end = js.index("async function refreshDashboard", auto_refresh_start)
    auto_refresh_js = js[auto_refresh_start:auto_refresh_end]

    assert "dashboardLiveRefreshInFlight: false" in js
    assert "function dashboardHasStaleVenues(payload)" in js
    assert "function scheduleStaleDashboardRefresh(options = {})" in js
    assert "dashboardHasStaleVenues(state.dashboard)" in js
    assert "scheduleStaleDashboardRefresh({ skipKisBlocks });" in js
    assert "forceRefresh: false" in auto_refresh_js
    assert "forceRefresh: true" not in auto_refresh_js
    assert "autoRefreshStale: false" in auto_refresh_js
    assert "Automatic stale-cache repair must not bypass backend cache/cooldown gates." in js


def test_static_ui_state_declares_evidence_policy_once() -> None:
    js = _js()

    assert js.count("evidencePolicy: {") == 1


def test_binance_growth_target_separates_account_and_jue_performance() -> None:
    binance_js = _binance_tab_js()

    assert "계좌 기준 현재 수익률" in binance_js
    assert "쥬 블록 실현손익" in binance_js
    assert "target.basis" in binance_js


def test_ops_advisory_details_render_top_bottlenecks() -> None:
    formatters_path = ROOT / "src/tradecraft/web/static/ui_formatters.js"
    ops_path = ROOT / "src/tradecraft/web/static/ui_ops.js"
    script = f"""
const fs = require("fs");
const window = {{
  HERMES_UI_SHARED: {{ opsSignalLabels: {{}} }},
}};
eval(fs.readFileSync({json.dumps(str(formatters_path))}, "utf8"));
eval(fs.readFileSync({json.dumps(str(ops_path))}, "utf8"));
const html = window.HERMES_UI_OPS.renderOpsAdvisoryDetails([
  {{
    venue: "binance",
    signal: "trading_validation_diagnostic_failures_binance",
    diagnostic_fail_count: 10,
    top_bottlenecks: [
      {{
        label: "거래비용 시뮬레이션",
        evidence: "총 gross PnL 대비 비용 드래그는 28.07%입니다.",
      }},
      {{
        label: "몬테카를로 시뮬레이션",
        evidence: "파산확률 71.80%입니다.",
      }},
    ],
  }},
]);
console.log(html);
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "병목 거래비용 시뮬레이션" in result.stdout
    assert "비용 드래그는 28.07%" in result.stdout
    assert "몬테카를로 시뮬레이션" in result.stdout


def test_static_ui_admin_auth_recognizes_dashboard_and_live_authority_paths() -> None:
    html = _html()
    js = _js()
    auth_js = _ui_auth_js()

    assert "/static/ui_auth.js" in html
    assert html.index("/static/ui_auth.js") < html.index("/static/app.js")
    assert "window.HERMES_UI_AUTH" in auth_js
    assert '"/dashboard",' in auth_js
    assert '"/live/",' in auth_js
    assert "const UI_AUTH = window.HERMES_UI_AUTH || {};" in js
    assert "function hasAdminToken()" in js
    assert 'if (!hasAdminToken()) {\n    markAuthRequired("운영 토큰을 입력하면 국장/블록/운영 데이터를 불러옵니다. 토큰은 브라우저 세션에만 저장됩니다.");\n  } else {' in js
    assert "국장 계좌 인증 대기" in js
    assert "브라우저 세션 토큰이 없으면 숨겨집니다" in js
    assert "재부팅, 새 브라우저, 세션 삭제 후에는 다시 입력해야 합니다" in js
    assert "서버/KIS 데이터 없음이 아니라 보호 API 인증 대기 상태입니다" in js
    assert "KIS 장애나 국장 데이터 공백이 아니라 보호 API 인증 대기입니다" in js
    assert "data-auth-focus" in js
    assert "function focusAuthTokenInput()" in js


def test_static_ui_auth_banner_explains_kis_data_hidden_by_session_auth() -> None:
    html = _html()

    assert "국장/KIS 계좌와 블록 정보는 브라우저 세션 운영 토큰이 있어야 표시됩니다" in html
    assert "KIS 데이터가 비어 있는 상태와 인증 대기를 구분하세요" in html


def test_static_ui_clears_auth_required_health_after_successful_token() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function setHealth");
const end = source.indexOf("function renderOpsBanner", start);
if (start < 0 || end < 0) throw new Error("auth helpers source not found");
const elements = {{
  healthPill: {{ textContent: "", style: {{ color: "" }} }},
  authBanner: {{ hidden: false, innerHTML: "" }},
  authMessage: {{ textContent: "" }},
  authTokenInput: {{ value: "" }},
}};
const state = {{ auth: {{ required: true, message: "admin auth required", token: "secret" }}, dashboard: null }};
const UI_AUTH = {{ readAdminToken: () => "" }};
const document = {{ activeElement: null }};
const window = {{ sessionStorage: {{ getItem: () => "", setItem: () => {{}}, removeItem: () => {{}} }} }};
const qs = (id) => elements[id] || null;
const renderHelperAgent = () => {{}};
eval(source.slice(start, end));
setHealth("Auth required", false);
clearAuthRequired();
console.log(JSON.stringify({{
  healthText: elements.healthPill.textContent,
  healthColor: elements.healthPill.style.color,
  authRequired: state.auth.required,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload == {
        "healthText": "API online",
        "healthColor": "var(--status-ok)",
        "authRequired": False,
    }


def test_static_ui_auth_save_refreshes_active_helper_tab() -> None:
    js = _js()
    token_save_start = js.index('bindEvent("authTokenSaveBtn", "click"')
    token_save_end = js.index('bindEvent("authTokenClearBtn"', token_save_start)
    token_save_handler = js[token_save_start:token_save_end]

    assert "await refreshDashboard();" in token_save_handler
    assert 'if (state.activePage === "helper")' in token_save_handler
    assert "ensureHelperTabData(state.activeHelperTab);" in token_save_handler


def test_static_ui_kis_helper_page_keeps_kis_quick_strip_visible() -> None:
    html = _html()
    js = _js()

    assert 'id="helperKisQuickStrip"' in html
    assert 'class="kis-quick-strip helper-kis-strip"' in html
    assert '"helperKisQuickStrip"' in js
    assert '["kisQuickStrip", "helperKisQuickStrip"]' in js
    assert 'state.activeHelperTab === "kis_trader"' in js
    assert 'state.activeHelperTab === "kis_memory"' in js
    assert 'target.hidden = id === "helperKisQuickStrip" && !showHelperKisStrip' in js


def test_kis_quick_strip_preserves_auth_required_message_on_both_surfaces() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function renderKisQuickStrip");
const end = source.indexOf("function renderAccountCashLine", start);
if (start < 0 || end < 0) throw new Error("renderKisQuickStrip source not found");
const slots = {{
  kisQuickStrip: {{ innerHTML: "" }},
  helperKisQuickStrip: {{ innerHTML: "" }},
}};
const qs = (id) => slots[id] || null;
const state = {{
  activePage: "helper",
  activeHelperTab: "kis_trader",
  auth: {{
    required: true,
    message: "운영 토큰을 입력하면 국장/블록/운영 데이터를 불러옵니다.",
  }},
  dashboard: null,
  kisBlockStatus: null,
}};
const hasAdminToken = () => false;
const kisQuickVenuesForDisplay = () => [];
const escapeHTML = (value) => String(value);
eval(source.slice(start, end));
renderKisQuickStrip();
console.log(JSON.stringify({{
  main: slots.kisQuickStrip.innerHTML,
  helper: slots.helperKisQuickStrip.innerHTML,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    for html in payload.values():
        assert "국장 계좌 인증 대기" in html
        assert "KIS 장애나 국장 데이터 공백이 아니라 보호 API 인증 대기입니다" in html
        assert "재부팅, 새 브라우저, 세션 삭제 후에는 다시 입력해야 합니다" in html
        assert "운영 토큰 입력" in html
        assert "KIS 연결 상태" not in html


def test_kis_quick_strip_auth_button_focuses_admin_token_input() -> None:
    js = _js()
    start = js.index('["kisQuickStrip", "helperKisQuickStrip"].forEach')
    end = js.index("const helperTabs", start)
    handler = js[start:end]

    assert 'target.closest("[data-auth-focus]")' in handler
    assert "focusAuthTokenInput();" in handler
    assert 'target.closest("[data-venue]")' in handler


def test_kis_quick_strip_falls_back_to_block_account_when_dashboard_venue_missing() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    source = js_path.read_text()
    assert "function kisQuickVenuesForDisplay(" in source
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function kisAccountNumber");
const end = source.indexOf("function renderKisQuickStrip", start);
if (start < 0 || end < 0) throw new Error("kisQuickVenuesForDisplay source not found");
const orderedVenuesForDisplay = (venues) => venues;
const fmtNum = (value) => String(value);
eval(source.slice(start, end));
const venues = kisQuickVenuesForDisplay([], {{
  account: {{
    status: "ok",
    account_label: "국장1",
    cash_krw: 4010886,
    orderable_cash_krw: 4010886,
    position_value_krw: 417112,
    total_value_krw: 4427998,
    position_count: 4,
  }},
}});
console.log(JSON.stringify(venues));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    venues = json.loads(result.stdout)

    assert venues == [
        {
            "id": "kr_stock",
            "label": "국장1",
            "market": "KRX",
            "assets": [
                {
                    "asset": "KRW",
                    "asset_name": "KRW",
                    "kind": "cash",
                    "qty": 4010886,
                    "available": 4010886,
                    "locked": 0,
                    "avg_price": 1,
                    "mark_price": 1,
                    "value_krw": 4010886,
                    "pnl_krw": 0,
                }
            ],
            "cash_krw": 4010886,
            "invested_krw": 417112,
            "unrealized_pnl_krw": 0,
            "total_krw": 4427998,
            "computed_total_krw": 4427998,
            "broker_total_krw": 4427998,
            "total_value_basis": "broker_net_asset",
            "cache_status": "kis_blocks_account_fallback",
            "position_count": 4,
        }
    ]


def test_dashboard_venue_display_defaults_name_korean_accounts_when_api_label_missing() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function venueDisplayDefaults");
const end = source.indexOf("function deriveAllVenue", start);
if (start < 0 || end < 0) throw new Error("orderedVenuesForDisplay source not found");
eval(source.slice(start, end));
const venues = orderedVenuesForDisplay([
  {{ id: "binance_futures", total_krw: 30 }},
  {{ id: "kr_stock_2", label: "국장(2번)", total_krw: 20 }},
  {{ id: "kr_stock", label: "국장", total_krw: 10 }},
]);
console.log(JSON.stringify(venues.map((item) => ({{
  id: item.id,
  label: item.label,
  market: item.market,
}}))));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(result.stdout)

    assert rows == [
        {"id": "kr_stock", "label": "국장1", "market": "KRX"},
        {"id": "kr_stock_2", "label": "국장2", "market": "KRX"},
        {"id": "binance_futures", "label": "Binance Futures", "market": "USDT-M"},
    ]


def test_kis_block_account_snapshot_updates_dashboard_venues() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function venueDisplayDefaults");
const end = source.indexOf("function renderKisQuickStrip", start);
if (start < 0 || end < 0) throw new Error("dashboard venue helpers not found");
const state = {{
  dashboard: {{
    venues: [],
    clock_utc: "2026-07-01T00:00:00Z",
  }},
}};
eval(source.slice(start, end));
const changed = syncDashboardKisVenueFromBlockStatus({{
  account: {{
    status: "ok",
    account_label: "국장1",
    cash_krw: 4010886,
    orderable_cash_krw: 4010886,
    position_value_krw: 421565,
    total_value_krw: 4432451,
    position_count: 1,
    positions: [{{
      symbol: "360750",
      name: "TIGER 미국S&P500",
      qty: 4,
      market_value_krw: 114560,
      unrealized_pnl_krw: 1200,
    }}],
  }},
}});
console.log(JSON.stringify({{ changed, venues: state.dashboard.venues }}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["changed"] is True
    assert payload["venues"][0]["id"] == "kr_stock"
    assert payload["venues"][0]["total_krw"] == 4432451
    assert payload["venues"][0]["cash_krw"] == 4010886
    assert payload["venues"][0]["invested_krw"] == 421565
    assert payload["venues"][0]["position_count"] == 1
    assert payload["venues"][0]["assets"][1]["asset_name"] == "TIGER 미국S&P500"


def test_kis_block_tab_paints_core_payload_before_auxiliary_loads() -> None:
    html = _html()
    js = _js()
    load_start = js.index("async function loadKisBlocks")
    load_end = js.index("function mergeKisBlockRows", load_start)
    load_body = js[load_start:load_end]

    assert "kisBlockLoading: false" in js
    assert 'tab === "kis_trader" && !state.kisBlockStatus && !state.kisBlockLoading' in js
    assert 'loadKisBlocks();\n    return;' in js
    assert "state.kisBlockLoading = true;" in load_body
    assert 'const path = activeOnly\n      ? "/kis/blocks?compact=true&active_only=true"\n      : "/kis/blocks?compact=true";' in load_body
    assert "const payload = await getJSON(path);" in load_body
    assert "state.kisBlockStatus = activeOnly" in load_body
    assert "? mergeKisBlockStatus(payload)" in load_body
    assert ": normalizeKisBlockPayload(payload);" in load_body
    assert "renderHelperAgent();\n    }\n    const shouldLoadOpsReadiness" in load_body
    assert "await Promise.allSettled(auxiliaryLoads);" in load_body
    assert "state.kisBlockLoading = false;" in load_body
    assert "const etfPromise =" not in load_body
    assert "const skipKisBlocks = Boolean(options.skipKisBlocks);" in js
    assert 'const useKisActiveOnly = !(state.activePage === "helper" && state.activeHelperTab === "kis_trader");' in js
    assert 'const kisBlocksPath = useKisActiveOnly\n    ? "/kis/blocks?compact=true&active_only=true"\n    : "/kis/blocks?compact=true";' in js
    assert "skipKisBlocks ? Promise.resolve(null) : getJSON(kisBlocksPath)" in js
    assert "useKisActiveOnly\n      ? mergeKisBlockStatus(kisBlockResult.value)\n      : normalizeKisBlockPayload(kisBlockResult.value);" in js
    assert 'state.activeHelperTab === "kis_trader"' in js
    assert 'const prioritizeVisibleHelperTab = hasAdminToken() && state.activePage === "helper";' in js
    assert 'const shouldPreloadVisibleHelperTab = prioritizeVisibleHelperTab && !isMemoryTab(state.activeHelperTab);' in js
    assert "prioritizeKisBlocks\n    ? true\n    : false" not in js
    assert "} else if (shouldPreloadVisibleHelperTab) {\n    ensureHelperTabData();\n  }" in js
    assert "const kisQuickPreload = hasAdminToken() && !prioritizeKisBlocks" in js
    assert "await refreshDashboard({ skipKisBlocks: prioritizeKisBlocks || Boolean(kisQuickPreload) });" in js
    assert "20260710_operator_shell_v3" in html


def test_helper_tab_data_does_not_call_protected_loaders_without_admin_token() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function ensureHelperTabData");
const end = source.indexOf("function renderHelperAskResult", start);
if (start < 0 || end < 0) throw new Error("ensureHelperTabData source not found");
const calls = [];
const state = {{
  activeHelperTab: "kis_trader",
  kisBlockStatus: null,
  kisBlockLoading: false,
  etfResearch: {{ status: null, candidates: null, loading: false }},
  dailyDiscovery: null,
  dailyDiscoveryLoading: false,
  strategyIntel: {{ result: null, loading: false }},
  marketJudge: {{ result: null, loading: false }},
  marketPulse: {{ result: null, loading: false }},
  rebalanceStatus: null,
  rebalanceError: "",
  reportsStatus: null,
  reportsLoading: false,
  runtimeStorage: null,
  runtimeStorageError: "",
  runtimeStorageLoading: false,
  binanceTrader: {{ status: null, loading: false, quantSignals: [], patternContext: null }},
  cryptoResearch: {{ context: null, loading: false }},
  cryptoAlpha: {{ context: null, loading: false }},
  evidencePolicy: {{ status: null, context: null, loading: false }},
  investmentMemory: null,
  investmentMemoryScope: "",
  investmentMemoryLoading: false,
  memoryReviews: null,
  memoryReviewError: "",
  jueWikiStatus: null,
  jueWikiLoading: false,
  settingsPage: {{
    catalog: null,
    loading: false,
    jueWorkflowStatus: null,
    jueWorkflowLoading: false,
    codexNativeStatus: null,
    codexNativeLoading: false,
  }},
}};
const hasAdminToken = () => false;
const isMemoryTab = () => false;
const memoryScopeForTab = () => "kis";
const loadKisBlocks = () => calls.push("loadKisBlocks");
const loadEtfResearch = () => calls.push("loadEtfResearch");
const loadDailyDiscovery = () => calls.push("loadDailyDiscovery");
const loadStrategyIntel = () => calls.push("loadStrategyIntel");
const loadMarketJudge = () => calls.push("loadMarketJudge");
const loadMarketPulse = () => calls.push("loadMarketPulse");
const loadRebalanceStatus = () => calls.push("loadRebalanceStatus");
const loadReportsStatus = () => calls.push("loadReportsStatus");
const loadRuntimeStorage = () => calls.push("loadRuntimeStorage");
const loadBinanceBlocks = () => calls.push("loadBinanceBlocks");
const loadCryptoResearch = () => calls.push("loadCryptoResearch");
const loadCryptoAlpha = () => calls.push("loadCryptoAlpha");
const loadEvidencePolicy = () => calls.push("loadEvidencePolicy");
const loadInvestmentMemory = () => calls.push("loadInvestmentMemory");
const loadMemoryReviews = () => calls.push("loadMemoryReviews");
const loadJueWiki = () => calls.push("loadJueWiki");
const loadSettingsCatalog = () => calls.push("loadSettingsCatalog");
const loadJueWorkflowStatus = () => calls.push("loadJueWorkflowStatus");
const loadCodexNativeStatus = () => calls.push("loadCodexNativeStatus");
eval(source.slice(start, end));
ensureHelperTabData("kis_trader");
console.log(JSON.stringify(calls));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == []


def test_rebalance_status_loads_only_when_rebalance_tab_is_visible() -> None:
    js = _js()
    refresh_start = js.index("async function refreshDashboard")
    refresh_end = js.index("async function loadTelegramStatus", refresh_start)
    refresh_body = js[refresh_start:refresh_end]
    ensure_start = js.index("function ensureHelperTabData")
    ensure_end = js.index("function renderHelperAskResult", ensure_start)
    ensure_body = js[ensure_start:ensure_end]

    assert 'getJSON("/rebalance/kis-status")' not in refresh_body
    assert "async function loadRebalanceStatus()" in js
    assert 'state.rebalanceStatus = await getJSON("/rebalance/kis-status");' in js
    assert 'tab === "rebalance" && !state.rebalanceStatus && !state.rebalanceError' in ensure_body
    assert "loadRebalanceStatus();" in ensure_body


def test_dashboard_refresh_uses_compact_reports_status_for_first_paint() -> None:
    js = _js()
    refresh_start = js.index("async function refreshDashboard")
    refresh_end = js.index("async function loadTelegramStatus", refresh_start)
    refresh_body = js[refresh_start:refresh_end]

    assert 'getJSON("/reports/status?compact=true")' in refresh_body
    assert 'getJSON("/reports/status"),' not in refresh_body


def test_research_strip_uses_compact_and_dashboard_research_fallbacks() -> None:
    js = _js()
    start = js.index("function renderResearchKnowledgeStrip")
    end = js.index("function renderPageMode", start)
    body = js[start:end]

    assert "reports.report_count" in body
    assert "research?.report_count" in body
    assert "reports.symbol_count" in body
    assert "research?.symbol_count" in body
    assert "reports.rag_count" in body
    assert "research?.rag_count" in body
    assert "reports.rag_available" in body
    assert "research?.rag_available" in body
    assert "reports.fundamentals_symbol_count" in body
    assert "research?.fundamentals_symbol_count" in body


def test_reports_tab_promotes_compact_status_to_full_status() -> None:
    js = _js()
    ensure_start = js.index("function ensureHelperTabData")
    ensure_end = js.index("function stringifySafe", ensure_start)
    ensure_body = js[ensure_start:ensure_end]

    assert "async function loadReportsStatus(" in js
    assert 'const path = compact ? "/reports/status?compact=true" : "/reports/status";' in js
    assert 'tab === "reports"' in ensure_body
    assert "state.reportsStatus.compact" in ensure_body
    assert "loadReportsStatus({ compact: false });" in ensure_body


def test_kis_block_ops_readiness_filters_cross_venue_validation_noise() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("const OPS_VALIDATION_VENUES");
const end = source.indexOf("function renderBlockOpsReadiness", start);
if (start < 0 || end < 0) throw new Error("ops readiness venue filters not found");
eval(source.slice(start, end));
const readiness = {{
  warnings: ["restart_required"],
  blockers: [],
  advisories: [
    "trading_validation_probe_kis",
    "trading_validation_diagnostic_failures_binance",
  ],
  advisory_details: [
    {{ venue: "kis", signal: "trading_validation_probe_kis", fail_count: 0 }},
    {{ venue: "binance", signal: "trading_validation_diagnostic_failures_binance", fail_count: 10 }},
  ],
  trading_validation: {{
    status: "ok",
    readiness: "probe",
    diagnostic_status: "risk_repair",
    summary: {{ fail_count: 10, warn_count: 18, missing_count: 0 }},
    discipline_count: 38,
    expected_discipline_count: 38,
    venues: {{
      kis: {{
        status: "ok",
        readiness: "probe",
        diagnostic_status: "watch",
        score: 65.79,
        discipline_count: 19,
        expected_discipline_count: 19,
        summary: {{ fail_count: 0, warn_count: 13, missing_count: 0, readiness: "probe", diagnostic_status: "watch" }},
      }},
      binance: {{
        status: "ok",
        readiness: "probe",
        diagnostic_status: "risk_repair",
        score: 34.21,
        discipline_count: 19,
        expected_discipline_count: 19,
        summary: {{ fail_count: 10, warn_count: 5, missing_count: 0, readiness: "probe", diagnostic_status: "risk_repair" }},
      }},
    }},
    bottlenecks: [
      {{ venue: "kis", id: "sharpe_ratio", status: "warn" }},
      {{ venue: "binance", id: "profit_factor", status: "fail" }},
    ],
    primary_next_actions: [
      {{ venue: "kis", action: "KIS sample build" }},
      {{ venue: "binance", action: "Binance repair" }},
    ],
  }},
}};
console.log(JSON.stringify(opsReadinessForVenue(readiness, "kis")));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["advisories"] == ["trading_validation_probe_kis"]
    assert payload["advisory_details"] == [
        {"venue": "kis", "signal": "trading_validation_probe_kis", "fail_count": 0}
    ]
    assert payload["trading_validation"]["summary"]["fail_count"] == 0
    assert list(payload["trading_validation"]["venues"]) == ["kis"]
    assert payload["trading_validation"]["bottlenecks"] == [
        {"venue": "kis", "id": "sharpe_ratio", "status": "warn"}
    ]
    assert payload["trading_validation"]["primary_next_actions"] == [
        {"venue": "kis", "action": "KIS sample build"}
    ]
    assert "trading_validation_diagnostic_failures_binance" not in json.dumps(payload)


def test_kis_quick_strip_prefers_dashboard_kis_venues() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const orderedStart = source.indexOf("function venueDisplayDefaults");
const helperStart = source.indexOf("function kisQuickVenuesForDisplay");
const helperEnd = source.indexOf("function renderKisQuickStrip", helperStart);
if (orderedStart < 0 || helperStart < 0 || helperEnd < 0) {{
  throw new Error("KIS quick venue helpers not found");
}}
eval(source.slice(orderedStart, helperEnd));
const venues = kisQuickVenuesForDisplay([
  {{ id: "binance", label: "바이낸스", total_krw: 100, assets: [] }},
  {{
    id: "kr_stock_2",
    label: "국장(2번)",
    total_krw: 14398076,
    cash_krw: 32076,
    invested_krw: 14366000,
    unrealized_pnl_krw: 0,
    assets: [{{ kind: "position", asset_name: "삼성전자", qty: 1 }}],
  }},
  {{
    id: "kr_stock",
    label: "국장",
    total_krw: 4428416,
    cash_krw: 4010886,
    invested_krw: 417530,
    unrealized_pnl_krw: 0,
    assets: [{{ kind: "position", asset_name: "피노", qty: 1 }}],
  }},
], {{
  account: {{
    status: "ok",
    total_value_krw: 1,
  }},
}});
console.log(JSON.stringify(venues));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    venues = json.loads(result.stdout)

    assert [row["id"] for row in venues] == ["kr_stock", "kr_stock_2"]
    assert venues[0]["label"] == "국장1"
    assert venues[0]["total_krw"] == 4428416
    assert venues[1]["label"] == "국장2"


def test_kis_block_tab_renders_account_holdings_even_without_active_blocks() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function venueDisplayDefaults");
const panelStart = source.indexOf("function renderKisAccountHoldingsPanel");
const end = source.indexOf("function renderAccountCashLine", panelStart);
if (start < 0 || panelStart < 0 || end < 0) {{
  throw new Error("KIS account holdings panel helpers not found");
}}
const state = {{
  dashboard: {{
    venues: [
      {{
        id: "kr_stock",
        label: "국장",
        cash_krw: 4010886,
        invested_krw: 417530,
        total_krw: 4428416,
        unrealized_pnl_krw: 2064,
        assets: [
          {{ kind: "cash", asset_name: "KRW", qty: 4010886, value_krw: 4010886 }},
          {{ kind: "position", asset: "360750", asset_name: "TIGER 미국S&P500", qty: 4, value_krw: 113300, pnl_krw: 446 }},
          {{ kind: "position", asset: "379800", asset_name: "KODEX 미국S&P500", qty: 4, value_krw: 103280, pnl_krw: 840 }},
        ],
      }},
    ],
  }},
}};
const escapeHTML = (value) => String(value ?? "");
const fmtKRW = (value) => String(Math.round(Number(value || 0))).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ",");
const fmtNum = (value) => String(Number(value || 0));
eval(source.slice(start, end));
const html = renderKisAccountHoldingsPanel({{
  account: {{
    status: "ok",
    account_label: "국장1",
    cash_krw: 4010886,
    position_value_krw: 417530,
    total_value_krw: 4428416,
    position_count: 4,
  }},
  active_blocks: [],
  block_history: [],
}});
console.log(JSON.stringify({{ html }}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    html = json.loads(result.stdout)["html"]

    assert "국장 계좌/보유 종목" in html
    assert "총 4,428,416원" in html
    assert "TIGER 미국S&amp;P500" in html or "TIGER 미국S&P500" in html
    assert "KODEX 미국S&amp;P500" in html or "KODEX 미국S&P500" in html
    assert "활성 블록이 없어도 계좌 보유분은 계속 표시됩니다" in html


def test_static_ui_normalizes_compact_kis_blocks_for_history_views() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    source = js_path.read_text()
    assert "function normalizeKisBlockPayload(" in source
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function mergeKisBlockRows");
const end = source.indexOf("function mergeBinanceStatus", start);
if (start < 0 || end < 0) throw new Error("normalizeKisBlockPayload source not found");
eval(source.slice(start, end));
const payload = normalizeKisBlockPayload({{
  compact: true,
  active_blocks: [{{ block_id: "open1", status: "open" }}],
  block_history: [{{ block_id: "closed1", status: "closed" }}],
}});
console.log(JSON.stringify(payload));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["blocks"] == [
        {"block_id": "open1", "status": "open"},
        {"block_id": "closed1", "status": "closed"},
    ]


def test_static_ui_kis_auto_refresh_uses_active_only_and_preserves_history() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    source = js_path.read_text()
    assert "activeOnly: true" in source
    assert '"/kis/blocks?compact=true&active_only=true"' in source
    assert "function mergeKisBlockStatus(" in source
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function mergeKisBlockRows");
const end = source.indexOf("function mergeBinanceStatus", start);
if (start < 0 || end < 0) throw new Error("mergeKisBlockStatus source not found");
const state = {{
  kisBlockStatus: {{
    active_blocks: [{{ block_id: "open1", status: "open", memory_links: ["keep"] }}],
    block_history: [{{ block_id: "closed1", status: "closed" }}],
    blocks: [
      {{ block_id: "open1", status: "open", memory_links: ["keep"] }},
      {{ block_id: "closed1", status: "closed" }},
    ],
    orders: [{{ id: 1 }}],
    events: [{{ id: 2 }}],
  }},
}};
eval(source.slice(start, end));
const merged = mergeKisBlockStatus({{
  compact: true,
  active_only: true,
  active_blocks: [{{ block_id: "open1", status: "open", current_price: 70000 }}],
}});
console.log(JSON.stringify(merged));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["active_blocks"][0]["current_price"] == 70000
    assert payload["active_blocks"][0]["memory_links"] == ["keep"]
    assert payload["block_history"] == [{"block_id": "closed1", "status": "closed"}]
    assert payload["blocks"] == [
        {
            "block_id": "open1",
            "status": "open",
            "memory_links": ["keep"],
            "current_price": 70000,
        },
        {"block_id": "closed1", "status": "closed"},
    ]
    assert payload["orders"] == [{"id": 1}]
    assert payload["events"] == [{"id": 2}]


def test_static_ui_loads_shared_label_registry_before_main_app() -> None:
    html = _html()

    assert "/static/ui_shared.js" in html
    assert html.index("/static/ui_shared.js") < html.index("/static/app.js")
    shared_js = _ui_shared_js()
    js = _js()
    assert "window.HERMES_UI_SHARED" in shared_js
    assert "opsSignalLabels" in shared_js
    assert "validationGateLabels" in shared_js
    assert (
        "binance_block_manager_last_run_failed: "
        '"Binance 쥬 최근 판단 실패"' in shared_js
    )
    assert "const OPS_SIGNAL_LABELS =" not in js
    assert "const VALIDATION_GATE_LABELS =" not in js
    assert "window.HERMES_UI_SHARED" in js


def test_static_ui_loads_formatter_helpers_before_main_app() -> None:
    html = _html()

    assert "/static/ui_formatters.js" in html
    assert html.index("/static/ui_formatters.js") < html.index("/static/app.js")
    formatters_js = _ui_formatters_js()
    js = _js()
    assert "window.HERMES_UI_FORMATTERS" in formatters_js
    for marker in (
        "escapeHTML(value)",
        "fmtKRW(value)",
        "fmtNum(value",
        "fmtKST(isoString",
        "fmtBytes(value)",
        "truncateWithEllipsis(value",
    ):
        assert marker in formatters_js
    for marker in (
        "function escapeHTML(",
        "function fmtKRW(",
        "function fmtNum(",
        "function fmtKST(",
        "function fmtBytes(",
        "function truncateWithEllipsis(",
    ):
        assert marker not in js
    assert "window.HERMES_UI_FORMATTERS" in js


def test_static_ui_loads_ops_helpers_before_main_app() -> None:
    html = _html()

    assert "/static/ui_ops.js" in html
    assert html.index("/static/ui_ops.js") < html.index("/static/app.js")
    ops_js = _ui_ops_js()
    js = _js()
    assert "window.HERMES_UI_OPS" in ops_js
    for marker in (
        "formatOpsSignalLabel(value)",
        "renderOpsRemediationActions(actions",
        "renderTradingValidationBottleneckSummary(tradingValidation",
        "formatValidationGateReason(value)",
        "tradingValidationTone(status)",
    ):
        assert marker in ops_js
    for marker in (
        "function formatOpsSignalLabel(",
        "function renderOpsRemediationActions(",
        "function renderTradingValidationBottleneckSummary(",
        "function formatValidationGateReason(",
        "function tradingValidationTone(",
    ):
        assert marker not in js
    assert "window.HERMES_UI_OPS" in js


def test_static_ui_loads_live_authority_panel_before_main_app() -> None:
    html = _html()

    assert "/static/ui_live_authority.js" in html
    assert html.index("/static/ui_live_authority.js") < html.index("/static/app.js")
    live_js = _ui_live_authority_js()
    js = _js()
    assert "window.HERMES_UI_LIVE_AUTHORITY" in live_js
    assert "function renderLiveAuthorityPanel(venue" in live_js
    assert "validationGate.discipline_matrix" in live_js
    assert "repair_execution" in live_js
    assert "function renderLiveAuthorityPanel(" not in js
    assert "window.HERMES_UI_LIVE_AUTHORITY" in js


def test_static_ui_loads_binance_tab_config_before_main_app() -> None:
    html = _html()

    assert "/static/binance_tab.js" in html
    assert html.index("/static/binance_tab.js") < html.index("/static/app.js")
    binance_tab_js = _binance_tab_js()
    js = _js()
    assert "window.HERMES_BINANCE_TAB" in binance_tab_js
    assert "lanes" in binance_tab_js
    assert "historyStatuses" in binance_tab_js
    assert "volatile_attack" in binance_tab_js
    assert "window.HERMES_BINANCE_TAB" in js


def test_binance_tab_owns_block_card_and_history_renderers() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()

    for marker in (
        "renderBlockCard(block",
        "renderBlockHistory(payload",
        "binance-block-card",
        "binance-history-panel",
        "historyPerformance(block",
    ):
        assert marker in binance_tab_js
    assert "BINANCE_TAB.renderBlockCard(block" in js
    assert "BINANCE_TAB.renderBlockHistory(payload" in js


def test_static_ui_loads_settings_tab_config_before_main_app() -> None:
    html = _html()

    assert "/static/settings_tab.js" in html
    assert html.index("/static/settings_tab.js") < html.index("/static/app.js")
    assert "20260630_settings_lazy_all_v1" in html
    settings_tab_js = _settings_tab_js()
    js = _js()
    assert "window.HERMES_SETTINGS_TAB" in settings_tab_js
    assert "riskLabels" in settings_tab_js
    assert "riskClasses" in settings_tab_js
    assert "window.HERMES_SETTINGS_TAB" in js


def test_static_ui_loads_crypto_research_tab_config_before_main_app() -> None:
    html = _html()

    assert "/static/crypto_research_tab.js" in html
    assert html.index("/static/crypto_research_tab.js") < html.index("/static/app.js")
    crypto_research_tab_js = _crypto_research_tab_js()
    js = _js()
    assert "window.HERMES_CRYPTO_RESEARCH_TAB" in crypto_research_tab_js
    assert "notesMap(context)" in crypto_research_tab_js
    assert "featuresMap(context)" in crypto_research_tab_js
    assert "window.HERMES_CRYPTO_RESEARCH_TAB" in js


def test_static_ui_loads_strategy_intel_tab_before_main_app() -> None:
    html = _html()

    assert "/static/strategy_intel_tab.js" in html
    assert html.index("/static/strategy_intel_tab.js") < html.index("/static/app.js")
    strategy_tab_js = _strategy_intel_tab_js()
    js = _js()
    assert "window.HERMES_STRATEGY_INTEL_TAB" in strategy_tab_js
    assert "renderSuitabilityBars(row" in strategy_tab_js
    assert "renderDataHealth(result" in strategy_tab_js
    assert "window.HERMES_STRATEGY_INTEL_TAB" in js


def test_strategy_intel_tab_owns_suitability_and_data_warning_renderers() -> None:
    js = _js()
    strategy_tab_js = _strategy_intel_tab_js()

    for marker in (
        "strategySuitability(row)",
        "strategyHorizonPayload(row, key)",
        "renderSuitabilityBars(row",
        "renderSuitabilityDetail(row",
        "dataWarnings(row)",
        "renderDataWarnings(row",
        "renderDataHealth(result",
    ):
        assert marker in strategy_tab_js
    assert "function strategySuitability(" not in js
    assert "function renderStrategySuitabilityBars(" not in js
    assert "function renderStrategySuitabilityDetail(" not in js
    assert "function strategyDataWarnings(" not in js
    assert "function renderStrategyDataWarnings(" not in js
    assert "function renderStrategyDataHealth(" not in js
    assert "STRATEGY_INTEL_TAB.renderSuitabilityBars(row" in js
    assert "STRATEGY_INTEL_TAB.renderSuitabilityDetail(row" in js
    assert "STRATEGY_INTEL_TAB.renderDataWarnings(row" in js
    assert "STRATEGY_INTEL_TAB.renderDataHealth(result" in js
    assert "STRATEGY_INTEL_TAB.dataWarnings(row)" in js


def test_strategy_intel_tab_owns_collect_source_and_score_renderers() -> None:
    js = _js()
    strategy_tab_js = _strategy_intel_tab_js()

    for marker in (
        "renderCollectResult(result",
        "renderFundamentalsCollectResult(result",
        "renderSources(sources",
        "renderScoreComponents(row",
    ):
        assert marker in strategy_tab_js
    assert "function renderStrategyCollectResult(" not in js
    assert "function renderStrategyFundamentalsCollectResult(" not in js
    assert "function renderStrategyIntelSources(" not in js
    assert "function renderStrategyScoreComponents(" not in js
    assert "STRATEGY_INTEL_TAB.renderCollectResult(" in js
    assert "STRATEGY_INTEL_TAB.renderFundamentalsCollectResult(" in js
    assert "STRATEGY_INTEL_TAB.renderSources(result.sources" in js
    assert "STRATEGY_INTEL_TAB.renderScoreComponents(row" in js


def test_static_ui_loads_market_judge_tab_before_main_app() -> None:
    html = _html()

    assert "/static/market_judge_tab.js" in html
    assert html.index("/static/market_judge_tab.js") < html.index("/static/app.js")
    market_judge_tab_js = _market_judge_tab_js()
    js = _js()
    assert "window.HERMES_MARKET_JUDGE_TAB" in market_judge_tab_js
    assert "renderTab(viewState" in market_judge_tab_js
    assert "renderMarketPulseSummary(viewState" in market_judge_tab_js
    assert "window.HERMES_MARKET_JUDGE_TAB" in js


def test_market_judge_tab_owns_market_pulse_and_judgment_renderers() -> None:
    js = _js()
    market_judge_tab_js = _market_judge_tab_js()

    for marker in (
        "marketActionLabel(value)",
        "renderMarketPulseSummary(viewState",
        "renderMarketJudgeHeader(payload",
        "renderMarketJudgmentCard(row",
        "renderTab(viewState",
    ):
        assert marker in market_judge_tab_js
    assert "function renderMarketPulseSummary(" not in js
    assert "function renderMarketJudgeHeader(" not in js
    assert "function renderMarketJudgmentCard(" not in js
    assert "function renderMarketJudgeTab(" not in js
    assert "MARKET_JUDGE_TAB.renderTab(" in js


def test_static_ui_loads_runtime_tab_before_main_app() -> None:
    html = _html()

    assert "/static/runtime_tab.js" in html
    assert html.index("/static/runtime_tab.js") < html.index("/static/app.js")
    runtime_tab_js = _runtime_tab_js()
    js = _js()
    assert "window.HERMES_RUNTIME_TAB" in runtime_tab_js
    assert "renderLLMUsagePanel(viewState" in runtime_tab_js
    assert "renderTab(viewState" in runtime_tab_js
    assert "window.HERMES_RUNTIME_TAB" in js


def test_runtime_helper_state_chip_does_not_warn_for_counts_or_active_states() -> None:
    js_path = ROOT / "src/tradecraft/web/static/app.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(js_path))}, "utf8");
const start = source.indexOf("function helperStateChip");
const end = source.indexOf("function boolFromStatus", start);
if (start < 0 || end < 0) throw new Error("helperStateChip source not found");
eval(source.slice(start, end));
const values = ["5095", "50000", "available", "active · sdk", "세션 상태/heartbeat"];
console.log(JSON.stringify(Object.fromEntries(values.map((value) => [value, helperStateChip(value).cls]))));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    classes = json.loads(result.stdout)

    assert classes["5095"] == "neutral"
    assert classes["50000"] == "neutral"
    assert classes["available"] == "ok"
    assert classes["active · sdk"] == "ok"
    assert classes["세션 상태/heartbeat"] == "neutral"


def test_runtime_tab_uses_ops_readiness_for_private_runner_statuses() -> None:
    js = _js()
    runtime_tab_js = _runtime_tab_js()

    assert "auth: state.auth" in js
    assert "hasAdminToken: hasAdminToken()" in js
    assert "opsReadiness: state.opsReadiness" in js
    assert "const ops = viewState?.opsReadiness || {};" in runtime_tab_js
    assert "const runnerProcesses = ops.processes || health?.runner_processes || {};" in runtime_tab_js
    assert "const healthHas = (key) => Boolean(health && Object.prototype.hasOwnProperty.call(health, key));" in runtime_tab_js
    assert 'healthBool("kis_block_trader_runner_alive")' in runtime_tab_js
    assert 'label: "Runtime 주문 권한"' in runtime_tab_js
    assert 'label: "Runtime 실행"' not in runtime_tab_js
    assert "health?.status === \"ok\"" not in runtime_tab_js
    assert 'optionalRunnerRow("research runner (optional)", "research"' in runtime_tab_js
    assert "legacy research runner" not in runtime_tab_js
    assert 'optionalRunnerRow("reports crawler", "naver_reports")' in runtime_tab_js
    assert 'optionalRunnerRow("strategy insight runner", "strategy_insights")' in runtime_tab_js
    assert 'optionalRunnerRow("crypto research runner", "crypto_market_research")' in runtime_tab_js
    assert '{ label: "research runner"' not in runtime_tab_js


def test_runtime_tab_distinguishes_kis_auth_wait_from_missing_data() -> None:
    runtime_js_path = ROOT / "src/tradecraft/web/static/runtime_tab.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(runtime_js_path))}, "utf8");
const window = {{}};
eval(source);
const html = window.HERMES_RUNTIME_TAB.renderTab({{
  auth: {{
    required: true,
    message: "admin auth required",
  }},
  hasAdminToken: false,
  dashboard: {{}},
  kisBlockStatus: {{}},
}}, {{
  escapeHTML: (value) => String(value ?? ""),
  fmtKRW: (value) => String(Math.round(Number(value || 0))),
  fmtKST: (value) => String(value || "--"),
  fmtBytes: (value) => String(value || 0),
  helperStateChip: (value) => ({{ text: String(value ?? "-"), cls: "neutral" }}),
  normalizeNonNegativeInt: (value) => Number.isFinite(Number(value)) ? Number(value) : null,
}});
console.log(html);
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    html = result.stdout

    assert "KIS 국장 계좌 인증 대기" in html
    assert "국장 계좌 데이터 공백이 아니라 보호 API 인증 대기" in html
    assert "data-auth-focus" in html
    assert "국장 계좌 데이터가 아직 대시보드/KIS 블록 payload에 없습니다" not in html
    assert "admin auth required" in html


def test_runtime_tab_shows_fundamentals_freshness_breakdown() -> None:
    runtime_tab_js = _runtime_tab_js()

    assert 'label: "fundamentals freshness"' in runtime_tab_js
    assert "fundamentalsFreshSymbols" in runtime_tab_js
    assert "fundamentalsStaleRatio" in runtime_tab_js
    assert '"fundamentals ok/stale"' not in runtime_tab_js


def test_runtime_llm_usage_panel_supports_period_filters_and_component_labels() -> None:
    html = _html()
    js = _js()
    runtime_tab_js = _runtime_tab_js()

    assert 'data-llm-usage-period="today"' in runtime_tab_js
    assert 'data-llm-usage-period="7d"' in runtime_tab_js
    assert 'data-llm-usage-period="all"' in runtime_tab_js
    assert "row.label || row.component" in runtime_tab_js
    assert "row.description ||" in runtime_tab_js
    assert "llmUsagePeriod" in js
    assert "llmUsageSummaryPath()" in js
    assert "data-llm-usage-period" in js
    assert _script_cache_busted(html, "runtime_tab.js")


def test_runtime_tab_owns_operational_data_renderers() -> None:
    js = _js()
    runtime_tab_js = _runtime_tab_js()

    for marker in (
        "renderLLMUsagePanel(viewState",
        "renderTab(viewState",
        "runnerLabel(runnerProcesses",
    ):
        assert marker in runtime_tab_js
    assert "function renderLLMUsagePanel(" not in js
    assert "function renderRuntimeHelperTab(" not in js
    assert "RUNTIME_TAB.renderTab(" in js


def test_static_ui_loads_rebalance_tab_before_main_app() -> None:
    html = _html()

    assert "/static/rebalance_tab.js" in html
    assert html.index("/static/rebalance_tab.js") < html.index("/static/app.js")
    rebalance_tab_js = _rebalance_tab_js()
    js = _js()
    assert "window.HERMES_REBALANCE_TAB" in rebalance_tab_js
    assert "renderTab(payload" in rebalance_tab_js
    assert "formatSymbolLabel(row" in rebalance_tab_js
    assert "window.HERMES_REBALANCE_TAB" in js


def test_rebalance_tab_owns_rebalance_renderer() -> None:
    js = _js()
    rebalance_tab_js = _rebalance_tab_js()

    for marker in (
        "renderTab(payload",
        "formatSymbolLabel(row",
        "목표 비중 테이블",
        "현재 오픈 포지션 종목",
    ):
        assert marker in rebalance_tab_js
    assert "function renderRebalanceHelperTab(" not in js
    assert "REBALANCE_TAB.renderTab(" in js


def test_static_ui_loads_backtest_tab_before_main_app() -> None:
    html = _html()

    assert "/static/backtest_tab.js" in html
    assert html.index("/static/backtest_tab.js") < html.index("/static/app.js")
    backtest_tab_js = _backtest_tab_js()
    js = _js()
    assert "window.HERMES_BACKTEST_TAB" in backtest_tab_js
    assert "renderStatus(payload" in backtest_tab_js
    assert "buildStartPayload(readValue" in backtest_tab_js
    assert "window.HERMES_BACKTEST_TAB" in js


def test_backtest_tab_owns_status_curve_scenario_and_payload_renderers() -> None:
    js = _js()
    backtest_tab_js = _backtest_tab_js()

    for marker in (
        "selectedSessionIds(root",
        "renderCurve(curve",
        "renderStatus(payload",
        "renderScenarios(rows",
        "renderDataStatus(payload",
        "buildStartPayload(readValue",
    ):
        assert marker in backtest_tab_js
    assert "function selectedBacktestSessionIds(" not in js
    assert "function renderBacktestCurve(" not in js
    assert "function renderBacktestStatus(" not in js
    assert "function renderBacktestScenarios(" not in js
    assert "BACKTEST_TAB.renderStatus(" in js
    assert "BACKTEST_TAB.renderScenarios(" in js
    assert "BACKTEST_TAB.buildStartPayload(" in js


def test_static_ui_loads_kis_trader_tab_config_before_main_app() -> None:
    html = _html()

    assert "/static/kis_trader_tab.js" in html
    assert html.index("/static/kis_trader_tab.js") < html.index("/static/app.js")
    kis_trader_tab_js = _kis_trader_tab_js()
    js = _js()
    assert "window.HERMES_KIS_TRADER_TAB" in kis_trader_tab_js
    assert "historyDates(blocks" in kis_trader_tab_js
    assert "filteredHistoryBlocks(payload" in kis_trader_tab_js
    assert "daySummary(payload" in kis_trader_tab_js
    assert "window.HERMES_KIS_TRADER_TAB" in js
    assert '<span>KIS</span>' in html
    assert '<strong>국장 블록</strong>' in html
    assert 'kis_trader: "국장 블록"' in js


def test_static_ui_loads_etf_tab_config_before_main_app() -> None:
    html = _html()

    assert "/static/etf_tab.js" in html
    assert html.index("/static/etf_tab.js") < html.index("/static/app.js")
    etf_tab_js = _etf_tab_js()
    js = _js()
    assert "window.HERMES_ETF_TAB" in etf_tab_js
    assert "researchRows(payload)" in etf_tab_js
    assert "universeRows(status)" in etf_tab_js
    assert "coreAllocation(payload, blocks" in etf_tab_js
    assert "researchStale(isoString" in etf_tab_js
    assert "window.HERMES_ETF_TAB" in js


def test_static_ui_loads_memory_tab_config_before_main_app() -> None:
    html = _html()

    assert "/static/memory_tab.js" in html
    assert html.index("/static/memory_tab.js") < html.index("/static/app.js")
    memory_tab_js = _memory_tab_js()
    js = _js()
    assert "window.HERMES_MEMORY_TAB" in memory_tab_js
    assert "renderPolicyStrip(memory" in memory_tab_js
    assert "renderJournalCard(row" in memory_tab_js
    assert "window.HERMES_MEMORY_TAB" in js


def test_memory_tab_owns_policy_strip_and_journal_card_renderers() -> None:
    js = _js()
    memory_tab_js = _memory_tab_js()

    for marker in (
        "renderPolicyStrip(memory",
        "renderJournalCard(row",
        "memory-policy-strip",
        "memory-journal-card",
        "data-helper-detail-id",
    ):
        assert marker in memory_tab_js
    assert "MEMORY_TAB.renderPolicyStrip(memory" in js
    assert "MEMORY_TAB.renderJournalCard(row" in js
    assert "memory-policy-strip" not in js
    assert "memory-journal-card" not in js


def test_memory_tab_owns_decision_skill_renderer() -> None:
    js = _js()
    memory_tab_js = _memory_tab_js()

    for marker in (
        "renderDecisionSkills(memoryStatus",
        "memory-skill-strip",
        "decision_skills",
        "decision_skill_status",
    ):
        assert marker in memory_tab_js
    assert "MEMORY_TAB.renderDecisionSkills(memoryStatus" in js


def test_etf_tab_owns_core_board_renderers() -> None:
    js = _js()
    etf_tab_js = _etf_tab_js()

    for marker in (
        "snapshotChip(snapshot",
        "scoreChips(score",
        "renderCandidateRow(item",
        "renderCoreBoard(payload, blocks",
        "etf-candidate-row",
        "etf-core-board",
    ):
        assert marker in etf_tab_js
    assert "ETF_TAB.snapshotChip(snapshot" in js
    assert "ETF_TAB.scoreChips(score" in js
    assert "ETF_TAB.renderCandidateRow(item" in js
    assert "ETF_TAB.renderCoreBoard(payload, blocks" in js


def test_settings_tab_owns_catalog_filter_helper() -> None:
    settings_tab_js = _settings_tab_js()
    js = _js()

    assert "filteredItems(catalog" in settings_tab_js
    assert "filteredItems(catalog, filterValue, categoryValue)" in settings_tab_js
    assert "SETTINGS_TAB.renderPage(" in js


def test_settings_tab_caps_initial_all_category_render() -> None:
    settings_path = ROOT / "src/tradecraft/web/static/settings_tab.js"
    script = f"""
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(settings_path))}, "utf8");
global.window = {{}};
eval(source);
const items = Array.from({{ length: 125 }}, (_, index) => ({{
  key: `setting_${{index}}`,
  label: `Setting ${{index}}`,
  description: "description",
  env: `ENV_${{index}}`,
  category: index % 2 ? "ai_llm" : "ops",
  category_label: index % 2 ? "AI/LLM" : "운영",
  value: String(index),
  editable: true,
  input_type: "text",
  risk: "normal",
}}));
const categories = [
  {{ key: "ai_llm", label: "AI/LLM", count: 62 }},
  {{ key: "ops", label: "운영", count: 63 }},
];
const html = window.HERMES_SETTINGS_TAB.renderPage({{
  catalog: {{ items, categories }},
  filter: "",
  category: "all",
  draft: {{}},
}}, {{
  escapeValue: (value) => String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;"),
  formatValue: String,
}});
const rowCount = (html.match(/<article class="settings-row/g) || []).length;
console.log(JSON.stringify({{
  rowCount,
  hasNotice: html.includes("전체 설정 125개 중 80개만 먼저 표시합니다."),
  defaultLimit: window.HERMES_SETTINGS_TAB.DEFAULT_ALL_RENDER_LIMIT,
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload == {
        "rowCount": 80,
        "hasNotice": True,
        "defaultLimit": 80,
    }


def test_settings_tab_owns_setting_input_renderer() -> None:
    settings_tab_js = _settings_tab_js()
    js = _js()

    for marker in (
        "renderInput(item, value",
        "settings-switch",
        "settings-textarea",
    ):
        assert marker in settings_tab_js
    assert "renderSettingInput(page, item, escape)" in settings_tab_js
    assert "SETTINGS_TAB.renderPage(" in js


def test_crypto_research_tab_owns_symbol_context_maps() -> None:
    js = _js()
    crypto_research_tab_js = _crypto_research_tab_js()

    for marker in (
        "notesMap(context)",
        "featuresMap(context)",
        "symbol_notes",
        "feature_packets",
    ):
        assert marker in crypto_research_tab_js
    assert "CRYPTO_RESEARCH_TAB.notesMap(context)" in js
    assert "CRYPTO_RESEARCH_TAB.featuresMap(context)" in js


def test_crypto_research_tab_owns_panel_renderers() -> None:
    js = _js()
    crypto_research_tab_js = _crypto_research_tab_js()

    for marker in (
        "renderTimeframeGrid(feature",
        "renderResearchPanel(state",
        "renderAlphaPanel(state",
        "renderLabTab(state",
        "crypto-research-panel",
        "crypto-alpha-panel",
        "Crypto Research Lab",
    ):
        assert marker in crypto_research_tab_js
    assert "CRYPTO_RESEARCH_TAB.renderLabTab(state" in js
    assert "function renderCryptoTimeframeGrid" not in js
    assert "function renderCryptoResearchPanel" not in js
    assert "function renderCryptoAlphaPanel" not in js
    assert "function renderCryptoResearchLabTab" not in js


def test_crypto_research_tab_owns_quant_and_pattern_boards() -> None:
    js = _js()
    crypto_research_tab_js = _crypto_research_tab_js()

    for marker in (
        "renderQuantBoard(state",
        "renderPatternBoard(state",
        "binance-quant-panel",
        "binance-pattern-panel",
        "optimized-set-board",
    ):
        assert marker in crypto_research_tab_js
    assert "renderQuantBoard(state, options)" in crypto_research_tab_js
    assert "renderPatternBoard(state, options)" in crypto_research_tab_js
    assert "function renderBinanceQuantBoard" not in js
    assert "function renderBinancePatternBoard" not in js
    assert "renderBinanceQuantBoard" not in js
    assert "renderBinancePatternBoard" not in js


def test_kis_trader_tab_owns_block_history_helpers() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "timelineDateValue(block)",
        "dateKeyKST(isoString)",
        "exitOrder(payload, block)",
        "historyPnl(payload, block",
        "filteredHistoryBlocks(payload",
        "daySummary(payload",
    ):
        assert marker in kis_trader_tab_js
    assert "KIS_TRADER_TAB.historyDates(blocks)" in js
    for marker in (
        "function blockTimelineDateValue",
        "function dateKeyKST",
        "function blockTimelineDate",
        "function blockExitOrder",
        "function blockHistoryPnl",
        "function blockHistoryStatusMatches",
        "function blockHistoryFilteredBlocks",
        "function blockHistoryDaySummary",
    ):
        assert marker not in js


def test_kis_trader_tab_owns_block_horizon_helpers() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "blockHorizons",
        "normalizeBlockHorizon(value)",
        "blockHorizonLabel(value)",
        "blockHorizonDescription(value)",
        "blockHorizonClass(value)",
        "blockHorizonForBlock(block)",
        "blockHorizonWeight(",
    ):
        assert marker in kis_trader_tab_js
    assert "KIS_TRADER_TAB.blockHorizonForBlock(block)" in js
    assert "KIS_TRADER_TAB.renderBlockCard(block" in js
    assert "blockHorizonLabel(horizon)" in kis_trader_tab_js
    for marker in (
        "const BLOCK_HORIZONS",
        "function normalizeBlockHorizon",
        "function blockHorizonLabel",
        "function blockHorizonDescription",
        "function blockHorizonClass",
        "function blockHorizonWeight",
        "function getBlockHorizon",
    ):
        assert marker not in js


def test_kis_trader_tab_owns_block_status_helpers() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "blockStatusLabel(value)",
        "blockTone(value)",
        "proposed: \"매수 대기\"",
        "if (status === \"open\") return \"good\"",
    ):
        assert marker in kis_trader_tab_js
    assert "KIS_TRADER_TAB.renderBlockCard(block" in js
    assert "blockStatusLabel(row.status)" in kis_trader_tab_js
    assert "blockTone(row.status)" in kis_trader_tab_js
    assert "function blockStatusLabel" not in js
    assert "function blockTone" not in js


def test_kis_trader_tab_owns_block_directive_context_helper() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "blockDirectiveContext(block)",
        "allocationReason",
        "preferredHorizon",
        "latestDirective",
    ):
        assert marker in kis_trader_tab_js
    assert "KIS_TRADER_TAB.renderBlockCard(block" in js
    assert "blockDirectiveContext(row)" in kis_trader_tab_js


def test_kis_trader_tab_owns_daily_discovery_helpers() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "dailyDiscoveryItems(payload)",
        "dailyDiscoverySummaryValue",
        "renderDailyDiscoveryCard(row",
        "renderDailyDiscoveryPanel(discoveryState",
        "payload,",
    ):
        assert marker in kis_trader_tab_js
    assert "KIS_TRADER_TAB.renderDailyDiscoveryPanel(" in js
    assert "function dailyDiscoveryItems" not in js
    assert "function dailyDiscoverySummaryValue" not in js
    assert "function renderDailyDiscoveryCard" not in js
    assert "function renderDailyDiscoveryPanel" not in js


def test_kis_trader_tab_owns_block_hero_and_horizon_renderers() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "renderBlockHero(payload",
        "renderHorizonAllocation(payload, blocks",
        "renderHorizonBlockGroups(blocks",
        "block-trader-hero",
        "block-horizon-allocation",
        "block-horizon-board",
    ):
        assert marker in kis_trader_tab_js
    assert "KIS_TRADER_TAB.renderBlockHero(payload" in js
    assert "KIS_TRADER_TAB.renderHorizonAllocation(payload, blocks" in js
    assert "KIS_TRADER_TAB.renderHorizonBlockGroups(blocks" in js
    assert "function renderKisBlockHero" not in js
    assert "function renderHorizonAllocation" not in js
    assert "function renderHorizonBlockGroups" not in js


def test_kis_trader_tab_owns_block_history_row_and_detail_renderers() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "renderBlockHistoryRow(payload, block",
        "renderBlockHistoryDetail(payload, block",
        "block-history-row",
        "block-history-detail",
        "renderBlockValidationChips(metadata)",
        "renderBlockCostFeasibilityChips(metadata)",
    ):
        assert marker in kis_trader_tab_js
    assert "function renderBlockHistoryRow" not in js
    assert "function renderBlockHistoryDetail" not in js


def test_kis_trader_tab_owns_block_card_renderer() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    card_start = kis_trader_tab_js.index("function renderBlockCard")
    card_end = kis_trader_tab_js.index("function renderHorizonBlockGroups", card_start)
    card_body = kis_trader_tab_js[card_start:card_end]

    assert "function renderBlockCard" not in js
    assert "block-card" in card_body
    assert "block-directive-panel" in card_body
    assert "renderBlockValidationChips(metadata)" in card_body
    assert "renderValidationPassportChips(metadata)" in card_body
    assert "renderBlockCostFeasibilityChips(metadata)" in card_body
    assert "renderBlockPolicyEffectChips(metadata)" in card_body


def test_kis_trader_tab_owns_block_history_board_renderer() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "renderBlockHistoryBoard(payload",
        "block-history-board",
        "block-history-date-controls",
        "block-history-filters",
        "renderBlockHistoryRow(payload, block",
        "renderBlockHistoryDetail(payload, selectedBlock",
    ):
        assert marker in kis_trader_tab_js
    assert "KIS_TRADER_TAB.renderBlockHistoryBoard(payload" in js
    assert "function renderBlockHistoryBoard" not in js


def test_kis_trader_tab_owns_block_ops_panels() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "renderBlockAllocation(payload",
        "renderBlockEventFeed(payload",
        "renderBlockManagerRun(payload",
        "계좌/블록 배정",
        "주문/이벤트",
        "LLM 매니저",
    ):
        assert marker in kis_trader_tab_js
    for marker in (
        "function renderBlockAllocation",
        "function renderBlockEventFeed",
        "function renderBlockManagerRun",
    ):
        assert marker not in js
    assert "KIS_TRADER_TAB.renderBlockAllocation(payload" in js
    assert "KIS_TRADER_TAB.renderBlockEventFeed(payload" in js
    assert "KIS_TRADER_TAB.renderBlockManagerRun(payload" in js


def test_kis_block_allocation_hides_zero_quantity_symbol_noise() -> None:
    kis_trader_tab_path = ROOT / "src/tradecraft/web/static/kis_trader_tab.js"
    script = f"""
global.window = {{}};
const fs = require("fs");
const source = fs.readFileSync({json.dumps(str(kis_trader_tab_path))}, "utf8");
eval(source);
const html = window.HERMES_KIS_TRADER_TAB.renderBlockAllocation({{
  allocation: {{
    items: [
      {{ symbol: "005930", name: "005930", account_qty: 0, block_qty: 0, unallocated_qty: 0, overallocated_qty: 0 }},
      {{ symbol: "360750", name: "TIGER 미국S&P500", account_qty: 4, block_qty: 4, unallocated_qty: 0, overallocated_qty: 0 }},
    ],
  }},
}}, {{
  escapeHTML: (value) => String(value ?? ""),
  asNumber: (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback,
  fmtKRW: (value) => String(Math.round(Number(value || 0))),
}});
console.log(html);
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    html = result.stdout

    assert "TIGER 미국S&P500 (360750)" in html
    assert "005930 (005930)" not in html
    assert "0수량 후보 1개 숨김" in html


def test_etf_tab_owns_core_research_helpers() -> None:
    js = _js()
    etf_tab_js = _etf_tab_js()

    for marker in (
        "researchRows(payload)",
        "universeRows(status)",
        "coreAllocation(payload, blocks",
        "researchStale(isoString",
    ):
        assert marker in etf_tab_js
    assert "ETF_TAB.researchRows(payload)" in js
    assert "ETF_TAB.universeRows(status)" in js
    assert "ETF_TAB.coreAllocation(" in js
    assert "ETF_TAB.researchStale(isoString)" in js


def test_helper_inner_tabs_removed_from_static_html() -> None:
    html = _html()

    assert 'id="helperTabs"' not in html
    assert 'data-helper-tab="ask"' not in html
    for tab in (
        "ask",
        "strategy_intel",
        "kis_memory",
        "binance_memory",
        "kis_trader",
        "binance_trader",
        "crypto_research",
        "runtime",
        "settings",
    ):
        assert f'data-nav-helper-tab="{tab}"' in html


def test_static_cache_buster_mentions_top_nav_version() -> None:
    html = _html()

    assert _script_cache_busted(html, "app.js")


def test_frontend_uses_hash_tab_before_default_ask() -> None:
    js = _js()

    assert "function resolveInitialHelperTab" in js
    assert "window.location.hash" in js
    assert 'openHelperPage("ask")' not in js
    assert "function openAskPageWithQuery" in js


def test_binance_lane_and_history_helpers_are_not_reowned_by_app_js() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()

    for marker in (
        "function binanceBlockLane",
        "function binanceBlockPrice",
        "function groupBinanceBlocksByLane",
        "function renderBinanceBlockCard",
        "function binanceHistoryDateKey",
        "function filteredBinanceHistory",
        "function renderBinanceBlockHistory",
    ):
        assert marker not in js
    assert "window.HERMES_BINANCE_TAB" in js
    assert "BINANCE_TAB.renderUniversePipeline(payload" in js
    assert "volatile_attack" in binance_tab_js
    assert "renderBlockValidationChips(metadata)" in binance_tab_js
    assert "renderBlockValidationChips," in js


def test_binance_tab_owns_lane_price_and_grouping_helpers() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()

    for marker in (
        "blockLane(block)",
        "laneLabel(value)",
        "blockPrice(block, key, aliasKey)",
        "groupBlocksByLane(blocks",
    ):
        assert marker in binance_tab_js
    assert '"spot:long:short": "현물 단기 롱"' in binance_tab_js
    assert 'lane === "all" ? "전체" : laneLabel(lane)' in binance_tab_js
    assert "${laneLabel(blockLane(block))} · ${block?.status || \"-\"}" in binance_tab_js
    assert "BINANCE_TAB.renderLaneBoard(payload, blocks" in js
    assert "BINANCE_TAB.blockLane(block)" not in js
    assert "BINANCE_TAB.blockPrice(block, key, aliasKey)" not in js
    assert "BINANCE_TAB.groupBlocksByLane(blocks, BINANCE_LANES)" not in js


def test_binance_tab_owns_history_filter_helpers() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()

    for marker in (
        "historyDateKey(block, dateFormatter",
        "historyRows(payload",
        "filteredHistory(payload",
    ):
        assert marker in binance_tab_js
    assert "BINANCE_TAB.renderBlockHistory(payload" in js
    assert "BINANCE_TAB.historyDateKey(block" not in js
    assert "BINANCE_TAB.filteredHistory(payload" not in js


def test_binance_tab_owns_history_performance_helper() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()
    start = js.index("BINANCE_TAB.renderBlockHistory(payload")
    end = js.index("function renderStatusHelperTab", start)
    body = js[start:end]

    assert "historyPerformance(block" in binance_tab_js
    assert "historyPerformance(block, asNumber)" in binance_tab_js
    assert "BINANCE_TAB.renderBlockHistory(payload" in js
    assert "block.realized_pnl_usdt ?? performance.pnl_usdt" not in body
    assert "block.r_multiple ?? performance.r_multiple" not in body


def test_binance_tab_owns_growth_and_risk_display_helpers() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()

    for marker in (
        "growthTargetStatusLabel(status)",
        "growthGovernorModeMeta(governor)",
        "growthUnlockPhaseMeta(unlock)",
        "riskGuardTone(guard)",
    ):
        assert marker in binance_tab_js
    assert "BINANCE_TAB.renderGrowthTarget(payload" in js
    assert "BINANCE_TAB.renderGrowthGovernor(payload" in js
    assert "BINANCE_TAB.renderGrowthUnlock(payload" in js
    assert "BINANCE_TAB.renderRiskGuard(payload" in js
    growth_start = js.index("BINANCE_TAB.renderGrowthTarget(payload")
    growth_end = js.index("BINANCE_TAB.renderLaneEdgePanel(payload", growth_start)
    body = js[growth_start:growth_end]
    assert "on_track: \"정상 속도\"" not in body
    assert "press_verified_edges: \"검증 Edge 가속\"" not in body
    assert "scale_ready: \"증액 가능\"" not in body


def test_binance_tab_owns_universe_growth_and_risk_renderers() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()

    for marker in (
        "latestCandidateGeneration(payload)",
        "renderCandidatePacketList(title, rows",
        "renderUniversePipeline(payload",
        "renderGrowthTarget(payload",
        "renderGrowthGovernor(payload",
        "renderGrowthUnlock(payload",
        "renderRiskGuard(payload",
        "renderLaneEdgePanel(payload",
        "binance-universe-panel",
        "binance-growth-target",
        "binance-risk-guard-panel",
    ):
        assert marker in binance_tab_js
    for marker in (
        "BINANCE_TAB.renderUniversePipeline(payload",
        "BINANCE_TAB.renderGrowthTarget(payload",
        "BINANCE_TAB.renderGrowthGovernor(payload",
        "BINANCE_TAB.renderGrowthUnlock(payload",
        "BINANCE_TAB.renderRiskGuard(payload",
        "BINANCE_TAB.renderLaneEdgePanel(payload",
    ):
        assert marker in js
    for marker in (
        "function latestBinanceCandidateGeneration",
        "function renderBinanceCandidatePacketList",
        "function renderBinanceUniversePipeline",
        "function renderBinanceGrowthTarget",
        "function renderBinanceGrowthGovernor",
        "function renderBinanceGrowthUnlock",
        "function renderBinanceLaneEdgePanel",
        "function renderBinanceRiskGuard",
    ):
        assert marker not in js


def test_binance_tab_owns_lane_board_and_kpi_renderers() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()

    for marker in (
        "activeBlocks(payload)",
        "renderKpiGrid(payload, blocks",
        "renderLaneBoard(payload, blocks",
        "block-trader-kpis",
        "binance-lane-board",
    ):
        assert marker in binance_tab_js
    assert "BINANCE_TAB.activeBlocks(payload)" in js
    assert "BINANCE_TAB.renderKpiGrid(payload, blocks" in js
    assert "BINANCE_TAB.renderLaneBoard(payload, blocks" in js


def test_binance_ui_separates_observe_universe_from_trade_candidates() -> None:
    binance_tab_js = _binance_tab_js()

    assert "상위 300 관찰" in binance_tab_js
    assert "실행 후보" in binance_tab_js
    assert "초변동 공격" in binance_tab_js
    assert "candidate_packets" in binance_tab_js
    assert "volatile_candidates" in binance_tab_js


def test_binance_history_renders_usdt_realized_pnl() -> None:
    js = _js()
    formatters_js = _ui_formatters_js()

    assert "function fmtUSDT" in formatters_js
    assert "fmtUSDT," in js
    assert "realized_pnl_usdt" in js
    assert "performance_today" in js
    assert "성과 USDT" in js
    assert "실현 손익" in js
    assert "성과 개선포인트" in js


def test_binance_backtest_lab_exposes_optimization_sets() -> None:
    js = _js()
    crypto_research_tab_js = _crypto_research_tab_js()

    assert "백테스트·최적화 랩" in crypto_research_tab_js
    assert "백테스트 라이브 교차검증" in js
    assert "optimized_strategy_sets" in js
    assert "optimization.set_count" in crypto_research_tab_js
    assert "function renderBinanceBacktestConfluencePanel" in js
    assert "optimized-set-card" in crypto_research_tab_js
    assert "patternSet: row" in js
    assert "원본 패턴 스코어카드" in crypto_research_tab_js


def test_live_authority_panel_surfaces_validation_gate() -> None:
    js = _js()
    panel_body = _live_authority_panel_body()
    start = js.index("function renderTradingValidationDetails")
    end = js.index("function binancePatternDirection", start)
    body = js[start:end] + panel_body

    assert "validation_gate" in body
    assert "trading_validation" in body
    assert "검증 게이트" in body
    assert "Readiness" in body
    assert "function renderTradingValidationDetails" in js
    assert "검증 랩 요약" in body
    assert "취약 테스트" in body
    assert "용량 병목" in body
    assert "operator_guidance" in body
    assert "tightest_symbol" in body
    assert "risk_governor_action" in body
    assert "Risk Governor" in body
    assert "failure_attribution" in body
    assert "실패 귀속" in body
    assert "recovery_focus" in body
    assert "source.payload" in body
    assert "pattern_lab" in body
    assert "source_scope" in body
    assert "검증 근거" in body
    assert "kis_live_forward_proxy" in body
    assert "전체 19검증" in body
    assert "trading-validation-matrix" in body
    assert "disciplineRows" in body
    assert "work_queue" in body
    assert "lane_policy_hints" in body
    assert "회복 작업" in body
    assert "entry_mode" in body
    assert "blocks_scaling" in body
    assert "lane_scorecards" in body
    assert "Lane 성과" in body
    assert "weak_lanes" in body


def test_live_authority_panel_uses_gate_matrix_as_validation_fallback() -> None:
    js = _js()
    panel_body = _live_authority_panel_body()
    detail_start = js.index("function renderTradingValidationDetails")
    detail_end = js.index("function binancePatternDirection", detail_start)
    detail_body = js[detail_start:detail_end]

    assert "function mergeTradingValidationWithGateMatrix" in js
    assert "validationGate.discipline_matrix" in panel_body
    assert "mergeTradingValidationWithGateMatrix(tradingValidation, validationGate)" in panel_body
    assert "validationMatrixSummary" in panel_body
    assert "validationCountsSummary" in panel_body
    assert "expectedDisciplineCount" in detail_body
    assert "actualDisciplineCount" in detail_body
    assert "rowDetailCount" in detail_body
    assert "summaryOnlyValidation" in detail_body
    assert "summaryWeakCount" in detail_body
    assert "summaryWeakRows" in detail_body
    assert "row 상세" in detail_body
    assert "summary 기준" in detail_body
    assert "summary 기준 취약 항목" in detail_body


def test_live_authority_panel_surfaces_validation_passport_summary() -> None:
    panel_body = _live_authority_panel_body()

    assert "validationGate.validation_passport" in panel_body
    assert "validationPassport" in panel_body
    assert "passportFailedIds" in panel_body
    assert "passportWeakIds" in panel_body
    assert "requires_revalidation" in panel_body
    assert "검증 여권" in panel_body
    assert "재검증" in panel_body


def test_kis_block_cards_surface_block_validation_chips() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()
    card_start = kis_trader_tab_js.index("function renderBlockCard")
    card_end = kis_trader_tab_js.index("function renderHorizonBlockGroups", card_start)
    card_body = kis_trader_tab_js[card_start:card_end]
    detail_start = kis_trader_tab_js.index("function renderBlockHistoryDetail")
    detail_end = kis_trader_tab_js.index("function renderBlockHistoryBoard", detail_start)
    detail_body = kis_trader_tab_js[detail_start:detail_end]

    assert "function renderBlockValidationChips" in js
    assert "discipline_matrix" in js
    assert "검증" in js
    assert "renderBlockValidationChips(metadata)" in card_body
    assert "renderBlockValidationChips(metadata)" in detail_body


def test_block_cards_surface_validation_passport_chips() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()
    kis_trader_tab_js = _kis_trader_tab_js()
    card_start = kis_trader_tab_js.index("function renderBlockCard")
    card_end = kis_trader_tab_js.index("function renderHorizonBlockGroups", card_start)
    card_body = kis_trader_tab_js[card_start:card_end]
    detail_start = kis_trader_tab_js.index("function renderBlockHistoryDetail")
    detail_end = kis_trader_tab_js.index("function renderBlockHistoryBoard", detail_start)
    detail_body = kis_trader_tab_js[detail_start:detail_end]

    assert "function renderValidationPassportChips" in js
    assert "validation_passport" in js
    assert "requires_revalidation" in js
    assert "failed_ids" in js
    assert "검증 여권" in js
    assert "renderValidationPassportChips(metadata)" in card_body
    assert "renderValidationPassportChips(metadata)" in detail_body
    assert "renderValidationPassportChips(metadata)" in binance_tab_js
    assert "renderValidationPassportChips," in js


def test_block_cards_surface_policy_effect_audit_chips() -> None:
    js = _js()
    binance_tab_js = _binance_tab_js()
    kis_trader_tab_js = _kis_trader_tab_js()
    card_start = kis_trader_tab_js.index("function renderBlockCard")
    card_end = kis_trader_tab_js.index("function renderHorizonBlockGroups", card_start)
    card_body = kis_trader_tab_js[card_start:card_end]
    detail_start = kis_trader_tab_js.index("function renderBlockHistoryDetail")
    detail_end = kis_trader_tab_js.index("function renderBlockHistoryBoard", detail_start)
    detail_body = kis_trader_tab_js[detail_start:detail_end]

    assert "function renderBlockPolicyEffectChips" in js
    assert "policy_effect_audit" in js
    assert "정책 반영" in js
    assert "target_price" in js
    assert "stop_price" in js
    assert "renderBlockPolicyEffectChips(metadata)" in card_body
    assert "renderBlockPolicyEffectChips(metadata)" in detail_body
    assert "renderBlockPolicyEffectChips(metadata)" in binance_tab_js
    assert "renderBlockPolicyEffectChips," in js


def test_kis_block_cards_surface_cost_feasibility_chips() -> None:
    html = _html()
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()
    card_start = kis_trader_tab_js.index("function renderBlockCard")
    card_end = kis_trader_tab_js.index("function renderHorizonBlockGroups", card_start)
    card_body = kis_trader_tab_js[card_start:card_end]
    detail_start = kis_trader_tab_js.index("function renderBlockHistoryDetail")
    detail_end = kis_trader_tab_js.index("function renderBlockHistoryBoard", detail_start)
    detail_body = kis_trader_tab_js[detail_start:detail_end]

    assert "function renderBlockCostFeasibilityChips" in js
    assert "cost_feasibility" in js
    assert "비용 후" in js
    assert "비용배수" in js
    assert "target_cost_multiple" in js
    assert "renderBlockCostFeasibilityChips(metadata)" in card_body
    assert "renderBlockCostFeasibilityChips(metadata)" in detail_body
    assert _script_cache_busted(html, "app.js")


def test_trading_validation_details_preserves_top_level_freshness() -> None:
    js = _js()
    start = js.index("function renderTradingValidationDetails")
    end = js.index("function binancePatternDirection", start)
    body = js[start:end]

    assert "source.age_sec" in body
    assert "source.stale" in body
    assert "source.stale_reason" in body
    assert "staleWarningHtml" in body
    assert "검증 오래됨" in body


def test_live_authority_panel_surfaces_validation_remediation_plan() -> None:
    js = _js()
    start = js.index("function renderTradingValidationDetails")
    end = js.index("function binancePatternDirection", start)
    body = js[start:end]

    assert "remediation_plan" in body
    assert "검증 복구 플랜" in body
    assert "trading-validation-remediation" in body
    assert "immediate_ops_controls" in body
    assert "research_validation_work" in body
    assert "sizing_risk_controls" in body
    assert "primary_next_action" in body
    assert "active_revision_evidence" in body
    assert "active revision" in body
    assert "legacy_proxy_sample_count" in body
    assert "proxy scale" in body


def test_trading_validation_details_surfaces_trade_blocking_scope() -> None:
    js = _js()
    start = js.index("function renderTradingValidationDetails")
    end = js.index("function binancePatternDirection", start)
    body = js[start:end]

    assert "trade_blocking" in body
    assert "blocking_scope" in body
    assert "remediationBlockingLabel" in body
    assert "거래 가능 · 스케일업 제한" in body
    assert "거래 차단" in body


def test_trading_validation_details_surfaces_pattern_lab_failed_reasons() -> None:
    js = _js()
    start = js.index("function renderTradingValidationDetails")
    end = js.index("function binancePatternDirection", start)
    body = js[start:end]

    assert "failed_reasons" in body
    assert "topPatternFailedReasons" in body
    assert "패턴랩 복구" in body
    assert "out_of_sample_missing" in body
    assert "walk_forward_pass_rate_low" in body


def test_live_authority_panel_surfaces_loss_cooldown() -> None:
    body = _live_authority_panel_body()

    assert "loss_cooldown" in body
    assert "손실 쿨다운" in body
    assert "do_not_scale_or_create_live_entry_without_new_evidence" in body
    assert "lossCooldownHtml" in body


def test_live_authority_panel_surfaces_repair_execution() -> None:
    js = _js()
    body = _live_authority_panel_body()

    assert "repair_execution" in body
    assert "검증 복구 실행" in body
    assert "repair-execution-panel" in body
    assert "scale-up 차단" in body
    assert "live shadow 필요" in body
    assert "repairExecutionTone" in js


def test_block_trader_tabs_surface_validation_repair_ops() -> None:
    js = _js()
    kis_start = js.index("function renderKisBlockTradingTab")
    kis_end = js.index("function cryptoResearchNotesMap", kis_start)
    kis_body = js[kis_start:kis_end]
    binance_start = js.index("function renderBinanceTraderTab")
    binance_end = js.index("function renderStatusHelperTab", binance_start)
    binance_body = js[binance_start:binance_end]

    assert "function renderValidationRepairOpsPanel" in js
    assert "validation_repair_ops" in js
    assert "19검증 수리 상태" in js
    assert 'renderValidationRepairOpsPanel(payload, "KIS 쥬")' in kis_body
    assert 'renderValidationRepairOpsPanel(payload, "바이낸스 쥬")' in binance_body


def test_live_authority_panel_uses_user_facing_validation_gate_labels() -> None:
    shared_js = _ui_shared_js()
    ops_js = _ui_ops_js()
    panel_body = _live_authority_panel_body()

    assert "function formatValidationGateLabel" in ops_js
    assert "function formatValidationGateReason" in ops_js
    assert "blocked_by_validation: \"검증 차단\"" in shared_js
    assert "risk_repair: \"리스크 수리\"" in shared_js
    assert "validation_incomplete: \"19개 검증 미완성\"" in shared_js
    assert "formatValidationGateLabel(gateStatus)" in panel_body
    assert "formatValidationGateReason(gateReason)" in panel_body
    assert "applied_max_budget_multiplier" in panel_body
    assert "적용 배수" in panel_body


def test_ops_validation_summary_surfaces_diagnostic_status() -> None:
    shared_js = _ui_shared_js()
    ops_js = _ui_ops_js()

    assert "risk_repair: \"리스크 수리\"" in shared_js
    assert "diagnostic_status" in ops_js
    assert "formatValidationGateLabel(diagnosticStatus)" in ops_js


def test_live_authority_panel_surfaces_validation_evidence_status() -> None:
    shared_js = _ui_shared_js()
    ops_js = _ui_ops_js()
    panel_body = _live_authority_panel_body()

    assert "validationEvidenceLabels" in shared_js
    assert "function formatValidationEvidenceLabel" in ops_js
    assert "function validationEvidenceTone" in ops_js
    assert "validation_evidence_status" in panel_body
    assert "validation_missing_dimensions" in panel_body
    assert "validation_failed_dimensions" in panel_body
    assert "검증 증거" in panel_body


def test_live_authority_panel_surfaces_active_revision_pending_blocks() -> None:
    js = _js()
    shared_js = _ui_shared_js()
    panel_body = _live_authority_panel_body()

    assert "activeRevisionEvidenceLabels" in shared_js
    assert "active_revision_samples_pending_close" in shared_js
    assert "no_active_revision_samples_with_proxy" in shared_js
    assert "열린 블록 검증 대기" in shared_js
    assert "과거 proxy 참고" in shared_js
    assert "function formatActiveRevisionEvidenceLabel" in js
    assert "function activeRevisionEvidenceTone" in js
    assert "authority.active_revision_evidence" in panel_body
    assert "pending_block_count" in panel_body
    assert "pending_block_lane_counts" in panel_body
    assert "Active revision" in panel_body


def test_live_authority_panel_surfaces_cost_precision_evidence() -> None:
    shared_js = _ui_shared_js()
    ops_js = _ui_ops_js()
    panel_body = _live_authority_panel_body()

    assert "costEvidenceLabels" in shared_js
    assert "hybrid_needs_market_cost_repair" in shared_js
    assert "혼합 비용 · 시장비용 보강" in shared_js
    assert "function formatCostEvidenceLabel" in ops_js
    assert "function costEvidenceTone" in ops_js
    assert "authority.performance_lanes" in panel_body
    assert "비용 증거" in panel_body
    assert "cost_precision_counts" in panel_body
    assert "cost_hybrid_alpha_count" in panel_body
    assert "scale_blocked_by_cost_precision" in panel_body
    assert "scale_blocked_by_verified_edge_samples" in panel_body
    assert "cost_verified_alpha_count" in panel_body
    assert "검증 α 샘플 부족" in panel_body
    assert "미검증 α" in panel_body


def test_live_authority_panel_uses_user_facing_risk_governor_labels() -> None:
    js = _js()
    shared_js = _ui_shared_js()
    ops_js = _ui_ops_js()
    panel_body = _live_authority_panel_body()
    passport_start = js.index("function renderValidationPassportChips")
    passport_end = js.index("function renderBlockCostFeasibilityChips", passport_start)
    passport_body = js[passport_start:passport_end]

    assert "function formatRiskGovernorLabel" in ops_js
    assert "function formatRiskGovernorSourceLabel" in ops_js
    assert "live_authority_error: \"Live Authority 오류로 신규 리스크 중단\"" in shared_js
    assert "live_authority_budget_zero: \"신규 리스크 예산 0\"" in shared_js
    assert "live_authority_risk_governor:halt_new_risk" in shared_js
    assert "formatRiskGovernorLabel(riskAction)" in passport_body
    assert "formatRiskGovernorLabel(riskGovernorAction)" in panel_body
    assert "formatRiskGovernorSourceLabel(riskGovernorSource)" in panel_body


def test_live_authority_panel_surfaces_row_detail_coverage() -> None:
    js = _js()
    block_start = js.index("function renderBlockValidationChips")
    block_end = js.index("function renderValidationPassportChips", block_start)
    block_body = js[block_start:block_end]
    passport_start = js.index("function renderValidationPassportChips")
    passport_end = js.index("function renderBlockCostFeasibilityChips", passport_start)
    passport_body = js[passport_start:passport_end]
    panel_body = _live_authority_panel_body()

    assert "row_detail_count" in js
    assert "row_detail_complete" in js
    assert "rowDetailLabel" in block_body
    assert "row 상세" in passport_body
    assert "passportRowDetailLabel" in panel_body
    assert "상세 row" in panel_body


def test_live_authority_panel_preserves_zero_budget_multiplier() -> None:
    formatters_js = _ui_formatters_js()
    live_js = _ui_live_authority_js()
    panel_body = _live_authority_panel_body()

    assert "function formatLiveMultiplier" in formatters_js
    assert "formatLiveMultiplier," in live_js
    assert "formatLiveMultiplier(authority.max_budget_multiplier)" in panel_body
    assert "multiplier ? `${fmtNum(multiplier, 2)}x` : \"-\"" not in panel_body


def test_ops_readiness_signals_are_user_facing_labels() -> None:
    js = _js()
    shared_js = _ui_shared_js()
    ops_js = _ui_ops_js()
    banner_start = js.index("function renderOpsBanner")
    banner_end = js.index("function metricTone", banner_start)
    banner_body = js[banner_start:banner_end]
    block_start = js.index("function renderBlockOpsReadiness")
    block_end = js.index("function renderKisBlockTradingTab", block_start)
    block_body = js[block_start:block_end]

    assert "function formatOpsSignalLabel" in ops_js
    assert "trading_validation_incomplete_kis" in shared_js
    assert "KIS 19개 검증 미완성" in shared_js
    assert "trading_validation_blocked_binance" in shared_js
    assert "Binance 검증 차단" in shared_js
    assert "trading_validation_strategy_blocked_binance" in shared_js
    assert "Binance 전략 검증 차단" in shared_js
    assert "formatOpsSignalList" in banner_body
    assert ".join(\" · \")" not in banner_body
    assert "formatOpsSignalLabel(item)" in block_body


def test_ops_restart_process_summary_names_stale_runner_targets() -> None:
    ops_path = ROOT / "src/tradecraft/web/static/ui_ops.js"
    script = f"""
const fs = require("fs");
global.window = {{
  HERMES_UI_SHARED: {{}},
  HERMES_UI_FORMATTERS: {{
    escapeHTML: (value) => String(value ?? ""),
    fmtPercent: (value) => `${{Number(value || 0).toFixed(1)}}%`,
    truncateWithEllipsis: (value) => String(value ?? ""),
  }},
}};
const source = fs.readFileSync({json.dumps(str(ops_path))}, "utf8");
eval(source);
const fn = window.HERMES_UI_OPS.formatOpsRestartProcessSummary;
if (typeof fn !== "function") throw new Error("formatOpsRestartProcessSummary missing");
console.log(fn({{
  stale_processes: ["kis_block_trader"],
  missing_processes: ["watchdog"],
  duplicate_processes: [],
  processes: {{
    kis_block_trader: {{ label: "KIS block trader runner" }},
    watchdog: {{ label: "watchdog runner" }},
  }},
}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == (
        "대상 KIS block trader runner 재시작 필요 · watchdog runner 중지"
    )


def test_ops_readiness_surfaces_remediation_actions() -> None:
    js = _js()
    ops_js = _ui_ops_js()
    banner_start = js.index("function renderOpsBanner")
    banner_end = js.index("function metricTone", banner_start)
    banner_body = js[banner_start:banner_end]
    block_start = js.index("function renderBlockOpsReadiness")
    block_end = js.index("function renderKisBlockTradingTab", block_start)
    block_body = js[block_start:block_end]

    assert "function renderOpsRemediationActions" in ops_js
    assert "remediation_actions" in js
    assert "다음 조치" in ops_js
    assert "data-ops-action-endpoint" in ops_js
    assert (
        "renderOpsRemediationActions(readiness.operational_remediation_actions"
        in banner_body
    )
    assert "renderOpsRemediationActions(ops.remediation_actions" in block_body


def test_static_ui_binance_manual_actions_send_live_confirmation_payloads() -> None:
    js = _js()
    helper_start = js.index("function binanceLiveExecutionEnabled")
    load_start = js.index("async function loadBinanceBlocks")
    load_end = js.index("async function loadCryptoResearch", load_start)
    body = js[helper_start:load_end]

    assert "function confirmBinanceLiveManualAction" in body
    assert "window.confirm" in body
    assert "confirm_live_manager_run: true" in body
    assert "confirm_live_executor_tick: true" in body
    assert 'getJSON("/binance/blocks/manager/run-once"' in body
    assert 'getJSON("/binance/blocks/executor/tick"' in body


def test_ops_readiness_surfaces_trading_validation_bottlenecks() -> None:
    js = _js()
    ops_js = _ui_ops_js()
    banner_start = js.index("function renderOpsBanner")
    banner_end = js.index("function metricTone", banner_start)
    banner_body = js[banner_start:banner_end]
    block_start = js.index("function renderBlockOpsReadiness")
    block_end = js.index("function renderKisBlockTradingTab", block_start)
    block_body = js[block_start:block_end]

    assert "function renderTradingValidationBottleneckSummary" in ops_js
    assert "tradingValidation?.bottlenecks" in ops_js
    assert "tradingValidation?.primary_next_actions" in ops_js
    assert "tradingValidation?.discipline_count" in ops_js
    assert "tradingValidation?.expected_discipline_count" in ops_js
    assert "19검증 집계" in ops_js
    assert "fail ${failCount}" in ops_js
    assert "function renderTradingValidationCostAttribution" in ops_js
    assert "worst_cost_groups" in ops_js
    assert "worst_cost_rows" in ops_js
    assert "비용 취약" in ops_js
    assert "비용 역전" in ops_js
    assert "19검증 병목" in ops_js
    assert "최우선 복구" in ops_js
    assert "renderTradingValidationBottleneckSummary" not in banner_body
    assert "renderTradingValidationBottleneckSummary(ops.trading_validation" in block_body


def test_ops_banner_hides_when_only_strategy_advisories_remain() -> None:
    js = _js()
    banner_start = js.index("function renderOpsBanner")
    banner_end = js.index("function metricTone", banner_start)
    banner_body = js[banner_start:banner_end]

    assert (
        "banner.hidden = blockers.length === 0 && warnings.length === 0;"
    ) in banner_body


def test_ops_banner_does_not_label_advisories_as_global_operations() -> None:
    js = _js()
    banner_start = js.index("function renderOpsBanner")
    banner_end = js.index("function metricTone", banner_start)
    banner_body = js[banner_start:banner_end]

    assert "const hasOnlyAdvisories =" not in banner_body
    assert "쥬 운영 정상 · 전략 개선 큐" not in banner_body
    assert "운영 차단" in banner_body
    assert "운영 점검 필요" in banner_body


def test_ops_banner_does_not_use_sticky_overlay_that_blocks_navigation() -> None:
    css = _css()
    banner_start = css.index(".ops-banner {")
    banner_end = css.index(".ops-banner.good", banner_start)
    banner_block = css[banner_start:banner_end]

    assert "position: sticky" not in banner_block
    assert "position: relative" in banner_block


def test_crypto_research_lab_is_top_level_page() -> None:
    html = _html()
    js = _js()
    crypto_research_tab_js = _crypto_research_tab_js()

    assert 'data-nav-helper-tab="crypto_research"' in html
    assert "crypto_research" in js
    assert "renderLabTab(state" in crypto_research_tab_js
    assert "CRYPTO_RESEARCH_TAB.renderLabTab(state" in js
    assert _script_cache_busted(html, "crypto_research_tab.js")


def test_ops_banner_labels_disk_space_signals() -> None:
    js = _js()
    shared_js = _ui_shared_js()
    runtime_tab_js = _runtime_tab_js()

    assert "disk_space_critical" in shared_js
    assert "디스크 여유 공간 위험" in shared_js
    assert "디스크 ${fmtBytes(disk.free_bytes)} 여유" in js
    assert "extracted PDFs" in runtime_tab_js
    assert "compact DBs" in runtime_tab_js
    assert "database_compact_candidates" in runtime_tab_js


def test_evidence_policy_flow_is_embedded_without_jue_lab_tab() -> None:
    html = _html()
    js = _js()

    assert 'data-nav-helper-tab="jue_lab"' not in html
    assert "renderJueLabTab" not in js
    assert "function renderEvidencePolicyFlow" in js
    assert '"/evidence-policy/status"' in js
    assert '"/evidence-policy/context"' in js
    assert "Object.keys(sourceMap).length" in js
    assert "const shouldRefreshVisibleFlow = () => (" in js
    assert 'state.activeHelperTab === "crypto_research" || isMemoryTab(state.activeHelperTab)' in js
    assert "if (shouldRender || shouldRefreshVisibleFlow()) renderHelperAgent();" in js


def test_memory_tabs_are_split_between_kis_and_binance_scopes() -> None:
    html = _html()
    js = _js()
    tabs_js = _tabs_js()

    assert 'data-nav-helper-tab="kis_memory"' in html
    assert 'data-nav-helper-tab="binance_memory"' in html
    assert 'data-nav-helper-tab="memory"' not in html
    assert '"kis_memory"' in tabs_js
    assert '"binance_memory"' in tabs_js
    assert 'tab === "kis_memory" || tab === "binance_memory"' in js
    assert "memoryScopeForTab(" in js
    assert 'return `/memory/today?scope=${encodeURIComponent(scope)}&compact=true`;' in js
    assert "getJSON(memoryTodayPath(scope))" in js
    assert "20260710_operator_shell_v3" in html


def test_investment_memory_loader_has_inflight_guard() -> None:
    js = _js()
    ensure_start = js.index("function ensureHelperTabData")
    ensure_end = js.index("function stringifySafe", ensure_start)
    ensure_body = js[ensure_start:ensure_end]
    load_start = js.index("async function loadInvestmentMemory")
    load_end = js.index("async function loadJueWiki", load_start)
    load_body = js[load_start:load_end]

    assert "investmentMemoryLoading: false" in js
    assert "&& !state.investmentMemoryLoading" in ensure_body
    assert "if (state.investmentMemoryLoading) return;" in load_body
    assert "state.investmentMemoryLoading = true;" in load_body
    assert "state.investmentMemoryLoading = false;" in load_body


def test_jue_wiki_ui_surface_exists() -> None:
    html = _html()
    js = _js()
    tabs_js = _tabs_js()

    assert 'data-nav-helper-tab="jue_wiki"' in html
    assert "renderJueWikiTab" in js
    assert "/wiki/status" in js
    assert "/wiki/context" in js
    assert '"jue_wiki"' in tabs_js


def test_jue_wiki_phase2_ui_controls_exist() -> None:
    html = _html()
    js = _js()

    assert "/wiki/search" in js
    assert "/wiki/lint/findings" in js
    assert "/wiki/repair/run-once" in js
    assert "jue-wiki-search" in html or "renderJueWiki" in js


def test_jue_wiki_phase3_applied_intelligence_ui_exists() -> None:
    js = Path("src/tradecraft/web/static/app.js").read_text(encoding="utf-8")

    assert "/wiki/application/status" in js
    assert "/wiki/application/effectiveness" in js
    assert "jueWikiApplicationStatus" in js
    assert "jueWikiApplicationEffectiveness" in js
    assert "renderJueWikiAppliedIntelligencePanel" in js
    assert "wiki-effectiveness" in js


def test_no_standalone_trading_lab_artifacts_are_introduced() -> None:
    root = ROOT

    assert not (root / "src/tradecraft/services/trading_lab.py").exists()
    assert not (root / "src/tradecraft/runtime/trading_lab_runner.py").exists()
    assert not (root / "docs/superpowers/plans/2026-05-25-jue-trading-lab.md").exists()


def test_binance_block_tab_does_not_embed_research_lab_panels() -> None:
    js = _js()
    start = js.index("function renderBinanceTraderTab")
    end = js.index("function renderStatusHelperTab", start)
    body = js[start:end]

    assert "renderCryptoResearchPanel()" not in body
    assert "renderCryptoAlphaPanel()" not in body
    assert "renderBinanceQuantBoard()" not in body
    assert "renderBinancePatternBoard()" not in body
    assert "renderBinanceBacktestConfluencePanel(payload)" in body


def test_binance_hold_note_has_full_detail_view() -> None:
    js = _js()

    assert "function renderBinanceHoldDecisionDetailText" in js
    assert "쥬 관망 노트 전체보기" in js
    assert "data-helper-detail-id" in js
    assert "리서치 후보" in js
    assert "실행 설계" in js
    assert "section(\"실행 설계 후보\"" in js


def test_binance_hold_note_exposes_data_gap_diagnostics() -> None:
    js = _js()

    for marker in (
        "book_enriched_count",
        "crypto_market_pulse",
        "book_fresh",
        "entry_preflight_blocked",
    ):
        assert marker in js


def test_settings_page_exposes_one_touch_restart() -> None:
    js = _js()
    settings_tab_js = _settings_tab_js()

    assert 'data-settings-action="restart"' in settings_tab_js
    assert '"/ops/restart"' in js
    assert "restartRunnersForSettings" in js
    assert "confirm_active_trading_restart" in js
    assert "KIS 장중 블록" in js


def test_settings_tab_owns_page_shell_renderer() -> None:
    js = _js()
    settings_tab_js = _settings_tab_js()

    assert "function renderPage(page" in settings_tab_js
    assert "settings-shell" in settings_tab_js
    assert "settings-category" in settings_tab_js
    assert "settings-save-result" in settings_tab_js
    assert "SETTINGS_TAB.renderPage(" in js
    assert "const categoryButtons = [" not in js


def test_settings_page_exposes_jue_workflow_status() -> None:
    js = _js()
    settings_tab_js = _settings_tab_js()

    assert "function renderJueWorkflowStatus(page" in settings_tab_js
    assert "renderJueWorkflowStatus(page, escape)" in settings_tab_js
    assert "SETTINGS_TAB.renderPage(" in js
    assert "function renderJueWorkflowStatus()" not in js
    assert '"/jue/workflows/status"' in js
    assert 'data-settings-action="refresh-jue-workflows"' in settings_tab_js
    assert "data-jue-workflow-panel" in settings_tab_js


def test_settings_page_exposes_codex_native_status() -> None:
    js = _js()
    settings_tab_js = _settings_tab_js()

    assert "function renderCodexNativeStatus(page" in settings_tab_js
    assert "renderCodexNativeStatus(page, escape)" in settings_tab_js
    assert "SETTINGS_TAB.renderPage(" in js
    assert "function renderCodexNativeStatus()" not in js
    assert '"/codex/native/status"' in js
    assert 'data-settings-action="refresh-codex-native"' in settings_tab_js
    assert "data-codex-native-panel" in settings_tab_js
    assert "Codex Native" in settings_tab_js


def test_memory_page_exposes_jue_source_and_lifecycle_panels() -> None:
    js = _js()
    memory_tab_js = _memory_tab_js()

    assert "renderJueSourceManifestPanel(state" in memory_tab_js
    assert "renderJueLifecyclePanel(state" in memory_tab_js
    assert '"/jue/source-manifest"' in js
    assert '"/jue/lifecycle/latest?limit=12"' in js
    assert 'data-memory-action="refresh_jue_context"' in memory_tab_js
    assert "Decision Lifecycle v3" in memory_tab_js
    assert "MEMORY_TAB.renderJueSourceManifestPanel(" in js
    assert "MEMORY_TAB.renderJueLifecyclePanel(" in js
    assert "function renderJueSourceManifestPanel" not in js
    assert "function renderJueLifecyclePanel" not in js


def test_memory_page_surfaces_validation_policy_scorecards() -> None:
    js = _js()

    assert "function renderValidationPolicyScorecards" in js
    assert "19검증 학습 정책" in js
    assert "validation-policy-grid" in js
    assert 'startsWith("validation.")' in js
    assert "discipline_id" in js


def test_kis_creative_hypothesis_panel_exists() -> None:
    js = _js()
    kis_trader_tab_js = _kis_trader_tab_js()

    for marker in (
        "renderKisHoldDecisionDetailText",
        "renderKisHoldDecision(payload",
        "renderKisCreativeHypothesesDetailText",
        "renderKisCreativeHypotheses(payload",
        "KIS 쥬 창의적 가설",
        "가설 전체보기",
    ):
        assert marker in kis_trader_tab_js
    assert "KIS_TRADER_TAB.renderKisHoldDecision(payload" in js
    assert "KIS_TRADER_TAB.renderKisCreativeHypotheses(payload" in js
    assert "function renderKisCreativeHypothesesDetailText" not in js
    assert "function renderKisCreativeHypotheses(payload" not in js


def test_runtime_storage_loads_only_when_runtime_tab_is_visible() -> None:
    js = _js()
    refresh_start = js.index("async function refreshDashboard")
    refresh_end = js.index("function renderTelegramStatus", refresh_start)
    refresh_body = js[refresh_start:refresh_end]
    ensure_start = js.index("function ensureHelperTabData")
    ensure_end = js.index("function stringifySafe", ensure_start)
    ensure_body = js[ensure_start:ensure_end]

    assert 'getJSON("/runtime/storage")' not in refresh_body
    assert "async function loadRuntimeStorage()" in js
    assert 'tab === "runtime"' in ensure_body
    assert "loadRuntimeStorage();" in ensure_body


def test_runtime_tab_surfaces_retained_rag_rebuild_backups() -> None:
    runtime_tab_js = _runtime_tab_js()

    assert "retained_artifacts" in runtime_tab_js
    assert "rag_rebuild_backups" in runtime_tab_js
    assert "RAG 재빌드 백업" in runtime_tab_js
    assert "보존 대상" in runtime_tab_js


def test_kis_active_refresh_reuses_embedded_readiness_without_full_ops_call() -> None:
    js = _js()
    load_start = js.index("async function loadKisBlocks")
    load_end = js.index("function mergeKisBlockRows", load_start)
    load_kis_blocks = js[load_start:load_end]

    assert "mergeOpsReadinessFromKisPayload(payload.readiness)" in load_kis_blocks
    assert "const includeOpsReadiness = options.includeOpsReadiness !== false;" in load_kis_blocks
    assert "const shouldLoadOpsReadiness = includeOpsReadiness && !(activeOnly && silent);" in load_kis_blocks
    assert "shouldLoadOpsReadiness ? loadOpsReadiness() : Promise.resolve()" in load_kis_blocks


def test_initial_prioritized_kis_helper_load_skips_duplicate_ops_readiness() -> None:
    js = _js()
    init_start = js.index("async function init()")
    init_end = js.index("init();", init_start)
    init_source = js[init_start:init_end]

    assert "const prioritizeKisBlocks =" in init_source
    assert "includeOpsReadiness: false" in init_source


def test_operator_navigation_is_grouped_and_mobile_dock_stays_compact() -> None:
    html = _html()
    tabs_js = _tabs_js()

    assert "navigationGroups" in tabs_js
    for group in ("운용", "판단·리서치", "학습", "시스템"):
        assert group in tabs_js
    assert "mobileNavItems" in tabs_js
    for item in ("홈", "국장", "크립토", "리서치", "더보기"):
        assert item in tabs_js
    assert 'id="mobileNavDock"' in html
    assert 'aria-label="모바일 주요 내비게이션"' in html
    assert 'id="mobileNavMoreBtn"' in html
    assert 'aria-controls="primaryNavRail"' in html
    assert 'id="primaryNavRail"' in html


def test_operator_shell_assets_share_current_cache_version() -> None:
    html = _html()
    version = "20260710_operator_shell_v3"

    assert f"/static/style.css?v={version}" in html
    assert f"/static/tabs.js?v={version}" in html
    assert f"/static/ui_shell.js?v={version}" in html
    assert f"/static/kis_trader_tab.js?v={version}" in html
    assert f"/static/binance_tab.js?v={version}" in html
    assert f"/static/app.js?v={version}" in html


def test_ui_shell_contract_builds_safety_summary_and_resource_states() -> None:
    html = _html()
    js = _js()
    shell_js = _ui_shell_js()

    assert "/static/ui_shell.js" in html
    assert html.index("/static/ui_shell.js") < html.index("/static/app.js")
    assert "window.HERMES_UI_SHELL" in shell_js
    assert "function normalizeResourceState(" in shell_js
    assert "function buildSafetySummary(" in shell_js
    assert "function renderHomeOpsSummaryHtml(" in shell_js
    assert "const UI_SHELL = window.HERMES_UI_SHELL || {};" in js
    assert "UI_SHELL.buildSafetySummary(" in js
    assert "UI_SHELL.renderHomeOpsSummaryHtml(" in js

    shell_path = ROOT / "src/tradecraft/web/static/ui_shell.js"
    script = f"""
global.window = {{}};
require({json.dumps(str(shell_path))});
const shell = window.HERMES_UI_SHELL;
const summary = shell.buildSafetySummary({{
  readiness: {{status: "red", blockers: ["kill"], warnings: ["stale"], live_trading_enabled: true}},
  kisStatus: {{execution_mode: "paper", kill_switch: {{enabled: false}}, blocks: [{{status: "active"}}]}},
  binanceStatus: {{execution_mode: "live", kill_switch: {{enabled: true}}, blocks: [{{status: "failed_entry"}}]}},
  authRequired: false,
  hasAdminToken: true,
}});
const resource = shell.normalizeResourceState({{status: "stale", updated_at: "2026-07-10T01:02:03Z"}});
console.log(JSON.stringify({{summary, resource}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["summary"]["tone"] == "bad"
    assert payload["summary"]["mode"] == "LIVE"
    assert payload["summary"]["blockerCount"] == 1
    assert payload["summary"]["kis"]["mode"] == "PAPER"
    assert payload["summary"]["binance"]["killSwitch"] is True
    assert payload["summary"]["binance"]["failedCount"] == 1
    assert payload["resource"]["kind"] == "stale"


def test_ui_shell_reads_nested_venue_execution_and_kill_switch_state() -> None:
    shell_path = ROOT / "src/tradecraft/web/static/ui_shell.js"
    script = f"""
global.window = {{}};
require({json.dumps(str(shell_path))});
const summary = window.HERMES_UI_SHELL.buildSafetySummary({{
  readiness: {{status: "green", blockers: [], warnings: []}},
  kisStatus: {{execution_mode: "paper", summary: {{kill_switch: {{enabled: true}}}}}},
  binanceStatus: {{execution: {{spot_mode: "live", futures_mode: "paper"}}}},
  hasAdminToken: true,
}});
console.log(JSON.stringify(summary));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    summary = json.loads(result.stdout)

    assert summary["mode"] == "LIVE"
    assert summary["kis"]["killSwitch"] is True
    assert summary["binance"]["mode"] == "LIVE"
    assert summary["tone"] == "bad"


def test_kis_and_binance_block_cards_expose_fill_and_adoption_provenance() -> None:
    kis_path = ROOT / "src/tradecraft/web/static/kis_trader_tab.js"
    binance_path = ROOT / "src/tradecraft/web/static/binance_tab.js"
    script = f"""
global.window = {{}};
require({json.dumps(str(kis_path))});
require({json.dumps(str(binance_path))});
const kis = window.HERMES_KIS_TRADER_TAB.renderBlockCard({{
  block_id: "kis-1",
  symbol: "005930",
  status: "open",
  created_by: "existing_position",
  metadata: {{fill_provenance: "exchange_fill"}},
}});
const binance = window.HERMES_BINANCE_TAB.renderBlockCard({{
  block_id: "bn-1",
  symbol: "BTCUSDT",
  status: "failed_entry",
  created_by: "wallet_adoption",
  metadata: {{fill_provenance: "paper_fill"}},
}});
console.log(JSON.stringify({{kis, binance}}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert "기존 보유 채택 · 쥬 진입 성과 제외" in payload["kis"]
    assert "거래소 체결" in payload["kis"]
    assert "Wallet 채택 · 쥬 진입 성과 제외" in payload["binance"]
    assert "Paper 체결" in payload["binance"]
    assert "진입 실패 · 체결 없음" in payload["binance"]


def test_venue_workspaces_separate_overview_active_and_history_surfaces() -> None:
    js = _js()
    shell_js = _ui_shell_js()

    assert "function renderWorkspaceJumpNav(" in shell_js
    assert "renderWorkspaceJumpNav," in shell_js
    assert 'UI_SHELL.renderWorkspaceJumpNav("kis"' in js
    assert 'UI_SHELL.renderWorkspaceJumpNav("binance"' in js
    for marker in (
        'id="kis-workspace-overview"',
        'id="kis-workspace-active"',
        'id="kis-workspace-history"',
        'id="binance-workspace-overview"',
        'id="binance-workspace-active"',
        'id="binance-workspace-history"',
    ):
        assert marker in js
    assert "data-workspace-jump" in shell_js
    assert 'target.closest("[data-workspace-jump]")' in js
    assert "scrollIntoView" in js


def test_auth_prompt_is_compact_until_operator_expands_token_form() -> None:
    html = _html()
    js = _js()
    css = _css()

    assert 'id="authBannerToggleBtn"' in html
    assert 'aria-controls="authTokenControls"' in html
    assert 'id="authTokenControls"' in html
    assert "function setAuthPromptExpanded(" in js
    assert 'bindEvent("authBannerToggleBtn", "click"' in js
    assert ".auth-banner:not(.expanded) .auth-token-controls" in css
    assert ".auth-banner.expanded .auth-token-controls" in css


def test_auth_required_helper_copy_is_scoped_to_active_venue() -> None:
    js = _js()
    start = js.index("function renderAuthRequiredHelperPanel")
    end = js.index("function renderOpsBanner", start)
    body = js[start:end]

    assert "activeHelperTab" in body
    assert 'activeHelperTab === "binance_trader"' in body
    assert "Binance 계좌·블록·실행 정보" in body
    assert "KIS 국장 계좌·블록·장중 판단 정보" in body
    assert "data-auth-scope" in body


def test_kis_quick_strip_is_not_rendered_in_binance_workspace() -> None:
    js = _js()
    start = js.index("function renderKisQuickStrip")
    end = js.index("function renderKisAccountHoldingsPanel", start)
    body = js[start:end]

    assert "showHelperKisStrip" in body
    assert 'state.activeHelperTab === "kis_trader"' in body
    assert 'state.activeHelperTab === "kis_memory"' in body
    assert "target.hidden = id === \"helperKisQuickStrip\" && !showHelperKisStrip" in body


def test_static_shell_exposes_keyboard_and_motion_accessibility_contracts() -> None:
    html = _html()
    js = _js()
    css = _css()

    assert 'class="skip-link"' in html
    assert 'id="mainWorkspace"' in html
    assert 'aria-current="page"' in html
    assert 'aria-expanded="false"' in html
    assert "setAttribute(\"aria-current\"" in js
    assert "setAttribute(\"aria-expanded\"" in js
    assert ":focus-visible" in css
    assert "prefers-reduced-motion: reduce" in css


def test_mobile_operator_header_keeps_status_and_venue_cards_compact() -> None:
    css = _css()
    marker = "Operator shell final responsive overrides"
    start = css.index(marker)
    body = css[start:]

    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in body
    assert ".pillbox > *" in body
    assert "width: auto" in body
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in body
    assert ".home-readiness-card" in body
    assert "grid-column: 1 / -1" in body
