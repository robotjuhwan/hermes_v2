const API = "/api";
const THEME_KEY = "hermes_theme_ai_research_v1";
const UI_STATE_KEY = "hermes_ui_state_v1";
const ACTIVE_BLOCK_REFRESH_MS = 10_000;
const SYSTEM_METRICS_REFRESH_MS = 60_000;
const SYSTEM_METRICS_COLLAPSED_REFRESH_MS = 300_000;
const SYSTEM_METRICS_MIN_REQUEST_GAP_MS = 30_000;
const UI_TABS = window.HERMES_UI_TABS || {};
const UI_SHARED = window.HERMES_UI_SHARED || {};
const UI_FORMATTERS = window.HERMES_UI_FORMATTERS || {};
const UI_OPS = window.HERMES_UI_OPS || {};
const UI_AUTH = window.HERMES_UI_AUTH || {};
const UI_SHELL = window.HERMES_UI_SHELL || {};
const UI_LIVE_AUTHORITY = window.HERMES_UI_LIVE_AUTHORITY || {};
const SYSTEM_METRICS_WIDGET = window.HERMES_SYSTEM_METRICS_WIDGET || {};
const KIS_QUICK_VIEW = window.HERMES_KIS_QUICK_VIEW || {};
const BINANCE_TAB = window.HERMES_BINANCE_TAB || {};
const SETTINGS_TAB = window.HERMES_SETTINGS_TAB || {};
const CRYPTO_RESEARCH_TAB = window.HERMES_CRYPTO_RESEARCH_TAB || {};
const KIS_TRADER_TAB = window.HERMES_KIS_TRADER_TAB || {};
const ETF_TAB = window.HERMES_ETF_TAB || {};
const MEMORY_TAB = window.HERMES_MEMORY_TAB || {};
const STRATEGY_INTEL_TAB = window.HERMES_STRATEGY_INTEL_TAB || {};
const MARKET_JUDGE_TAB = window.HERMES_MARKET_JUDGE_TAB || {};
const RUNTIME_TAB = window.HERMES_RUNTIME_TAB || {};
const REBALANCE_TAB = window.HERMES_REBALANCE_TAB || {};
const BACKTEST_TAB = window.HERMES_BACKTEST_TAB || {};
const ACTIVE_BLOCK_TABS = new Set(UI_TABS.activeBlockTabs || ["kis_trader", "binance_trader"]);
const ASK_HELPER_TAB = UI_TABS.defaultHelperTab || "ask";
const EVIDENCE_POLICY_CONTEXT_PATH = "/evidence-policy/context";
const {
  asNumber,
  escapeHTML,
  fmtBytes,
  fmtDurationSec,
  fmtKRW,
  fmtKST,
  fmtMaybeKRW,
  fmtNum,
  fmtPercent,
  fmtUSDT,
  formatLiveMultiplier,
  truncateWithEllipsis,
} = UI_FORMATTERS;
const {
  costEvidenceTone,
  formatCostEvidenceLabel,
  formatOpsRestartProcessSummary,
  formatOpsSignalLabel,
  formatOpsSignalList,
  formatRiskGovernorLabel,
  formatRiskGovernorSourceLabel,
  formatValidationEvidenceLabel,
  formatValidationGateLabel,
  formatValidationGateReason,
  renderOpsRemediationActions,
  renderOpsAdvisoryDetails,
  renderTradingValidationBottleneckSummary,
  tradingValidationTone,
  validationEvidenceTone,
} = UI_OPS;
const HELPER_TABS = new Set(UI_TABS.helperTabs || [
  "research",
  "strategy_intel",
  "kis_memory",
  "binance_memory",
  "jue_wiki",
  "market_judge",
  "ask",
  "runtime",
  "settings",
  "rebalance",
  "kis_trader",
  "binance_trader",
  "crypto_research",
  "reports",
]);
const state = {
  dashboard: null,
  dashboardLiveRefreshInFlight: false,
  activeVenueId: "all",
  activePage: "main",
  mobileMenuOpen: false,
  view: "dashboard",
  activeHelperTab: "ask",
  kisBlockStatus: null,
  kisBlockLoading: false,
  kisBlockError: "",
  liveAuthority: null,
  liveAuthorityError: "",
  kisBlockHistory: {
    date: "",
    status: "inactive",
    horizon: "all",
    query: "",
    selectedBlockId: "",
  },
  binanceTrader: {
    status: null,
    quantSignals: [],
    quantError: "",
    patternContext: null,
    patternError: "",
    historyDate: "",
    historyStatus: "closed_error",
    historyLane: "all",
    historyQuery: "",
    loading: false,
    running: false,
    error: "",
  },
  cryptoResearch: {
    status: null,
    context: null,
    loading: false,
    running: false,
    error: "",
    result: null,
  },
  cryptoAlpha: {
    status: null,
    context: null,
    loading: false,
    running: false,
    error: "",
    result: null,
  },
  evidencePolicy: {
    status: null,
    context: null,
    loading: false,
    error: "",
  },
  etfResearch: {
    status: null,
    candidates: null,
    loading: false,
    running: false,
    error: "",
  },
  dailyDiscovery: null,
  dailyDiscoveryLoading: false,
  dailyDiscoveryRunning: false,
  dailyDiscoveryError: "",
  investmentMemory: null,
  investmentMemoryScope: "",
  investmentMemoryLoading: false,
  jueSourceManifest: null,
  jueLifecycleLatest: null,
  investmentMemoryError: "",
  jueSourceManifestError: "",
  jueLifecycleError: "",
  investmentMemoryRunning: false,
  jueWikiStatus: null,
  jueWikiContext: null,
  jueWikiSearch: null,
  jueWikiFindings: null,
  jueWikiRepair: null,
  jueWikiApplicationStatus: null,
  jueWikiApplicationEffectiveness: null,
  jueWikiScope: "kis",
  jueWikiSearchQuery: "",
  jueWikiSelectedPageId: "",
  jueWikiLoading: false,
  jueWikiRepairRunning: false,
  jueWikiError: "",
  memoryReviews: null,
  memoryRevisions: null,
  memoryReviewRunning: false,
  memoryReviewError: "",
  reportsStatus: null,
  reportsError: "",
  reportsLoading: false,
  runtimeStorage: null,
  runtimeStorageError: "",
  runtimeStorageLoading: false,
  runtimeStorageCleanup: {
    running: false,
    result: null,
    error: "",
  },
  rebalanceStatus: null,
  rebalanceError: "",
  healthStatus: null,
  healthError: "",
  opsReadiness: null,
  opsReadinessError: "",
  systemMetrics: {
    payload: null,
    error: "",
    loading: false,
    collapsed: true,
    timer: null,
    inFlight: false,
    lastRequestAt: 0,
  },
  llmUsage: null,
  llmUsageError: "",
  llmUsagePeriod: "today",
  settingsPage: {
    catalog: null,
    jueWorkflowStatus: null,
    codexNativeStatus: null,
    loading: false,
    jueWorkflowLoading: false,
    codexNativeLoading: false,
    saving: false,
    restarting: false,
    error: "",
    jueWorkflowError: "",
    codexNativeError: "",
    saveResult: null,
    restartResult: null,
    filter: "",
    category: "all",
    draft: {},
  },
  theme: "dark",
  auth: {
    token: "",
    required: false,
    message: "",
    expanded: false,
  },
  lastRenderedWebhookMessage: "",
  helperAsk: {
    query: "",
    symbol: "",
    loading: false,
    error: "",
    result: null,
  },
  strategyIntel: {
    query: "다음 거래일 관심 후보를 전략적으로 정리해줘",
    loading: false,
    collectLoading: false,
    valuationCollectLoading: false,
    error: "",
    collectError: "",
    valuationCollectError: "",
    collectResult: null,
    valuationCollectResult: null,
    result: null,
    selectedSymbol: "",
  },
  symbolAnalysis: {
    input: "",
    loading: false,
    running: false,
    error: "",
    specialWatch: null,
    history: null,
    result: null,
    selectedHistoryIndex: null,
  },
  marketJudge: {
    loading: false,
    running: false,
    error: "",
    result: null,
  },
  marketPulse: {
    loading: false,
    running: false,
    error: "",
    result: null,
  },
  candidateCoverage: null,
  exitQuality: null,
  marketRiskCap: null,
  helperDetailRegistry: {},
  helperDetailSeq: 0,
  helperDetailModal: null,
  backtest: {
    selectedSessionIds: [],
    scenarios: [],
    status: null,
    dataStatus: null,
    pollTimer: null,
  },
  activeRefresh: {
    timer: null,
    inFlight: false,
    lastAt: "",
    error: "",
  },
};

function qs(id) {
  return document.getElementById(id);
}

function statusSaysLive(payload) {
  if (!payload || typeof payload !== "object") return false;
  const mode = String(payload.execution_mode || payload.execution?.mode || "").toLowerCase();
  return payload.execute_orders === true || mode === "live" || mode === "real";
}

function statusSaysPaper(payload) {
  if (!payload || typeof payload !== "object") return false;
  const mode = String(payload.execution_mode || payload.execution?.mode || "").toLowerCase();
  return payload.execute_orders === false || mode === "paper" || mode === "dry_run";
}

function binanceLiveExecutionEnabled() {
  const sources = [
    state.binanceTrader.status?.readiness?.execution,
    state.binanceTrader.status?.execution,
    state.opsReadiness?.binance_block_trader?.execution,
  ].filter((source) => source && typeof source === "object");
  return sources.some((execution) => (
    ["spot_mode", "futures_mode", "upbit_spot_mode"].some((key) => (
      String(execution[key] || "").trim().toLowerCase() === "live"
    ))
  ));
}

function confirmBinanceLiveManualAction(label) {
  if (!binanceLiveExecutionEnabled()) return true;
  return window.confirm(
    `Binance ${label}은 live crypto execution 상태에서 주문을 만들거나 체결할 수 있습니다. 계속할까요?`
  );
}

function renderGlobalExecutionMode() {
  const node = qs("globalExecutionModeText");
  if (!node) return;
  const live = Boolean(state.opsReadiness?.live_trading_enabled)
    || statusSaysLive(state.kisBlockStatus)
    || statusSaysLive(state.binanceTrader.status);
  if (live) {
    node.textContent = "LIVE · 실주문 활성";
    return;
  }
  const knownPaper = statusSaysPaper(state.kisBlockStatus)
    || statusSaysPaper(state.binanceTrader.status)
    || state.opsReadiness?.live_trading_enabled === false;
  node.textContent = knownPaper ? "Paper · 실주문 잠금" : "상태 확인 중";
}

function renderHomeOpsSummary() {
  const root = qs("homeOpsSummary");
  if (!root || typeof UI_SHELL.buildSafetySummary !== "function" || typeof UI_SHELL.renderHomeOpsSummaryHtml !== "function") {
    return;
  }
  const summary = UI_SHELL.buildSafetySummary({
    readiness: state.opsReadiness,
    kisStatus: state.kisBlockStatus,
    binanceStatus: state.binanceTrader.status,
    authRequired: state.auth.required,
    hasAdminToken: hasAdminToken(),
  });
  root.innerHTML = UI_SHELL.renderHomeOpsSummaryHtml(summary, { escapeHTML });
  root.dataset.tone = summary.tone;
}

function bindEvent(id, eventName, handler) {
  const node = qs(id);
  if (!node) return;
  node.addEventListener(eventName, handler);
}

function saveUiState() {
  const nextHash = state.activePage === "helper" && HELPER_TABS.has(state.activeHelperTab)
    ? `#helper/${state.activeHelperTab}`
    : "#main";
  if (window.location.hash !== nextHash) {
    window.history.replaceState(null, "", nextHash);
  }
  try {
    window.localStorage.setItem(
      UI_STATE_KEY,
      JSON.stringify({
        activePage: state.activePage,
        activeHelperTab: state.activeHelperTab,
        activeVenueId: state.activeVenueId,
        systemMetricsCollapsed: state.systemMetrics.collapsed,
        view: state.view,
      })
    );
  } catch (_) {
    // localStorage can be unavailable in restricted browser modes.
  }
}

function restoreUiState() {
  const hash = String(window.location.hash || "").replace(/^#/, "");
  const [hashPage, hashTab] = hash.split("/");
  const hasHashState = hashPage === "main" || (hashPage === "helper" && HELPER_TABS.has(hashTab));
  if (hasHashState) {
    state.activePage = hashPage === "helper" ? "helper" : "main";
    state.activeHelperTab = hashPage === "helper" ? hashTab : ASK_HELPER_TAB;
  }
  try {
    const raw = window.localStorage.getItem(UI_STATE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const page = String(parsed.activePage || "");
    const tab = String(parsed.activeHelperTab || "");
    if (!hasHashState && page === "helper" && HELPER_TABS.has(tab)) {
      state.activePage = "helper";
      state.activeHelperTab = tab;
    } else if (!hasHashState) {
      state.activePage = "main";
    }
    if (parsed.activeVenueId) {
      state.activeVenueId = String(parsed.activeVenueId);
    }
    if (parsed.view === "backtest" || parsed.view === "dashboard") {
      state.view = parsed.view;
    }
    if (typeof parsed.systemMetricsCollapsed === "boolean") {
      state.systemMetrics.collapsed = parsed.systemMetricsCollapsed;
    }
  } catch (_) {
    state.activePage = "main";
    state.activeHelperTab = ASK_HELPER_TAB;
  }
}

function resolveInitialHelperTab(fallback = ASK_HELPER_TAB) {
  const hash = String(window.location.hash || "").replace(/^#/, "");
  if (hash.startsWith("helper/")) {
    const tab = hash.split("/")[1] || "";
    if (HELPER_TABS.has(tab)) return tab;
  }
  const stored = String(state.activeHelperTab || fallback || ASK_HELPER_TAB);
  return HELPER_TABS.has(stored) ? stored : ASK_HELPER_TAB;
}

function isMemoryTab(tab = state.activeHelperTab) {
  return tab === "kis_memory" || tab === "binance_memory";
}

function memoryScopeForTab(tab = state.activeHelperTab) {
  return tab === "binance_memory" ? "binance" : "kis";
}

function memoryTodayPath(scope = memoryScopeForTab()) {
  return `/memory/today?scope=${encodeURIComponent(scope)}&compact=true`;
}

function getInitialTheme() {
  const saved = window.localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return "dark";
}

function applyTheme(theme) {
  state.theme = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = state.theme;
  window.localStorage.setItem(THEME_KEY, state.theme);
  const button = qs("themeToggle");
  if (button) {
    button.textContent = state.theme === "dark" ? "라이트 모드" : "다크 모드";
  }
}

function toggleTheme() {
  applyTheme(state.theme === "dark" ? "light" : "dark");
}

function setActiveView(view) {
  state.view = view === "backtest" ? "backtest" : "dashboard";
  const dashboardPane = qs("dashboardPane");
  const backtestPane = qs("backtestPane");
  const dashboardBtn = qs("viewDashboardBtn");
  const backtestBtn = qs("viewBacktestBtn");

  if (dashboardPane && backtestPane) {
    dashboardPane.classList.toggle("hidden", state.view !== "dashboard");
    backtestPane.classList.toggle("hidden", state.view !== "backtest");
  }
  if (dashboardBtn && backtestBtn) {
    dashboardBtn.classList.toggle("active", state.view === "dashboard");
    backtestBtn.classList.toggle("active", state.view === "backtest");
  }
}

function setHealth(text, ok = true) {
  const pill = qs("healthPill");
  pill.textContent = text;
  pill.style.color = ok ? "var(--status-ok)" : "var(--status-bad)";
}

function readAdminToken() {
  return typeof UI_AUTH.readAdminToken === "function" ? UI_AUTH.readAdminToken() : "";
}

function writeAdminToken(token) {
  if (typeof UI_AUTH.writeAdminToken === "function") UI_AUTH.writeAdminToken(token);
}

function hasAdminToken() {
  return Boolean(String(state.auth.token || readAdminToken() || "").trim());
}

function adminAuthHeaders() {
  const token = String(state.auth.token || readAdminToken() || "").trim();
  return typeof UI_AUTH.adminAuthHeaders === "function" ? UI_AUTH.adminAuthHeaders(token) : {};
}

function requestHasAdminToken(headers) {
  return typeof UI_AUTH.requestHasAdminToken === "function" ? UI_AUTH.requestHasAdminToken(headers) : false;
}

function isProtectedApiPath(path) {
  return typeof UI_AUTH.isProtectedApiPath === "function" ? UI_AUTH.isProtectedApiPath(path) : false;
}

function isAuthError(error) {
  return Boolean(error?.authRequired);
}

function markAuthRequired(message = "운영 토큰 필요") {
  state.auth.required = true;
  state.auth.message = message;
  setHealth("Auth required", false);
  renderAuthPrompt();
}

function clearAuthRequired() {
  state.auth.required = false;
  state.auth.message = "";
  setHealth("API online", true);
  renderAuthPrompt();
}

function renderAuthPrompt() {
  const banner = qs("authBanner");
  const message = qs("authMessage");
  const tokenInput = qs("authTokenInput");
  if (!banner) return;
  banner.hidden = !state.auth.required;
  if (message) {
    message.textContent = state.auth.message || "운영 토큰이 필요한 요청입니다.";
  }
  if (tokenInput && document.activeElement !== tokenInput) {
    tokenInput.value = String(state.auth.token || readAdminToken() || "");
  }
  setAuthPromptExpanded(state.auth.required && state.auth.expanded);
  renderAuthGatedDashboardShell();
  if (typeof renderHomeOpsSummary === "function") renderHomeOpsSummary();
  renderHelperAgent();
}

function setAuthPromptExpanded(expanded, options = {}) {
  const banner = qs("authBanner");
  const toggle = qs("authBannerToggleBtn");
  const next = Boolean(expanded && state.auth.required);
  state.auth.expanded = next;
  if (banner?.classList) banner.classList.toggle("expanded", next);
  if (toggle) {
    toggle.setAttribute("aria-expanded", String(next));
    toggle.textContent = next ? "입력 닫기" : "토큰 입력";
  }
  if (next && options.focus) {
    const input = qs("authTokenInput");
    input?.focus();
    input?.select?.();
  }
}

function focusAuthTokenInput() {
  const input = qs("authTokenInput");
  if (!input) return;
  setAuthPromptExpanded(true, { focus: true });
}

function renderAuthGatedDashboardShell() {
  if (!state.auth.required || state.dashboard) return;
  const message = state.auth.message || "운영 토큰이 필요한 요청입니다.";
  const sessionHint = "서버/KIS 데이터 없음이 아니라 보호 API 인증 대기 상태입니다. 국장 데이터는 정상 수집 중이어도 브라우저 세션 토큰이 없으면 숨겨집니다. 토큰은 이 브라우저 세션에만 저장되므로 재부팅, 새 브라우저, 세션 삭제 후에는 다시 입력해야 합니다.";
  const kisQuickStrip = qs("kisQuickStrip");
  if (kisQuickStrip) {
    kisQuickStrip.innerHTML = `
      <article class="kis-quick-card muted auth-gated">
        <div>
          <span class="section-kicker">KIS</span>
          <strong>국장 계좌 인증 대기</strong>
        </div>
        <p>${escapeHTML(sessionHint)} ${escapeHTML(message)} 상단 토큰 입력 후 국장 현금, 보유종목, 블록 상태를 다시 불러옵니다.</p>
        <button class="btn small warm" type="button" data-auth-focus="true">운영 토큰 입력</button>
      </article>
    `;
  }
  const venueTabs = qs("venueTabs");
  if (venueTabs) {
    venueTabs.innerHTML = `<span class="strategy-data-chip">운영 토큰 입력 후 시장별 잔고 표시</span>`;
  }
  const activeMarketBadge = qs("activeMarketBadge");
  if (activeMarketBadge) {
    activeMarketBadge.textContent = "인증 대기";
  }
  const assetsBody = qs("assetsBody");
  if (assetsBody) {
    assetsBody.innerHTML = `
      <tr>
        <td colspan="9" class="table-empty-cell">
          ${escapeHTML(sessionHint)} ${escapeHTML(message)} 토큰 인증 후 국장 계좌 상세가 표시됩니다.
        </td>
      </tr>
    `;
  }
}

function renderAuthRequiredHelperPanel(activeHelperTab = state.activeHelperTab) {
  const message = state.auth.message || "운영 토큰이 필요한 요청입니다.";
  const isBinance = activeHelperTab === "binance_trader";
  const isKis = activeHelperTab === "kis_trader" || activeHelperTab === "kis_memory";
  const scope = isBinance ? "binance" : isKis ? "kis" : "operations";
  const title = isBinance ? "Binance 운영 토큰 필요" : isKis ? "KIS 운영 토큰 필요" : "운영 토큰 필요";
  const protectedText = isBinance
    ? "Binance 계좌·블록·실행 정보는 보호 API라 인증 후 표시됩니다."
    : isKis
      ? "KIS 국장 계좌·블록·장중 판단 정보는 보호 API라 인증 후 표시됩니다."
      : "보호된 투자 운영 정보는 인증 후 표시됩니다.";
  const chips = isBinance
    ? ["Binance 계좌 보호됨", "현물·선물 블록 보호됨", "실행 상태 보호됨"]
    : isKis
      ? ["KIS 국장 계좌 보호됨", "국장 블록 보호됨", "장중 판단 보호됨"]
      : ["계좌 정보 보호됨", "블록 트레이딩 보호됨", "운영 상태 보호됨"];
  return `
    <section class="memory-section" data-auth-scope="${escapeHTML(scope)}">
      <div class="panel-head compact">
        <div>
          <h3>${escapeHTML(title)}</h3>
          <p>${escapeHTML(protectedText)}</p>
        </div>
      </div>
      <div class="notice warn">
        ${escapeHTML(message)} 상단의 토큰 입력을 열어 인증하면 이 작업공간을 다시 불러옵니다.
      </div>
      <div class="strategy-data-strip">
        ${chips.map((chip) => `<span class="strategy-data-chip warn">${escapeHTML(chip)}</span>`).join("")}
      </div>
      <button class="btn small warm" type="button" data-auth-focus="true">운영 토큰 입력하기</button>
    </section>
  `;
}

function renderOpsBanner() {
  const banner = qs("opsBanner");
  if (!banner) return;
  const readiness = state.opsReadiness;
  const error = state.opsReadinessError;
  if (!readiness && !error) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  if (error) {
    banner.hidden = false;
    banner.className = "ops-banner warn";
    banner.innerHTML = `
      <strong>운영 상태 확인 실패</strong>
      <span>${escapeHTML(error)}</span>
    `;
    return;
  }
  const warnings = Array.isArray(readiness.warnings) ? readiness.warnings : [];
  const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
  const status = String(readiness.status || "yellow");
  banner.hidden = blockers.length === 0 && warnings.length === 0;
  if (banner.hidden) {
    banner.innerHTML = "";
    return;
  }
  const liveText = readiness.live_trading_enabled ? "실주문 활성" : "Paper/실주문 비활성";
  const disk = readiness.disk_space && typeof readiness.disk_space === "object" ? readiness.disk_space : {};
  const diskText = disk.free_bytes === undefined
    ? "디스크 -"
    : `디스크 ${fmtBytes(disk.free_bytes)} 여유`;
  const chips = [
    liveText,
    readiness.memory?.seeded ? "메모리 seed 완료" : "메모리 seed 필요",
    readiness.market_judge?.enabled ? "Market judge on" : "Market judge off",
    readiness.market_pulse?.enabled ? "Market pulse on" : "Market pulse off",
    diskText,
    warnings.includes("restart_required") ? "재시작 필요" : "프로세스 최신",
  ];
  banner.className = `ops-banner ${status === "red" ? "bad" : status === "green" ? "good" : "warn"}`;
  const restartText = formatOpsRestartProcessSummary(readiness, 4);
  const baseSignalText = formatOpsSignalList([...blockers, ...warnings]);
  const signalText = restartText ? `${baseSignalText} · ${restartText}` : baseSignalText;
  const remediationHtml = renderOpsRemediationActions(readiness.operational_remediation_actions, 3);
  const bannerTitle = status === "red"
    ? "운영 차단"
    : "운영 점검 필요";
  banner.innerHTML = `
    <div>
      <strong>${escapeHTML(bannerTitle)}</strong>
      <span>${escapeHTML(signalText)}</span>
      ${remediationHtml}
    </div>
    <div class="ops-chip-row">
      ${chips.map((chip) => `<span class="strategy-data-chip">${escapeHTML(chip)}</span>`).join("")}
    </div>
  `;
}

function metricTone(value, warnAt, badAt) {
  const numeric = Number(value || 0);
  if (numeric >= badAt) return "bad";
  if (numeric >= warnAt) return "warn";
  return "good";
}

function renderSystemMetricsWidget() {
  const widget = qs("systemMetricsWidget");
  if (!widget) return;
  const renderer = SYSTEM_METRICS_WIDGET.renderSystemMetricsWidget;
  if (typeof renderer !== "function") {
    widget.className = "system-metrics-widget collapsed warn";
    widget.innerHTML = `
      <button class="system-metrics-summary" type="button" data-system-metrics-action="toggle">
        <span>SYS</span>
        <strong>위젯 대기</strong>
        <small>렌더러 로드 중</small>
      </button>
    `;
    return;
  }
  const rendered = renderer({
    payload: state.systemMetrics.payload || {},
    error: state.systemMetrics.error || "",
    collapsed: Boolean(state.systemMetrics.collapsed),
    hasAdminToken: hasAdminToken(),
  });
  widget.className = rendered.className || "system-metrics-widget";
  widget.innerHTML = rendered.html || "";
}

function shouldPollSystemMetrics() {
  return hasAdminToken() && document.visibilityState !== "hidden";
}

function systemMetricsRefreshIntervalMs() {
  return state.systemMetrics.collapsed ? SYSTEM_METRICS_COLLAPSED_REFRESH_MS : SYSTEM_METRICS_REFRESH_MS;
}

async function loadSystemMetrics({ silent = false, force = false } = {}) {
  if (!shouldPollSystemMetrics()) {
    renderSystemMetricsWidget();
    return;
  }
  if (state.systemMetrics.inFlight) return;
  const now = Date.now();
  if (
    !force
    && silent
    && state.systemMetrics.lastRequestAt
    && now - state.systemMetrics.lastRequestAt < SYSTEM_METRICS_MIN_REQUEST_GAP_MS
  ) {
    return;
  }
  state.systemMetrics.inFlight = true;
  state.systemMetrics.lastRequestAt = now;
  if (!silent) {
    state.systemMetrics.loading = true;
    renderSystemMetricsWidget();
  }
  try {
    state.systemMetrics.payload = await getJSON("/ops/system-metrics");
    state.systemMetrics.error = "";
  } catch (error) {
    state.systemMetrics.error = getErrorMessage(error);
  } finally {
    state.systemMetrics.inFlight = false;
    state.systemMetrics.loading = false;
    renderSystemMetricsWidget();
  }
}

function syncSystemMetricsRefresh() {
  if (state.systemMetrics.timer) {
    window.clearInterval(state.systemMetrics.timer);
    state.systemMetrics.timer = null;
  }
  renderSystemMetricsWidget();
  if (!shouldPollSystemMetrics()) return;
  loadSystemMetrics({ silent: true });
  state.systemMetrics.timer = window.setInterval(() => {
    loadSystemMetrics({ silent: true });
  }, systemMetricsRefreshIntervalMs());
}

function venueDisplayDefaults(id) {
  const defaults = {
    kr_stock: { label: "국장1", market: "KRX" },
    kr_stock_2: { label: "국장2", market: "KRX" },
    us_stock: { label: "미장", market: "US" },
    upbit: { label: "Upbit", market: "KRW Crypto" },
    bithumb: { label: "Bithumb", market: "KRW Crypto" },
    binance: { label: "Binance Spot", market: "USDT Crypto" },
    binance_futures: { label: "Binance Futures", market: "USDT-M" },
  };
  return defaults[String(id || "")] || {};
}

function normalizeVenueForDisplay(venue) {
  const row = venue && typeof venue === "object" ? { ...venue } : {};
  const id = String(row.id || "");
  const defaults = venueDisplayDefaults(id);
  const label = String(row.label || "").trim();
  const name = String(row.name || "").trim();
  const market = String(row.market || "").trim();
  const genericKisLabels = new Set(["국장", "국장(2번)", "KIS", "KRX"]);
  row.label = defaults.label && genericKisLabels.has(label)
    ? defaults.label
    : label || defaults.label || name || id || "시장";
  row.market = market || defaults.market || "연동 시장";
  return row;
}

function orderedVenuesForDisplay(venues) {
  const rows = Array.isArray(venues) ? venues.map(normalizeVenueForDisplay) : [];
  const priority = new Map([
    ["kr_stock", 0],
    ["kr_stock_2", 1],
    ["us_stock", 2],
    ["upbit", 3],
    ["bithumb", 4],
    ["binance", 5],
    ["binance_futures", 6],
  ]);
  return rows.sort((a, b) => {
    const left = priority.has(a?.id) ? priority.get(a.id) : 50;
    const right = priority.has(b?.id) ? priority.get(b.id) : 50;
    if (left !== right) return left - right;
    return String(a?.label || a?.id || "").localeCompare(String(b?.label || b?.id || ""), "ko");
  });
}

function deriveAllVenue(venues) {
  const all = {
    id: "all",
    label: "전체",
    market: "통합 보기",
    cash_krw: 0,
    invested_krw: 0,
    unrealized_pnl_krw: 0,
    total_krw: 0,
    assets: [],
  };

  for (const venue of venues) {
    all.cash_krw += Number(venue.cash_krw || 0);
    all.invested_krw += Number(venue.invested_krw || 0);
    all.unrealized_pnl_krw += Number(venue.unrealized_pnl_krw || 0);
    all.total_krw += Number(venue.total_krw || 0);

    for (const asset of venue.assets || []) {
      all.assets.push({ ...asset, venue_label: venue.label });
    }
  }
  return all;
}

function getActiveVenue() {
  const venues = state.dashboard?.venues || [];
  if (state.activeVenueId === "all") {
    return deriveAllVenue(orderedVenuesForDisplay(venues));
  }
  return venues.find((item) => item.id === state.activeVenueId) || deriveAllVenue(venues);
}

function getActiveSessions() {
  const sessions = state.dashboard?.sessions || [];
  if (state.activeVenueId === "all") {
    return sessions;
  }
  return sessions.filter((item) => item.venue_id === state.activeVenueId);
}

function renderBacktestSessionOptions() {
  const list = qs("btSessionList");
  if (!list) return;
  const sessions = state.dashboard?.sessions || [];
  const uniqueRows = [];
  const seen = new Set();
  for (const row of sessions) {
    const id = String(row.session_id || "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    uniqueRows.push(row);
  }

  if (!state.backtest.selectedSessionIds.length) {
    state.backtest.selectedSessionIds = uniqueRows.map((row) => String(row.session_id));
  } else {
    const valid = new Set(uniqueRows.map((row) => String(row.session_id)));
    state.backtest.selectedSessionIds = state.backtest.selectedSessionIds.filter((id) => valid.has(id));
    if (!state.backtest.selectedSessionIds.length) {
      state.backtest.selectedSessionIds = uniqueRows.map((row) => String(row.session_id));
    }
  }

  const selected = new Set(state.backtest.selectedSessionIds);
  list.innerHTML = uniqueRows
    .map((row) => {
      const id = String(row.session_id || "-");
      const checked = selected.has(id) ? "checked" : "";
      const label = `${row.venue_label || row.venue_id || "-"} · ${row.name || row.mode || "-"}`;
      return `
        <label class="bt-session-item">
          <input class="bt-session-check" type="checkbox" value="${escapeHTML(id)}" ${checked} />
          <span>${escapeHTML(id)}</span>
          <span class="session-mode">${escapeHTML(label)}</span>
        </label>
      `;
    })
    .join("");
}

function renderTopMetrics() {
  const data = state.dashboard;
  if (!data) return;
  const venues = Array.isArray(data.venues) ? data.venues : [];
  const spouseVenue = venues.find((item) => item.id === "kr_stock_2") || null;
  const ownVenues = venues.filter((item) => item.id !== "kr_stock_2");

  const ownTotals = ownVenues.reduce(
    (acc, venue) => {
      acc.total += Number(venue.total_krw || 0);
      acc.cash += Number(venue.cash_krw || 0);
      acc.invested += Number(venue.invested_krw || 0);
      acc.pnl += Number(venue.unrealized_pnl_krw || 0);
      return acc;
    },
    { total: 0, cash: 0, invested: 0, pnl: 0 }
  );

  qs("clockPill").textContent = `KST ${fmtKST(data.clock_utc, true)}`;
  qs("totalAssetValue").textContent = `${fmtKRW(ownTotals.total)} KRW`;
  qs("cashAssetValue").textContent = `${fmtKRW(ownTotals.cash)} KRW`;
  qs("investedAssetValue").textContent = `${fmtKRW(ownTotals.invested)} KRW`;
  qs("unrealizedPnlValue").textContent = `${fmtKRW(ownTotals.pnl)} KRW`;
  qs("unrealizedPnlValue").className = ownTotals.pnl >= 0 ? "gain" : "loss";

  const spouseTotal = Number(spouseVenue?.total_krw || 0);
  const spouseCash = Number(spouseVenue?.cash_krw || 0);
  const spouseInvested = Number(spouseVenue?.invested_krw || 0);
  const spousePnl = Number(spouseVenue?.unrealized_pnl_krw || 0);
  qs("spouseAssetValue").textContent = `${fmtKRW(spouseTotal)} KRW`;
  qs("spouseAssetDetail").textContent = spouseVenue
    ? `현금 ${fmtKRW(spouseCash)} | 투자 ${fmtKRW(spouseInvested)} | 손익 ${fmtKRW(spousePnl)}`
    : "국장(2번) 미연동";

  qs("venueCountValue").textContent = `${ownVenues.length} markets`;
  renderFxPills(data.fx);
}

function kisQuickViewOptions() {
  return { escapeHTML, fmtKRW, fmtNum, orderedVenuesForDisplay };
}

function kisAccountNumber(...values) {
  for (const value of values) {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric;
  }
  return 0;
}

function kisAccountPositionAsset(position) {
  const row = position && typeof position === "object" ? position : {};
  const symbol = String(row.symbol || row.asset || "").trim();
  const name = String(row.name || row.asset_name || symbol || "-").trim();
  const qty = kisAccountNumber(row.qty, row.quantity, row.available_qty);
  return {
    asset: symbol,
    symbol,
    asset_name: name,
    kind: "stock",
    qty,
    available: kisAccountNumber(row.available_qty, qty),
    locked: 0,
    avg_price: kisAccountNumber(row.avg_price, row.average_price),
    mark_price: kisAccountNumber(row.mark_price, row.current_price),
    value_krw: kisAccountNumber(row.value_krw, row.market_value_krw, row.evaluation_amount_krw),
    pnl_krw: kisAccountNumber(row.unrealized_pnl_krw, row.pnl_krw),
  };
}

function kisAccountVenueFromBlockStatus(kisBlockStatus) {
  const account = kisBlockStatus?.account && typeof kisBlockStatus.account === "object"
    ? kisBlockStatus.account
    : null;
  if (!account || String(account.status || "").toLowerCase() !== "ok") return null;

  const cash = kisAccountNumber(account.cash_krw, account.orderable_cash_krw);
  const orderableCash = kisAccountNumber(account.orderable_cash_krw, cash);
  const positionAssets = Array.isArray(account.positions)
    ? account.positions.map(kisAccountPositionAsset).filter((asset) => asset.asset || asset.asset_name)
    : [];
  const positionValue = kisAccountNumber(
    account.position_value_krw,
    positionAssets.reduce((sum, asset) => sum + kisAccountNumber(asset.value_krw), 0)
  );
  const total = kisAccountNumber(account.total_value_krw, account.total_asset_krw, cash + positionValue);
  return {
    id: "kr_stock",
    label: account.account_label || "국장1",
    market: "KRX",
    assets: [
      {
        asset: "KRW",
        asset_name: "KRW",
        kind: "cash",
        qty: cash,
        available: orderableCash,
        locked: 0,
        avg_price: 1,
        mark_price: 1,
        value_krw: cash,
        pnl_krw: 0,
      },
      ...positionAssets,
    ],
    cash_krw: cash,
    invested_krw: positionValue,
    unrealized_pnl_krw: kisAccountNumber(account.unrealized_pnl_krw),
    total_krw: total,
    computed_total_krw: total,
    broker_total_krw: total,
    total_value_basis: "broker_net_asset",
    cache_status: "kis_blocks_account_fallback",
    position_count: kisAccountNumber(account.position_count, positionAssets.length),
  };
}

function kisQuickVenuesForDisplay(dashboardVenues, kisBlockStatus) {
  const moduleFn = typeof KIS_QUICK_VIEW !== "undefined" && typeof KIS_QUICK_VIEW.kisQuickVenuesForDisplay === "function"
    ? KIS_QUICK_VIEW.kisQuickVenuesForDisplay
    : null;
  if (moduleFn) {
    return moduleFn(dashboardVenues, kisBlockStatus, kisQuickViewOptions());
  }

  const venues = orderedVenuesForDisplay(dashboardVenues || []).filter((item) =>
    ["kr_stock", "kr_stock_2"].includes(String(item.id || ""))
  );
  if (venues.length) return venues;

  const account = kisBlockStatus?.account && typeof kisBlockStatus.account === "object"
    ? kisBlockStatus.account
    : null;
  if (!account || String(account.status || "").toLowerCase() !== "ok") return [];

  const accountVenue = kisAccountVenueFromBlockStatus(kisBlockStatus);
  return accountVenue ? [accountVenue] : [];
}

function syncDashboardKisVenueFromBlockStatus(kisBlockStatus) {
  if (!state.dashboard || typeof state.dashboard !== "object") return false;
  const accountVenue = kisQuickVenuesForDisplay([], kisBlockStatus).find((venue) =>
    String(venue?.id || "") === "kr_stock"
  );
  if (!accountVenue) return false;
  const venues = Array.isArray(state.dashboard.venues) ? [...state.dashboard.venues] : [];
  const index = venues.findIndex((venue) => String(venue?.id || "") === "kr_stock");
  const normalized = normalizeVenueForDisplay(accountVenue);
  if (index >= 0) {
    venues[index] = {
      ...venues[index],
      ...normalized,
      assets: Array.isArray(normalized.assets) ? normalized.assets : venues[index].assets || [],
    };
  } else {
    venues.push(normalized);
  }
  state.dashboard = {
    ...state.dashboard,
    venues: orderedVenuesForDisplay(venues),
  };
  return true;
}

function renderKisQuickStrip() {
  const targets = ["kisQuickStrip", "helperKisQuickStrip"]
    .map((id) => [id, qs(id)])
    .filter(([, target]) => Boolean(target));
  if (!targets.length) return;
  const showHelperKisStrip = state.activePage === "helper" && (
    state.activeHelperTab === "kis_trader"
    || state.activeHelperTab === "kis_memory"
  );
  const renderer = typeof KIS_QUICK_VIEW !== "undefined" && typeof KIS_QUICK_VIEW.renderKisQuickStripHtml === "function"
    ? KIS_QUICK_VIEW.renderKisQuickStripHtml
    : null;
  let html = "";
  if (renderer) {
    html = renderer({
      authRequired: Boolean(state.auth?.required),
      hasAdminToken: hasAdminToken(),
      authMessage: state.auth?.message || "운영 토큰이 필요한 요청입니다.",
      dashboardVenues: state.dashboard?.venues || [],
      kisBlockStatus: state.kisBlockStatus,
    }, kisQuickViewOptions());
  } else if (!hasAdminToken()) {
    const message = state.auth?.required
      ? state.auth.message || "운영 토큰이 필요한 요청입니다."
      : "국장/블록 API는 보호 API라 이 브라우저 세션의 운영 토큰이 필요합니다.";
    html = `
      <article class="kis-quick-card muted auth-gated">
        <div>
          <span class="section-kicker">KIS</span>
          <strong>국장 계좌 인증 대기</strong>
        </div>
        <p>KIS 장애나 국장 데이터 공백이 아니라 보호 API 인증 대기입니다. 브라우저 세션에 운영 토큰이 없어 국장 현금, 보유종목, 블록 상태를 숨겼습니다. 토큰은 이 브라우저 세션에만 저장되므로 재부팅, 새 브라우저, 세션 삭제 후에는 다시 입력해야 합니다. ${escapeHTML(message)}</p>
        <button class="btn small warm" type="button" data-auth-focus="true">운영 토큰 입력</button>
      </article>
    `;
  } else {
    const venues = kisQuickVenuesForDisplay(state.dashboard?.venues || [], state.kisBlockStatus);
    if (!venues.length) {
      html = `
        <article class="kis-quick-card muted">
          <div>
            <span class="section-kicker">KIS</span>
            <strong>국장 계좌 대기</strong>
          </div>
          <p>운영 토큰 또는 KIS 연결 상태를 확인해 주세요.</p>
        </article>
      `;
    } else {
      html = venues.map((venue) => {
        const assets = Array.isArray(venue.assets) ? venue.assets : [];
        const positions = assets.filter((asset) => asset.kind !== "cash");
        const positionCount = Number(venue.position_count || positions.length || 0);
        const positionText = positions.length
          ? positions
              .slice(0, 4)
              .map((asset) => {
                const name = asset.asset_name || asset.asset || asset.symbol || "-";
                const qty = Number(asset.qty || 0);
                return `${name} ${fmtNum(qty)}주`;
              })
              .join(" · ")
          : positionCount > 0
            ? `보유 ${fmtNum(positionCount, 0)}종목`
            : "보유 종목 없음";
        const moreText = positions.length > 4 ? ` · 외 ${positions.length - 4}개` : "";
        const pnl = Number(venue.unrealized_pnl_krw || 0);
        const basis = String(venue.total_value_basis || "") === "broker_net_asset" ? "공식 총평가" : "현금+보유";
        return `
          <button class="kis-quick-card" type="button" data-venue="${escapeHTML(venue.id)}">
            <div class="kis-quick-title">
              <span class="section-kicker">KIS</span>
              <strong>${escapeHTML(venue.label || "국장")}</strong>
              <em>${escapeHTML(basis)}</em>
            </div>
            <div class="kis-quick-values">
              <span>총 ${escapeHTML(fmtKRW(venue.total_krw))}</span>
              <span>현금 ${escapeHTML(fmtKRW(venue.cash_krw))}</span>
              <span>투자 ${escapeHTML(fmtKRW(venue.invested_krw))}</span>
              <span class="${pnl >= 0 ? "gain" : "loss"}">손익 ${escapeHTML(fmtKRW(pnl))}</span>
            </div>
            <p>${escapeHTML(positionText + moreText)}</p>
          </button>
        `;
      }).join("");
    }
  }
  targets.forEach(([id, target]) => {
    target.hidden = id === "helperKisQuickStrip" && !showHelperKisStrip;
    if (target.hidden) {
      target.innerHTML = "";
      return;
    }
    target.innerHTML = html;
  });
}

function renderKisAccountHoldingsPanel(payload = {}) {
  const renderer = typeof KIS_QUICK_VIEW !== "undefined" && typeof KIS_QUICK_VIEW.renderKisAccountHoldingsPanel === "function"
    ? KIS_QUICK_VIEW.renderKisAccountHoldingsPanel
    : null;
  if (renderer) {
    return renderer({
      dashboardVenues: state.dashboard?.venues || [],
      payload,
    }, kisQuickViewOptions());
  }

  const venues = kisQuickVenuesForDisplay(state.dashboard?.venues || [], payload);
  if (!venues.length) return "";
  const rows = venues.map((venue) => {
    const assets = Array.isArray(venue.assets) ? venue.assets : [];
    const positions = assets.filter((asset) => asset.kind !== "cash");
    const positionCount = Number(venue.position_count || positions.length || 0);
    const positionRows = positions.slice(0, 8).map((asset) => {
      const name = asset.asset_name || asset.asset || asset.symbol || "-";
      const symbol = asset.asset || asset.symbol || "";
      const qty = Number(asset.qty || 0);
      const value = Number(asset.value_krw || 0);
      const pnl = Number(asset.pnl_krw || 0);
      return `
        <li>
          <div>
            <strong>${escapeHTML(name)}</strong>
            <span class="mono">${escapeHTML(symbol)}</span>
          </div>
          <div class="runtime-kis-values">
            <span>${escapeHTML(fmtNum(qty))}주</span>
            <span>${escapeHTML(fmtKRW(value))}원</span>
            <span class="${pnl >= 0 ? "gain" : "loss"}">${escapeHTML(fmtKRW(pnl))}원</span>
          </div>
        </li>
      `;
    }).join("");
    const hiddenCount = Math.max(positions.length - 8, 0);
    const positionText = positionRows
      || (positionCount > 0 ? `<li><span>보유 ${escapeHTML(fmtNum(positionCount, 0))}종목 · 상세 종목 payload 대기</span></li>` : "<li><span>보유 종목 없음</span></li>");
    return `
      <article class="helper-card helper-card-wide runtime-kis-snapshot">
        <div class="panel-head compact">
          <div>
            <h4>${escapeHTML(venue.label || "국장")} 계좌</h4>
            <p>총 ${escapeHTML(fmtKRW(venue.total_krw))}원 · 현금 ${escapeHTML(fmtKRW(venue.cash_krw))}원 · 투자 ${escapeHTML(fmtKRW(venue.invested_krw))}원</p>
          </div>
          <span class="strategy-data-chip ${Number(venue.unrealized_pnl_krw || 0) >= 0 ? "good" : "warn"}">손익 ${escapeHTML(fmtKRW(venue.unrealized_pnl_krw))}원</span>
        </div>
        <ul class="runtime-kis-list">
          ${positionText}
          ${hiddenCount > 0 ? `<li><span>외 ${escapeHTML(fmtNum(hiddenCount, 0))}개 보유</span></li>` : ""}
        </ul>
      </article>
    `;
  }).join("");
  return `
    <section class="memory-section">
      <div class="panel-head compact">
        <div>
          <h3>국장 계좌/보유 종목</h3>
          <p>활성 블록이 없어도 계좌 보유분은 계속 표시됩니다.</p>
        </div>
      </div>
      <div class="helper-grid">
        ${rows}
      </div>
    </section>
  `;
}

function renderAccountCashLine(account) {
  if (!account || typeof account !== "object") {
    return "국장1 계좌 스냅샷 대기";
  }
  const cashLike = asNumber(account.cash_krw, 0);
  const orderable = asNumber(account.orderable_cash_krw, cashLike);
  const settled = asNumber(account.settled_cash_krw, cashLike);
  const receivable = asNumber(account.receivable_cash_krw, 0);
  const positionValue = asNumber(account.position_value_krw, 0);
  const positionCount = normalizeNonNegativeInt(account.position_count);
  const parts = [
    `현금성 ${fmtKRW(cashLike)}원`,
    `주문가능 ${fmtKRW(orderable)}원`,
    `예수금 ${fmtKRW(settled)}원`,
  ];
  if (receivable > 0) {
    parts.push(`정산예정 ${fmtKRW(receivable)}원`);
  }
  parts.push(`보유 ${fmtKRW(positionValue)}원`);
  parts.push(`${positionCount}종목`);
  return parts.join(" · ");
}

function isFxWarnSource(source) {
  const normalized = String(source || "").toLowerCase().trim();
  if (!normalized) return true;
  return normalized.startsWith("fallback") || normalized.endsWith("_proxy");
}

function renderFxPills(fx) {
  const usdtPill = qs("fxUsdtPill");
  const usdPill = qs("fxUsdPill");
  const updatedPill = qs("fxUpdatedPill");
  if (!usdtPill || !usdPill || !updatedPill) return;

  if (!fx) {
    usdtPill.className = "pill mono";
    usdPill.className = "pill mono";
    updatedPill.className = "pill mono";
    usdtPill.textContent = "USDT/KRW --";
    usdPill.textContent = "USD/KRW --";
    updatedPill.textContent = "FX --";
    return;
  }

  const usdtRate = Number(fx.usdt_krw || 0);
  const usdRate = Number(fx.usd_krw || 0);
  const usdtSource = String(fx.usdt_source || "-");
  const usdSource = String(fx.usd_source || "-");
  const fxWarn = String(fx.status || "").toLowerCase() === "warn";
  const usdtWarn = isFxWarnSource(usdtSource);
  const usdWarn = isFxWarnSource(usdSource);

  usdtPill.className = `pill mono ${usdtWarn ? "pill-warn" : "pill-ok"}`;
  usdPill.className = `pill mono ${usdWarn ? "pill-warn" : "pill-ok"}`;
  updatedPill.className = `pill mono ${fxWarn ? "pill-warn" : "pill-ok"}`;
  usdtPill.textContent = `USDT/KRW ${fmtNum(usdtRate, 2)} (${usdtSource})`;
  usdPill.textContent = `USD/KRW ${fmtNum(usdRate, 2)} (${usdSource})`;
  updatedPill.textContent = `FX ${fxWarn ? "WARN" : "OK"} ${fmtKST(fx.fetched_at)}`;
}

function renderVenueTabs() {
  const venues = orderedVenuesForDisplay(state.dashboard?.venues || []);
  const tabs = [{ id: "all", label: "전체" }, ...venues.map((v) => ({ id: v.id, label: v.label }))];

  if (!tabs.some((tab) => tab.id === state.activeVenueId)) {
    state.activeVenueId = "all";
  }

  qs("venueTabs").innerHTML = tabs
    .map((row) => {
      const activeClass = row.id === state.activeVenueId ? "active" : "";
      return `
        <button type="button" class="tab-button ${activeClass}" data-venue="${escapeHTML(row.id)}">
          ${escapeHTML(row.label)}
        </button>
      `;
    })
    .join("");
}

function renderAssetsTable(venue) {
  qs("assetsBody").innerHTML = (venue.assets || [])
  .map((row) => {
      const displayName = row.asset_name || row.asset || "-";
      const label = row.venue_label ? `${row.venue_label} · ${displayName}` : displayName;
      const kind = row.kind === "cash" ? "현금" : "보유";
      const avg = row.kind === "cash" ? "-" : fmtKRW(row.avg_price);
      const mark = row.kind === "cash" ? "-" : fmtKRW(row.mark_price);
      const pnlClass = Number(row.pnl_krw) >= 0 ? "gain" : "loss";
      return `
      <tr>
        <td>${escapeHTML(label)}</td>
        <td>${escapeHTML(kind)}</td>
        <td>${escapeHTML(fmtNum(row.qty))}</td>
        <td>${escapeHTML(fmtNum(row.available))}</td>
        <td>${escapeHTML(fmtNum(row.locked))}</td>
        <td>${escapeHTML(avg)}</td>
        <td>${escapeHTML(mark)}</td>
        <td>${escapeHTML(fmtKRW(row.value_krw))}</td>
        <td class="${pnlClass}">${escapeHTML(fmtKRW(row.pnl_krw))}</td>
      </tr>
    `;
    })
    .join("");
}

function renderSessions(sessions, clockUtc) {
  const rows = sessions || [];
  qs("sessionUpdatedAt").textContent = `${rows.length} sessions · KST ${fmtKST(clockUtc)}`;

  if (!rows.length) {
    qs("sessionCards").innerHTML = `
      <article class="session-card">
        <div class="session-footnote">선택한 거래소에 연결된 세션이 없습니다.</div>
      </article>
    `;
    return;
  }

  qs("sessionCards").innerHTML = rows
    .map((row) => {
      const statusClass = String(row.status || "").toLowerCase() === "running" ? "running" : "paused";
      const markets = Array.isArray(row.active_markets) ? row.active_markets.join(", ") : "";
      const bot = row.bot_name ? ` · ${row.bot_name}` : "";
      const venuePrefix = row.venue_label ? `${row.venue_label} · ` : "";
      const modeLine = `${venuePrefix}${row.mode || "-"}${bot} · ${markets}`;
      const header = `
        <div class="session-head">
          <div>
            <h4>${escapeHTML(row.name || "-")}</h4>
            <div class="session-mode">${escapeHTML(modeLine)}</div>
          </div>
          <span class="session-status ${statusClass}">${escapeHTML(row.status || "-")}</span>
        </div>
      `;

      if (row.mode === "short_term") {
        const realized = Number(row.realized_pnl_krw || 0);
        const unrealized = Number(row.unrealized_pnl_krw || 0);
        const fees = Number(row.fees_paid_krw || 0);
        const net = realized + unrealized + fees;
        const netClass = net >= 0 ? "gain" : "loss";
        const makerFee = Number(row.fee_breakdown?.maker_krw || 0);
        const takerFee = Number(row.fee_breakdown?.taker_krw || 0);

        return `
      <article class="session-card compact">
        ${header}
        <div class="session-compact-grid">
          <div class="compact-kpi">
            <span>종목</span>
            <strong>${escapeHTML(row.trade_symbol || "-")}</strong>
          </div>
          <div class="compact-kpi">
            <span>실시간 순손익</span>
            <strong class="${netClass}">${escapeHTML(fmtKRW(net))} KRW</strong>
          </div>
          <div class="compact-kpi">
            <span>오늘 체결</span>
            <strong>${escapeHTML(row.trade_count_today)}</strong>
          </div>
          <div class="compact-kpi">
            <span>수수료</span>
            <strong class="${fees >= 0 ? "gain" : "loss"}">${escapeHTML(fmtKRW(fees))} KRW</strong>
          </div>
          <div class="compact-kpi">
            <span>승률</span>
            <strong>${escapeHTML(fmtNum(row.win_rate_pct, 1))}%</strong>
          </div>
          <div class="compact-kpi">
            <span>사이클</span>
            <strong>${escapeHTML(row.cycle_sec)}s</strong>
          </div>
        </div>
        <details class="session-details">
          <summary>상세 지표</summary>
          <div class="session-details-body">
        <div class="session-kpis">
          <div class="session-kpi">
            <p>전략 수</p>
            <strong>${escapeHTML(row.strategy_count)}</strong>
          </div>
          <div class="session-kpi">
            <p>사이클</p>
            <strong>${escapeHTML(row.cycle_sec)}s</strong>
          </div>
          <div class="session-kpi">
            <p>오늘 체결</p>
            <strong>${escapeHTML(row.trade_count_today)}</strong>
          </div>
          <div class="session-kpi">
            <p>실현 손익</p>
            <strong class="${realized >= 0 ? "gain" : "loss"}">${escapeHTML(fmtKRW(realized))} KRW</strong>
          </div>
          <div class="session-kpi">
            <p>미실현 손익</p>
            <strong class="${unrealized >= 0 ? "gain" : "loss"}">${escapeHTML(fmtKRW(unrealized))} KRW</strong>
          </div>
          <div class="session-kpi">
            <p>수수료 합계</p>
            <strong class="${fees >= 0 ? "gain" : "loss"}">${escapeHTML(fmtKRW(fees))} KRW</strong>
          </div>
          <div class="session-kpi">
            <p>순손익(실시간)</p>
            <strong class="${netClass}">${escapeHTML(fmtKRW(net))} KRW</strong>
          </div>
          <div class="session-kpi">
            <p>체결대금</p>
            <strong>${escapeHTML(fmtKRW(row.volume_traded_krw))} KRW</strong>
          </div>
          <div class="session-kpi">
            <p>승률</p>
            <strong>${escapeHTML(fmtNum(row.win_rate_pct, 1))}%</strong>
          </div>
          <div class="session-kpi">
            <p>평균 보유</p>
            <strong>${escapeHTML(fmtNum(row.avg_holding_min, 1))}분</strong>
          </div>
          <div class="session-kpi">
            <p>당일 DD</p>
            <strong class="${Number(row.intraday_drawdown_pct) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtNum(row.intraday_drawdown_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>리스크 가드</p>
            <strong>${escapeHTML(row.risk_guard || "-")}</strong>
          </div>
        </div>
        <div class="session-section">
          <h5>단타 종목 설정</h5>
          <div class="symbol-plan-grid">
            <div class="symbol-plan-item"><span>종목</span><strong>${escapeHTML(row.trade_symbol || "-")}</strong></div>
            <div class="symbol-plan-item"><span>포지션</span><strong>${escapeHTML(row.position_side || "-")}</strong></div>
            <div class="symbol-plan-item"><span>진입가</span><strong>${escapeHTML(fmtMaybeKRW(row.entry_price))}</strong></div>
            <div class="symbol-plan-item"><span>손절가</span><strong>${escapeHTML(fmtMaybeKRW(row.stop_loss_price))}</strong></div>
            <div class="symbol-plan-item"><span>익절가</span><strong>${escapeHTML(fmtMaybeKRW(row.take_profit_price))}</strong></div>
            <div class="symbol-plan-item"><span>최대 노출</span><strong>${escapeHTML(fmtMaybeKRW(row.max_notional_krw))} KRW</strong></div>
            <div class="symbol-plan-item"><span>최대 보유시간</span><strong>${escapeHTML(fmtNum(row.holding_limit_min || 0, 0))}분</strong></div>
          </div>
        </div>
        <div class="session-footnote">
          ${escapeHTML(row.display_note || "-")}<br />
          Maker ${escapeHTML(fmtKRW(makerFee))} / Taker ${escapeHTML(fmtKRW(takerFee))} KRW
        </div>
          </div>
        </details>
      </article>
    `;
      }

      const alpha = Number(row.portfolio_return_30d_pct || 0) - Number(row.benchmark_return_30d_pct || 0);
      const alphaClass = alpha >= 0 ? "gain" : "loss";
      const principles = (row.principles || [])
        .map(
          (item) => `
          <div class="principle ${String(item.status).toLowerCase() === "ok" ? "ok" : "warn"}">
            <span>${escapeHTML(item.rule)}</span>
            <strong>${escapeHTML(item.value)}</strong>
          </div>
        `
        )
        .join("");

      return `
      <article class="session-card compact">
        ${header}
        <div class="session-compact-grid">
          <div class="compact-kpi">
            <span>30일 수익률</span>
            <strong class="${Number(row.portfolio_return_30d_pct) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtNum(row.portfolio_return_30d_pct, 2))}%</strong>
          </div>
          <div class="compact-kpi">
            <span>알파(30d)</span>
            <strong class="${alphaClass}">${escapeHTML(fmtNum(alpha, 2))}%</strong>
          </div>
          <div class="compact-kpi">
            <span>드리프트</span>
            <strong>${escapeHTML(fmtNum(row.allocation_drift_pct, 2))}%</strong>
          </div>
          <div class="compact-kpi">
            <span>리밸런스 예정</span>
            <strong>${escapeHTML(row.rebalance_due || "-")}</strong>
          </div>
          <div class="compact-kpi">
            <span>목표 종목 수</span>
            <strong>${escapeHTML((row.targets || []).length)}개</strong>
          </div>
          <div class="compact-kpi">
            <span>현금 버퍼</span>
            <strong>${escapeHTML(fmtNum(row.cash_buffer_pct, 2))}%</strong>
          </div>
        </div>
        <details class="session-details">
          <summary>상세 지표</summary>
          <div class="session-details-body">
        <div class="session-kpis">
          <div class="session-kpi">
            <p>전략 수</p>
            <strong>${escapeHTML(row.strategy_count)}</strong>
          </div>
          <div class="session-kpi">
            <p>사이클</p>
            <strong>${escapeHTML(row.cycle_sec)}s</strong>
          </div>
          <div class="session-kpi">
            <p>오늘 체결</p>
            <strong>${escapeHTML(row.trade_count_today)}</strong>
          </div>
          <div class="session-kpi">
            <p>30일 수익률</p>
            <strong class="${Number(row.portfolio_return_30d_pct) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtNum(row.portfolio_return_30d_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>벤치마크(30d)</p>
            <strong>${escapeHTML(fmtNum(row.benchmark_return_30d_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>알파(30d)</p>
            <strong class="${alphaClass}">${escapeHTML(fmtNum(alpha, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>Tracking Error</p>
            <strong>${escapeHTML(fmtNum(row.tracking_error_30d_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>1Y Max DD</p>
            <strong class="${Number(row.max_drawdown_1y_pct) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtNum(row.max_drawdown_1y_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>Turnover(30d)</p>
            <strong>${escapeHTML(fmtNum(row.turnover_30d_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>Fee Drag(30d)</p>
            <strong class="${Number(row.fee_drag_30d_pct) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtNum(row.fee_drag_30d_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>Allocation Drift</p>
            <strong>${escapeHTML(fmtNum(row.allocation_drift_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>리밸런스 예정</p>
            <strong>${escapeHTML(row.rebalance_due || "-")}</strong>
          </div>
          <div class="session-kpi">
            <p>리밸런스 금액</p>
            <strong>${escapeHTML(fmtKRW(row.rebalance_amount_krw))} KRW</strong>
          </div>
          <div class="session-kpi">
            <p>리밸런스 라인</p>
            <strong>${escapeHTML(row.rebalance_lines)} 종목</strong>
          </div>
          <div class="session-kpi">
            <p>현금 버퍼</p>
            <strong>${escapeHTML(fmtNum(row.cash_buffer_pct, 2))}%</strong>
          </div>
          <div class="session-kpi">
            <p>리스크 가드</p>
            <strong>${escapeHTML(row.risk_guard || "-")}</strong>
          </div>
        </div>
        <div class="principle-grid">${principles}</div>
        <div class="session-section">
          <h5>종목별 목표 밸런스 / 가격 가이드</h5>
          <div class="target-table-wrap">
            <table class="target-table">
              <thead>
                <tr>
                  <th>종목</th>
                  <th>목표비중</th>
                  <th>현재비중</th>
                  <th>목표가</th>
                  <th>손절가</th>
                  <th>익절가</th>
                </tr>
              </thead>
              <tbody>
                ${(row.targets || [])
                  .map(
                    (item) => `
                    <tr>
                      <td>${escapeHTML(item.symbol)}</td>
                      <td>${escapeHTML(fmtNum(item.target_weight_pct, 1))}%</td>
                      <td>${escapeHTML(fmtNum(item.current_weight_pct, 1))}%</td>
                      <td>${escapeHTML(fmtMaybeKRW(item.target_price))}</td>
                      <td>${escapeHTML(fmtMaybeKRW(item.stop_loss_price))}</td>
                      <td>${escapeHTML(fmtMaybeKRW(item.take_profit_price))}</td>
                    </tr>
                  `
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </div>
        <div class="session-footnote">${escapeHTML(row.display_note || "-")}</div>
          </div>
        </details>
      </article>
    `;
    })
    .join("");
}

function renderActiveVenue() {
  const venue = getActiveVenue();
  qs("activeMarketBadge").textContent = `${venue.label} | ${venue.market}`;
  qs("activeTotalValue").textContent = `${fmtKRW(venue.total_krw)} KRW`;
  qs("activeCashValue").textContent = `${fmtKRW(venue.cash_krw)} KRW`;
  qs("activeInvestedValue").textContent = `${fmtKRW(venue.invested_krw)} KRW`;
  qs("activePnlValue").textContent = `${fmtKRW(venue.unrealized_pnl_krw)} KRW`;
  qs("activePnlValue").className = Number(venue.unrealized_pnl_krw) >= 0 ? "gain" : "loss";

  renderAssetsTable(venue);
}

function renderEvents(rows) {
  qs("eventList").innerHTML = rows
    .map(
      (row) => `
      <li>
        <span class="event-type">${escapeHTML(row.type)}</span>
        <strong>${escapeHTML(row.message)}</strong>
      </li>
    `
    )
    .join("");
}

function getErrorMessage(reason) {
  if (reason instanceof Error && reason.message) {
    return reason.message;
  }
  return "요청 실패";
}

function pickUpdatedAt(payload) {
  if (!payload || typeof payload !== "object") return "";
  const keys = ["updated_at", "fetched_at", "generated_at", "collected_at", "last_updated_at", "clock_utc"];
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return "";
}

function readableText(value, fallback = "내용 없음") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "object") return stringifySafe(value, true);
  const text = String(value).replace(/\n{3,}/g, "\n\n").trim();
  return text || fallback;
}

function registerHelperDetail(payload) {
  state.helperDetailSeq += 1;
  const id = `detail_${state.helperDetailSeq}`;
  state.helperDetailRegistry[id] = payload;
  return id;
}

function renderHelperDetailModal() {
  const detail = state.helperDetailModal;
  if (!detail) return "";
  const meta = Array.isArray(detail.meta)
    ? detail.meta.map((item) => String(item || "").trim()).filter((item) => item)
    : [];
  const metaHtml = meta.length
    ? `<div class="helper-detail-meta">${meta.map((item) => `<span>${escapeHTML(item)}</span>`).join("")}</div>`
    : "";
  const url = String(detail.url || "").trim();
  const urlHtml = url
    ? `<a class="helper-detail-link" href="${escapeHTML(url)}" target="_blank" rel="noreferrer">원문</a>`
    : "";
  return `
    <div class="helper-detail-backdrop" data-helper-detail-close="true">
      <section class="helper-detail-modal" role="dialog" aria-modal="true" aria-labelledby="helperDetailTitle">
        <header class="helper-detail-head">
          <div>
            <span class="eyebrow">${escapeHTML(detail.subtitle || "DETAIL")}</span>
            <h4 id="helperDetailTitle">${escapeHTML(detail.title || "전문")}</h4>
          </div>
          <div class="helper-detail-actions">
            ${urlHtml}
            <button class="btn small" type="button" data-helper-detail-close="true">닫기</button>
          </div>
        </header>
        ${metaHtml}
        <pre class="helper-detail-body">${escapeHTML(readableText(detail.body))}</pre>
      </section>
    </div>
  `;
}

function normalizeScore100(value) {
  if (value === null || value === undefined || value === "") return null;
  const score = Number(value);
  if (!Number.isFinite(score)) return null;
  const rounded = Math.round(score);
  return Math.min(100, Math.max(0, rounded));
}

function normalizeNonNegativeInt(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.round(parsed));
}

function helperSourceLabel(value) {
  const source = String(value || "").trim().toLowerCase();
  if (!source) return "기타";
  if (source.includes("codex")) return "AI 리서치";
  if (source.includes("naver_report_rag")) return "RAG 검색";
  if (source.includes("naver_report_db")) return "리포트 DB";
  if (source.includes("report_crawl")) return "직접 수집";
  return truncateWithEllipsis(source, 28);
}

function helperSourceToneClass(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("rag")) return "source-rag";
  if (text.includes("리포트") || text.includes("db") || text.includes("report")) return "source-db";
  if (text.includes("whale") || text.includes("세시반") || text.includes("market") || text.includes("reference")) {
    return "source-reference";
  }
  if (text.includes("ai") || text.includes("codex")) return "source-ai";
  return "source-neutral";
}

function helperStateChip(value) {
  const text = String(value ?? "").trim() || "-";
  const lower = text.toLowerCase();
  const okTokens = [
    "ok",
    "online",
    "running",
    "covered",
    "connected",
    "ready",
    "up",
    "enabled",
    "true",
    "available",
    "active",
    "clear",
    "pass",
  ];
  const badTokens = ["critical", "blocked", "failed", "down", "invalid"];
  const warnTokens = [
    "warn",
    "stale",
    "offline",
    "error",
    "missing",
    "unknown",
    "disabled",
    "false",
  ];
  if (badTokens.some((token) => lower === token || lower.includes(token))) {
    return { text, cls: "bad" };
  }
  const isWarn = warnTokens.some((token) => lower === token || lower.includes(token));
  const isOk = okTokens.some((token) => lower === token || lower.includes(token));
  return {
    text,
    cls: isOk && !isWarn ? "ok" : isWarn ? "warn" : "neutral",
  };
}

function boolFromStatus(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (value === null || value === undefined || value === "") return fallback;
  const text = String(value).trim().toLowerCase();
  if (["true", "1", "yes", "enabled", "active", "ready"].includes(text)) return true;
  if (["false", "0", "no", "disabled", "inactive", "none"].includes(text)) return false;
  return fallback;
}

function renderResearchMetricTile(label, value, detail, tone = "neutral") {
  return `
    <article class="helper-research-metric ${escapeHTML(`tone-${tone}`)}">
      <span>${escapeHTML(label)}</span>
      <strong>${escapeHTML(value)}</strong>
      <small>${escapeHTML(detail)}</small>
    </article>
  `;
}

function renderResearchKnowledgeStrip(research, rows) {
  const reports = state.reportsStatus || {};
  const repo = reports.repository || {};
  const intelligence = reports.intelligence || {};
  const facts = repo.facts || {};
  const rag = reports.rag || {};
  const llmFacts = intelligence.llm_facts || {};
  const codexRuntime = intelligence.codex_runtime || {};
  const health = state.healthStatus || {};

  const score = normalizeScore100(research?.agent_self_score_100);
  const learningTotalCount = normalizeNonNegativeInt(research?.learning_total_count);
  const reportTotal = normalizeNonNegativeInt(
    repo.total_reports ?? reports.report_count ?? research?.report_count
  );
  const factsTotal = normalizeNonNegativeInt(facts.total_facts);
  const symbolTotal = normalizeNonNegativeInt(
    repo.total_symbols ?? reports.symbol_count ?? research?.symbol_count
  );
  const symbolLinkCount = normalizeNonNegativeInt(
    repo.symbol_link_count ?? reports.symbol_link_count ?? research?.symbol_link_count
  );
  const etfLinkCount = normalizeNonNegativeInt(repo.etf_link_count);
  const linkedReportCount = normalizeNonNegativeInt(repo.linked_report_count);
  const unlinkedEtfKeywordCount = normalizeNonNegativeInt(repo.unlinked_etf_keyword_report_count);
  const lastSymbolLinkUpdated = repo.last_symbol_link_updated_at
    ? fmtKST(repo.last_symbol_link_updated_at, true)
    : "--";
  const ragCount = normalizeNonNegativeInt(
    rag.count ?? reports.rag_count ?? research?.rag_count
  );
  const ragAvailable = boolFromStatus(
    rag.available ?? reports.rag_available ?? research?.rag_available,
    false
  );
  const fundamentalsSymbolCount = normalizeNonNegativeInt(
    reports.fundamentals_symbol_count ?? research?.fundamentals_symbol_count
  );
  const llmFactsEnabled = boolFromStatus(
    llmFacts.enabled,
    boolFromStatus(health.naver_reports_llm_facts_enabled, false)
  );
  const llmFactsActive = boolFromStatus(
    llmFacts.active,
    boolFromStatus(health.naver_reports_llm_facts_active, false)
  );
  const codexRuntimeMode = String(codexRuntime.mode || health.codex_runtime_mode || "none");
  const codexRuntimeReady = boolFromStatus(
    codexRuntime.ready,
    boolFromStatus(health.codex_runtime_ready, false)
  );
  const note = truncateWithEllipsis(research?.agent_self_score_note || "", 120);
  const isStale =
    String(research?.status || "").toLowerCase() === "stale" || Boolean(research?.stale);
  const updatedLabel = research?.updated_at ? fmtKST(research.updated_at, true) : "--";
  const ageLabel = fmtDurationSec(research?.age_sec);
  const maxAgeLabel = fmtDurationSec(research?.max_age_sec);
  const sourceLabels = Array.isArray(research?.market_intelligence_sources)
    ? research.market_intelligence_sources
        .map((row) => row?.label || row?.source_id || "")
        .filter((label) => label)
        .slice(0, 4)
    : [];

  const llmValue = llmFactsActive ? "active" : llmFactsEnabled ? "waiting" : "off";
  const llmTone = llmFactsActive ? "ok" : llmFactsEnabled ? "warn" : "neutral";
  const ragTone = ragAvailable ? "ok" : "neutral";

  return `
    ${
      isStale
        ? `<div class="helper-stale-banner">
            <strong>오래됨</strong>
            <span>마지막 갱신 ${escapeHTML(updatedLabel)} · 경과 ${escapeHTML(ageLabel)} · 기준 ${escapeHTML(maxAgeLabel)}</span>
          </div>`
        : ""
    }
    <div class="helper-research-metrics">
      ${renderResearchMetricTile(
        "리서치 점수",
        score === null ? "--/100" : `${score}/100`,
        note || "자가평가 코멘트 없음",
        isStale || score === null ? "warn" : score >= 65 ? "ok" : "warn"
      )}
      ${renderResearchMetricTile(
        "리포트 DB",
        reportTotal === null ? "--건" : `${reportTotal}건`,
        `facts ${factsTotal === null ? "--" : factsTotal} · symbols ${symbolTotal === null ? "--" : symbolTotal}`,
        reportTotal ? "ok" : "neutral"
      )}
      ${renderResearchMetricTile(
        "ETF 링크",
        linkedReportCount === null ? "--건" : `${linkedReportCount}건`,
        `links ${symbolLinkCount === null ? "--" : symbolLinkCount} · ETF ${etfLinkCount === null ? "--" : etfLinkCount} · unlinked ${unlinkedEtfKeywordCount === null ? "--" : unlinkedEtfKeywordCount} · ${lastSymbolLinkUpdated}`,
        linkedReportCount ? "ok" : unlinkedEtfKeywordCount ? "warn" : "neutral"
      )}
      ${renderResearchMetricTile(
        "RAG",
        ragAvailable ? "ready" : "off",
        `chunks ${ragCount === null ? "--" : ragCount} · valuation ${fundamentalsSymbolCount === null ? "--" : fundamentalsSymbolCount}`,
        ragTone
      )}
      ${renderResearchMetricTile(
        "LLM facts",
        llmValue,
        `native ${codexRuntimeReady ? codexRuntimeMode : "none"}`,
        llmTone
      )}
    </div>
    <div class="helper-research-context">
      <span class="${isStale ? "status-warn" : ""}">status ${escapeHTML(research?.status || "-")}</span>
      <span>items ${rows.length}</span>
      <span>learning ${learningTotalCount === null ? "--" : learningTotalCount}</span>
      <span>query ${escapeHTML(truncateWithEllipsis(research?.query || "-", 42))}</span>
      ${sourceLabels.length ? `<span>sources ${escapeHTML(sourceLabels.join(", "))}</span>` : ""}
    </div>
  `;
}

function renderPageMode() {
  renderGlobalExecutionMode();
  const isHelper = state.activePage === "helper";
  document.body.dataset.page = state.activePage;
  const mainIds = ["mainDashboardPage"];
  for (const id of mainIds) {
    const node = qs(id);
    if (node) {
      node.hidden = isHelper;
    }
  }

  const helperSection = qs("helperAgentSection");
  if (helperSection) {
    helperSection.hidden = !isHelper;
  }

  const mainNavBtn = qs("mainNavBtn");
  if (mainNavBtn) {
    const active = !isHelper;
    mainNavBtn.classList.toggle("active", active);
    mainNavBtn.setAttribute("aria-current", active ? "page" : "false");
  }
  const helperNavBtn = qs("helperNavBtn");
  if (helperNavBtn) {
    helperNavBtn.classList.toggle("active", isHelper && state.activeHelperTab === resolveInitialHelperTab());
  }
  document.querySelectorAll("[data-nav-helper-tab]").forEach((button) => {
    const targetTab = String(button.dataset.navHelperTab || "");
    const active = isHelper && targetTab === state.activeHelperTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelectorAll("[data-mobile-page], [data-mobile-helper-tab]").forEach((button) => {
    const mobilePage = String(button.dataset.mobilePage || "");
    const mobileTab = String(button.dataset.mobileHelperTab || "");
    const active = mobilePage === "main"
      ? !isHelper
      : isHelper && mobileTab === state.activeHelperTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  const mobileMore = qs("mobileNavMoreBtn");
  if (mobileMore) {
    mobileMore.setAttribute("aria-expanded", String(state.mobileMenuOpen));
  }
}

function setMobileMenuOpen(open) {
  state.mobileMenuOpen = Boolean(open);
  document.body.classList.toggle("mobile-menu-open", state.mobileMenuOpen);
  const mobileMore = qs("mobileNavMoreBtn");
  if (mobileMore) {
    mobileMore.setAttribute("aria-expanded", String(state.mobileMenuOpen));
  }
}

function openHelperPage(tab = ASK_HELPER_TAB) {
  const nextTab = HELPER_TABS.has(tab) ? tab : resolveInitialHelperTab();
  state.activePage = "helper";
  state.activeHelperTab = nextTab;
  setMobileMenuOpen(false);
  saveUiState();
  renderPageMode();
  renderHelperAgent();
  syncActiveBlockRefresh();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openAskPageWithQuery(query = "") {
  if (query) state.helperAsk.query = query;
  openHelperPage(ASK_HELPER_TAB);
  renderHelperAgent();
}

function openMainPage() {
  state.activePage = "main";
  state.helperDetailModal = null;
  setMobileMenuOpen(false);
  const modalRoot = qs("helperModalRoot");
  if (modalRoot) {
    modalRoot.innerHTML = "";
  }
  saveUiState();
  renderPageMode();
  syncActiveBlockRefresh();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function shouldAutoRefreshActiveBlocks() {
  return (
    state.activePage === "helper"
    && ACTIVE_BLOCK_TABS.has(state.activeHelperTab)
    && document.visibilityState !== "hidden"
  );
}

function renderActiveRefreshChip(tab) {
  if (!ACTIVE_BLOCK_TABS.has(tab)) return "";
  const active = shouldAutoRefreshActiveBlocks() && state.activeHelperTab === tab;
  const tone = state.activeRefresh.error ? "warn" : active ? "good" : "neutral";
  const suffix = state.activeRefresh.inFlight
    ? "갱신 중"
    : state.activeRefresh.lastAt
      ? `최근 ${fmtKST(state.activeRefresh.lastAt, true)}`
      : `${Math.round(ACTIVE_BLOCK_REFRESH_MS / 1000)}초 주기`;
  return `<span class="strategy-data-chip ${tone}">자동 갱신 ${escapeHTML(suffix)}</span>`;
}

function syncActiveBlockRefresh() {
  if (shouldAutoRefreshActiveBlocks()) {
    if (!state.activeRefresh.timer) {
      state.activeRefresh.timer = window.setInterval(
        refreshActiveBlockPanel,
        ACTIVE_BLOCK_REFRESH_MS
      );
    }
    return;
  }
  if (state.activeRefresh.timer) {
    window.clearInterval(state.activeRefresh.timer);
    state.activeRefresh.timer = null;
  }
}

async function refreshActiveBlockPanel() {
  if (!shouldAutoRefreshActiveBlocks() || state.activeRefresh.inFlight) return;
  if (
    state.activeHelperTab === "binance_trader"
    && (state.binanceTrader.loading || state.binanceTrader.running)
  ) {
    return;
  }
  state.activeRefresh.inFlight = true;
  state.activeRefresh.error = "";
  try {
    if (state.activeHelperTab === "kis_trader") {
      await loadKisBlocks({
        activeOnly: true,
        includeEtf: false,
        includeDiscovery: false,
        includeJudge: false,
        silent: true,
      });
    } else if (state.activeHelperTab === "binance_trader") {
      await loadBinanceBlocks("auto", { includeContext: false, silent: true });
    }
    state.activeRefresh.lastAt = new Date().toISOString();
  } catch (error) {
    state.activeRefresh.error = getErrorMessage(error);
  } finally {
    state.activeRefresh.inFlight = false;
    if (state.activePage === "helper" && ACTIVE_BLOCK_TABS.has(state.activeHelperTab)) {
      renderHelperAgent();
    }
  }
}

function ensureHelperTabData(tab = state.activeHelperTab) {
  if (!hasAdminToken()) return;
  if (
    tab === "strategy_intel"
    && !state.strategyIntel.result
    && !state.strategyIntel.loading
  ) {
    loadStrategyIntel(false);
  }
  if (
    tab === "market_judge"
    && !state.marketJudge.result
    && !state.marketJudge.loading
  ) {
    loadMarketJudge(false);
  }
  if (
    tab === "market_judge"
    && !state.marketPulse.result
    && !state.marketPulse.loading
  ) {
    loadMarketPulse(false);
  }
  if (tab === "rebalance" && !state.rebalanceStatus && !state.rebalanceError) {
    loadRebalanceStatus();
  }
  if (
    tab === "reports"
    && (!state.reportsStatus || state.reportsStatus.compact)
    && !state.reportsLoading
  ) {
    loadReportsStatus({ compact: false });
  }
  if (
    tab === "runtime"
    && !state.runtimeStorage
    && !state.runtimeStorageError
    && !state.runtimeStorageLoading
  ) {
    loadRuntimeStorage();
  }
  if (tab === "kis_trader" && !state.kisBlockStatus && !state.kisBlockLoading) {
    loadKisBlocks();
    return;
  } else if (
    tab === "kis_trader"
    && !state.etfResearch.status
    && !state.etfResearch.candidates
    && !state.etfResearch.loading
  ) {
    loadEtfResearch();
  }
  if (tab === "kis_trader" && !state.dailyDiscovery && !state.dailyDiscoveryLoading) {
    loadDailyDiscovery();
  }
  if (
    tab === "binance_trader"
    && !state.binanceTrader.status
    && !state.binanceTrader.loading
  ) {
    loadBinanceBlocks(false);
  }
  if (
    tab === "crypto_research"
    && (
      !state.binanceTrader.status
      || (!state.binanceTrader.quantSignals.length && !state.binanceTrader.patternContext)
    )
    && !state.binanceTrader.loading
  ) {
    loadBinanceBlocks(false, { includeContext: true });
  }
  if (
    tab === "crypto_research"
    && !state.cryptoResearch.context
    && !state.cryptoResearch.loading
  ) {
    loadCryptoResearch(false);
  }
  if (
    tab === "crypto_research"
    && !state.cryptoAlpha.context
    && !state.cryptoAlpha.loading
  ) {
    loadCryptoAlpha(false);
  }
  if (
    (tab === "crypto_research" || isMemoryTab(tab))
    && !state.evidencePolicy.status
    && !state.evidencePolicy.context
    && !state.evidencePolicy.loading
  ) {
    loadEvidencePolicy(false);
  }
  if (
    isMemoryTab(tab)
    && (
      !state.investmentMemory
      || state.investmentMemoryScope !== memoryScopeForTab(tab)
    )
    && !state.investmentMemoryLoading
  ) {
    loadInvestmentMemory(memoryScopeForTab(tab));
  } else if (isMemoryTab(tab) && !state.memoryReviews && !state.memoryReviewError) {
    loadMemoryReviews();
  }
  if (
    tab === "jue_wiki"
    && !state.jueWikiStatus
    && !state.jueWikiLoading
  ) {
    loadJueWiki();
  }
  if (tab === "settings" && !state.settingsPage.catalog && !state.settingsPage.loading) {
    loadSettingsCatalog();
  }
  if (
    tab === "settings"
    && !state.settingsPage.jueWorkflowStatus
    && !state.settingsPage.jueWorkflowLoading
  ) {
    loadJueWorkflowStatus();
  }
  if (
    tab === "settings"
    && !state.settingsPage.codexNativeStatus
    && !state.settingsPage.codexNativeLoading
  ) {
    loadCodexNativeStatus();
  }
}

function stringifySafe(value, pretty = false) {
  try {
    return JSON.stringify(value, null, pretty ? 2 : 0);
  } catch (_) {
    return String(value ?? "");
  }
}

function formatHelperValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "number") {
    return fmtNum(value, Number.isInteger(value) ? 0 : 4);
  }
  if (typeof value === "boolean") {
    return value ? "예" : "아니오";
  }
  if (Array.isArray(value)) {
    if (!value.length) return "-";
    return value
      .slice(0, 8)
      .map((item) => (typeof item === "object" ? stringifySafe(item) : String(item)))
      .join(", ");
  }
  if (typeof value === "object") {
    return stringifySafe(value);
  }
  return String(value);
}

function normalizeResearchItems(research) {
  if (Array.isArray(research)) return research;
  if (!research || typeof research !== "object") return [];
  const keys = ["items", "rows", "results", "entries", "reports", "summaries"];
  for (const key of keys) {
    if (Array.isArray(research[key])) {
      return research[key];
    }
  }
  return [research];
}

function renderResearchHelperTab() {
  const research = state.dashboard?.research;
  const rows = normalizeResearchItems(research);
  const knowledgeStrip = renderResearchKnowledgeStrip(research, rows);
  const isStale =
    String(research?.status || "").toLowerCase() === "stale" || Boolean(research?.stale);
  if (!rows.length) {
    return `
      <div class="helper-research-shell">
        ${knowledgeStrip}
        <div class="notice">리서치 요약 데이터가 없습니다.</div>
      </div>
    `;
  }

  const grouped = new Map();
  rows.slice(0, 18).forEach((row, index) => {
    if (!row || typeof row !== "object") {
      const label = "기타";
      const list = grouped.get(label) || [];
      list.push({ title: `리서치 ${index + 1}`, summary: formatHelperValue(row), status: "ok", picks: [] });
      grouped.set(label, list);
      return;
    }

    const label = helperSourceLabel(row.source || row.provider);
    const list = grouped.get(label) || [];
    list.push(row);
    grouped.set(label, list);
  });

  const score = normalizeScore100(research?.agent_self_score_100);
  const learningTotalCount = normalizeNonNegativeInt(research?.learning_total_count);
  const note = truncateWithEllipsis(research?.agent_self_score_note || "", 120);
  const overview = `${isStale ? "오래됨 · " : ""}전체 ${rows.length}건 · 누적 학습 ${learningTotalCount === null ? "--" : learningTotalCount}회 · 점수 ${score === null ? "--" : score}/100`;

  const sections = [...grouped.entries()].map(([groupLabel, entries]) => {
    const itemsHtml = entries
      .map((row, index) => {
        const fullTitle = row.title || row.name || row.symbol || row.code || row.topic || `리서치 ${index + 1}`;
        const title = truncateWithEllipsis(fullTitle, 84);
        const fullSummary = row.summary || row.thesis || row.note || row.reason || row.description || row.content || "요약 정보 없음";
        const summaryText = truncateWithEllipsis(
          fullSummary,
          220
        );
        const status = String(row.status || "ok").toLowerCase();
        const statusLabel = isStale
          ? "오래됨"
          : status === "ok"
            ? "OK"
            : truncateWithEllipsis(status.toUpperCase(), 18);
        const statusClass = status === "ok" && !isStale ? "ok" : "warn";
        const picks = Array.isArray(row.picks)
          ? row.picks
              .map((code) => String(code || "").trim())
              .filter((code) => code)
              .slice(0, 4)
          : [];
        const picksText = picks.length ? `후보: ${picks.join(", ")}` : "후보: 없음";
        const queryText = truncateWithEllipsis(row.query || research?.query || "일반", 34);
        const detailId = registerHelperDetail({
          title: String(fullTitle || ""),
          subtitle: groupLabel,
          body: fullSummary,
          url: row.url || row.link || "",
          meta: [
            statusLabel,
            picksText,
            `쿼리: ${row.query || research?.query || "일반"}`,
            row.source || row.provider || "",
          ],
        });
        return `
          <li class="helper-row-item">
            <div class="helper-row-head">
              <h4>${escapeHTML(title)}</h4>
              <span class="helper-row-status ${statusClass}">${escapeHTML(statusLabel)}</span>
            </div>
            <p class="helper-row-summary">${escapeHTML(summaryText)}</p>
            <div class="helper-row-meta">
              <span>${escapeHTML(picksText)}</span>
              <span>${escapeHTML(`쿼리: ${queryText}`)}</span>
              <button class="btn small helper-read-btn" type="button" data-helper-detail-id="${escapeHTML(detailId)}">전문</button>
            </div>
          </li>
        `;
      })
      .join("");

    return `
      <section class="helper-group-section ${helperSourceToneClass(groupLabel)}">
        <header class="helper-group-head">
          <h4>${escapeHTML(groupLabel)}</h4>
          <span class="pill mono">${escapeHTML(`${entries.length}건`)}</span>
        </header>
        <ul class="helper-row-list">
          ${itemsHtml}
        </ul>
      </section>
    `;
  });

  return `
    <div class="helper-research-shell">
      ${knowledgeStrip}
      <div class="helper-research-overview">
        <strong>${escapeHTML(overview)}</strong>
        <span>${escapeHTML(note === "-" ? "자가평가 코멘트 없음" : note)}</span>
      </div>
      <div class="helper-group-grid">
        ${sections.join("")}
      </div>
    </div>
  `;
}

function helperAskQuickQuestions() {
  return [
    "최근 리포트 기준으로 삼성전자 긍정/부정 근거를 정리해줘",
    "반도체 업황에서 확인해야 할 리스크만 뽑아줘",
    "오늘 리포트와 RAG 기준으로 시장 분위기를 요약해줘",
    "목표주가 변화가 있는 종목의 근거를 알려줘",
  ];
}

function renderHelperAskResult() {
  const result = state.helperAsk.result;
  const error = state.helperAsk.error;
  if (state.helperAsk.loading) {
    return '<div class="notice">답변 생성 중입니다.</div>';
  }
  if (error) {
    return `<div class="notice">질문 처리 실패: ${escapeHTML(error)}</div>`;
  }
  if (!result) {
    return '<div class="notice">아직 답변이 없습니다.</div>';
  }

  const citations = Array.isArray(result.citations) ? result.citations.slice(0, 12) : [];
  const reports = Array.isArray(result.items) ? result.items.slice(0, 5) : [];
  const ragItems = Array.isArray(result.rag_items) ? result.rag_items.slice(0, 5) : [];
  const followups = Array.isArray(result.followups) ? result.followups.slice(0, 4) : [];
  const limitations = Array.isArray(result.limitations) ? result.limitations.slice(0, 4) : [];
  const mode = String(result.mode || "deterministic");
  const confidence = String(result.confidence || "medium");
  const model = String(result.model || "gpt-5.6-terra");

  const citationsHtml = citations.length
    ? citations.map((row) => `<span>${escapeHTML(row)}</span>`).join("")
    : "<span>근거 인용 없음</span>";
  const reportCards = reports
    .map((row) => {
      const fullTitle = row.title || row.symbol || "리포트";
      const title = truncateWithEllipsis(fullTitle, 74);
      const meta = [row.broker, row.published_at, row.symbol].filter(Boolean).join(" · ") || "-";
      const fullSnippet = row.snippet || row.summary || "요약 없음";
      const snippet = truncateWithEllipsis(fullSnippet, 180);
      const detailId = registerHelperDetail({
        title: String(fullTitle || ""),
        subtitle: "리포트 DB",
        body: fullSnippet,
        url: row.url || row.link || "",
        meta: [meta],
      });
      return `
        <li class="helper-row-item source-db">
          <div class="helper-row-head">
            <h4>${escapeHTML(title)}</h4>
            <span class="helper-row-status ok">DB</span>
          </div>
          <p class="helper-row-summary">${escapeHTML(snippet || "요약 없음")}</p>
          <div class="helper-row-meta">
            <span>${escapeHTML(meta)}</span>
            <button class="btn small helper-read-btn" type="button" data-helper-detail-id="${escapeHTML(detailId)}">전문</button>
          </div>
        </li>
      `;
    })
    .join("");
  const ragCards = ragItems
    .map((row) => {
      const fullTitle = row.title || row.symbol || "RAG 문단";
      const title = truncateWithEllipsis(fullTitle, 74);
      const meta = [row.broker, row.published_at, row.symbol].filter(Boolean).join(" · ") || "-";
      const fullContent = row.content || row.summary || "문단 없음";
      const snippet = truncateWithEllipsis(fullContent, 200);
      const detailId = registerHelperDetail({
        title: String(fullTitle || ""),
        subtitle: "RAG 문단",
        body: fullContent,
        url: row.url || row.link || "",
        meta: [meta],
      });
      return `
        <li class="helper-row-item source-rag">
          <div class="helper-row-head">
            <h4>${escapeHTML(title)}</h4>
            <span class="helper-row-status ok">RAG</span>
          </div>
          <p class="helper-row-summary">${escapeHTML(snippet || "문단 없음")}</p>
          <div class="helper-row-meta">
            <span>${escapeHTML(meta)}</span>
            <button class="btn small helper-read-btn" type="button" data-helper-detail-id="${escapeHTML(detailId)}">전문</button>
          </div>
        </li>
      `;
    })
    .join("");
  const followupHtml = followups.length
    ? followups
        .map((item) => `<button class="helper-chip" type="button" data-helper-question="${escapeHTML(item)}">${escapeHTML(item)}</button>`)
        .join("")
    : "";
  const limitationHtml = limitations.length
    ? limitations.map((item) => `<li>${escapeHTML(item)}</li>`).join("")
    : "<li>수집 리포트와 RAG 문단을 실거래 판단에 연결하되, 주문은 블록 규칙과 안전 게이트 검증 후 실행됩니다.</li>";

  return `
    <section class="helper-answer-card">
      <div class="helper-answer-head">
        <div>
          <span class="eyebrow">ANSWER</span>
          <h4>${escapeHTML(truncateWithEllipsis(result.query || state.helperAsk.query || "질문", 82))}</h4>
        </div>
        <span class="pill mono">${escapeHTML(`${mode} · ${confidence} · ${model}`)}</span>
      </div>
      <pre class="helper-answer-text">${escapeHTML(result.answer || "")}</pre>
      <div class="helper-citation-strip">${citationsHtml}</div>
    </section>
    <div class="helper-ask-grid">
      <article class="helper-card">
        <h4>리포트 근거</h4>
        <ul class="helper-row-list">${reportCards || '<li class="helper-row-item">리포트 DB 근거 없음</li>'}</ul>
      </article>
      <article class="helper-card">
        <h4>RAG 문단</h4>
        <ul class="helper-row-list">${ragCards || '<li class="helper-row-item">RAG 근거 없음</li>'}</ul>
      </article>
      <article class="helper-card helper-card-wide">
        <h4>후속 질문</h4>
        <div class="helper-chip-row">${followupHtml || '<span class="helper-muted">후속 질문 없음</span>'}</div>
      </article>
      <article class="helper-card helper-card-wide">
        <h4>한계</h4>
        <ul class="helper-plain-list">${limitationHtml}</ul>
      </article>
    </div>
  `;
}

function renderAskHelperTab() {
  const quickQuestions = helperAskQuickQuestions()
    .map((item) => `<button class="helper-chip" type="button" data-helper-question="${escapeHTML(item)}">${escapeHTML(item)}</button>`)
    .join("");
  const queryValue = escapeHTML(state.helperAsk.query);
  const symbolValue = escapeHTML(state.helperAsk.symbol);
  const disabled = state.helperAsk.loading ? "disabled" : "";
  return `
    <div class="helper-ask-shell">
      <form class="helper-ask-form" id="helperAskForm">
        <label>
          질문
          <textarea id="helperAskQuery" rows="4" placeholder="예: 삼성전자 최근 리포트에서 긍정/부정 근거가 뭐야?">${queryValue}</textarea>
        </label>
        <div class="helper-ask-controls">
          <label>
            종목코드
            <input id="helperAskSymbol" inputmode="numeric" maxlength="6" placeholder="선택" value="${symbolValue}" />
          </label>
          <button class="btn primary" type="submit" ${disabled}>질문</button>
        </div>
        <div class="helper-chip-row">${quickQuestions}</div>
      </form>
      ${renderHelperAskResult()}
    </div>
  `;
}

function strategyIntelQuickQuestions() {
  return [
    "다음 거래일 관심 후보를 전략적으로 정리해줘",
    "고래 포지션과 종가 수급이 겹치는 후보를 우선순위로 봐줘",
    "오늘 시장 regime과 피해야 할 후보를 같이 정리해줘",
    "리포트 긍정 변화와 섹터 모멘텀이 같이 있는 종목을 찾아줘",
  ];
}

function sourceTone(status) {
  const text = String(status || "").toLowerCase();
  if (text === "active" || text === "ok" || text === "updated") return "ok";
  if (text === "waiting" || text === "partial" || text === "empty" || text === "fallback") return "warn";
  if (text === "error") return "bad";
  return "muted";
}

function strategyValuationLabel(label) {
  const text = String(label || "unknown").toLowerCase();
  if (text === "undervalued") return "저평가";
  if (text === "fair") return "적정";
  if (text === "expensive") return "부담";
  return "불명";
}

function strategyValuationTone(valuation) {
  const status = String(valuation?.status || "").toLowerCase();
  const label = String(valuation?.label || "unknown").toLowerCase();
  if (status && status !== "ok") return "muted";
  if (label === "undervalued") return "good";
  if (label === "expensive") return "warn";
  if (label === "fair") return "neutral";
  return "muted";
}

function formatMetric(value, suffix = "", maxFractionDigits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return `${fmtNum(parsed, maxFractionDigits)}${suffix}`;
}

function renderStrategyValuationChips(row) {
  const valuation = row?.valuation && typeof row.valuation === "object" ? row.valuation : {};
  const metrics = valuation.metrics && typeof valuation.metrics === "object" ? valuation.metrics : {};
  const score = valuation.score && typeof valuation.score === "object" ? valuation.score : {};
  const tone = strategyValuationTone(valuation);
  const discount = score.relative_per_discount_pct;
  return `
    <div class="strategy-valuation-strip">
      <span class="strategy-valuation-chip ${escapeHTML(tone)}">밸류 ${escapeHTML(strategyValuationLabel(valuation.label))}</span>
      <span class="strategy-valuation-chip">PER ${escapeHTML(formatMetric(metrics.per, "배", 2))}</span>
      <span class="strategy-valuation-chip">PBR ${escapeHTML(formatMetric(metrics.pbr, "배", 2))}</span>
      <span class="strategy-valuation-chip">업종 대비 ${escapeHTML(formatMetric(discount, "%", 1))}</span>
    </div>
  `;
}

function renderStrategyValuationDetail(row) {
  const valuation = row?.valuation && typeof row.valuation === "object" ? row.valuation : {};
  const metrics = valuation.metrics && typeof valuation.metrics === "object" ? valuation.metrics : {};
  const score = valuation.score && typeof valuation.score === "object" ? valuation.score : {};
  const reasons = Array.isArray(valuation.reasons) ? valuation.reasons.slice(0, 4) : [];
  const risks = Array.isArray(valuation.risks) ? valuation.risks.slice(0, 4) : [];
  const status = String(valuation.status || "missing");
  if (status !== "ok") {
    return `
      <div class="strategy-detail-section strategy-valuation-detail">
        <strong>밸류에이션 근거</strong>
        <ul><li>${escapeHTML(status === "error" ? "수집 오류 상태입니다." : "아직 종목분석 DB에 최신 데이터가 없습니다.")}</li></ul>
      </div>
      <div class="strategy-detail-section strategy-valuation-detail">
        <strong>밸류 리스크</strong>
        <ul><li>밸류 갱신 후 가격 부담/매력도를 보조 신호로 확인합니다.</li></ul>
      </div>
    `;
  }
  return `
    <div class="strategy-detail-section strategy-valuation-detail">
      <strong>밸류에이션 근거</strong>
      <div class="strategy-valuation-metrics">
        <span>PER <b>${escapeHTML(formatMetric(metrics.per, "배", 2))}</b></span>
        <span>PBR <b>${escapeHTML(formatMetric(metrics.pbr, "배", 2))}</b></span>
        <span>업종 PER <b>${escapeHTML(formatMetric(metrics.industry_per, "배", 2))}</b></span>
        <span>할인율 <b>${escapeHTML(formatMetric(score.relative_per_discount_pct, "%", 1))}</b></span>
      </div>
      <ul>${(reasons.length ? reasons : ["밸류 근거 추가 대기"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
    </div>
    <div class="strategy-detail-section strategy-valuation-detail">
      <strong>밸류 리스크</strong>
      <div class="strategy-valuation-metrics">
        <span>저평가 <b>${escapeHTML(String(score.undervalued_score || 0))}</b></span>
        <span>부담 <b>${escapeHTML(String(score.overvalued_risk || 0))}</b></span>
        <span>퀄리티 <b>${escapeHTML(String(score.quality_score || 0))}</b></span>
        <span>성장 <b>${escapeHTML(String(score.growth_score || 0))}</b></span>
      </div>
      <ul>${(risks.length ? risks : ["단독 매수 근거가 아니라 보조 신호로만 사용"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
    </div>
  `;
}

function renderStrategyCandidateDetail(row) {
  const facts = Array.isArray(row.facts) ? row.facts.slice(0, 5) : [];
  const citations = Array.isArray(row.citations) ? row.citations.slice(0, 5) : [];
  const reportIds = Array.isArray(row.report_ids) ? row.report_ids.slice(0, 8) : [];
  const components = row.score_components || {};
  const componentRows = [
    ["리포트", components.report],
    ["리서치", components.research],
    ["고래", components.whale],
    ["종가수급", components.after_close],
    ["밸류", components.valuation],
    ["최신성", components.recency],
    ["근거", components.evidence],
    ["적합도", components.fit],
    ["리스크", components.risk_penalty],
  ];
  const symbol = String(row.symbol || "").trim();
  const whyQuestion = `${symbol} 왜 후보인지 근거와 반론을 설명해줘`;
  const coverage = row?.data_coverage && typeof row.data_coverage === "object" ? row.data_coverage : {};
  const identity = row?.identity_status && typeof row.identity_status === "object" ? row.identity_status : {};
  const warnings = STRATEGY_INTEL_TAB.dataWarnings(row);
  return `
    <div class="strategy-candidate-detail">
      <div class="strategy-detail-section strategy-data-quality-detail">
        <strong>자료 신뢰도</strong>
        <div class="strategy-detail-components">
          <span>종목명 <b>${escapeHTML(identity.label || "-")}</b></span>
          <span>커버리지 <b>${escapeHTML(String(coverage.coverage_score ?? "-"))}</b></span>
          <span>소스 <b>${escapeHTML(String(coverage.source_count ?? "-"))}</b></span>
        </div>
        <ul>${(warnings.length ? warnings : ["자료 상태 양호"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
      </div>
      <div class="strategy-detail-section">
        <strong>근거 문단</strong>
        <ul>${(facts.length ? facts : ["RAG/리포트 근거 문장 추가 대기"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
      </div>
      <div class="strategy-detail-section">
        <strong>리포트/출처</strong>
        <ul>
          ${reportIds.length ? `<li>${escapeHTML(`리포트 ID: ${reportIds.join(", ")}`)}</li>` : "<li>연결된 리포트 ID 없음</li>"}
          ${(citations.length ? citations : ["근거 위치 추가 대기"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}
        </ul>
      </div>
      <div class="strategy-detail-section">
        <strong>점수 해부</strong>
        <div class="strategy-detail-components">
          ${componentRows.map(([label, value]) => `<span>${escapeHTML(label)} <b>${escapeHTML(String(Math.round(Number(value || 0))))}</b></span>`).join("")}
        </div>
      </div>
      ${STRATEGY_INTEL_TAB.renderSuitabilityDetail(row, { escapeHTML })}
      ${renderStrategyValuationDetail(row)}
      <div class="strategy-detail-actions">
        <button class="btn small" type="button" data-strategy-question="${escapeHTML(whyQuestion)}">이 후보로 다시 질문</button>
      </div>
    </div>
  `;
}

function renderStrategyCandidate(row, index) {
  const reasons = Array.isArray(row.reasons) ? row.reasons.slice(0, 3) : [];
  const risks = Array.isArray(row.risks) ? row.risks.slice(0, 2) : [];
  const checks = Array.isArray(row.checks) ? row.checks.slice(0, 3) : [];
  const sources = Array.isArray(row.sources) ? row.sources.slice(0, 5) : [];
  const score = Number(row.score || 0);
  const balanced = STRATEGY_INTEL_TAB.strategyHorizonPayload(row, "balanced");
  const confidence = Number(row.confidence || 0);
  const symbol = String(row.symbol || "").trim();
  const title = `${row.name || row.symbol || "후보"}${row.symbol ? ` (${row.symbol})` : ""}`;
  const stance = String(row.stance || "watch").toUpperCase();
  const selected = symbol && state.strategyIntel.selectedSymbol === symbol;
  return `
    <article class="strategy-candidate-card ${selected ? "selected" : ""}">
      <div class="strategy-candidate-rank">${escapeHTML(String(index + 1))}</div>
      <div class="strategy-candidate-main">
        <div class="strategy-candidate-head">
          <div>
            <span class="eyebrow">WATCHLIST</span>
            <h4>${escapeHTML(title)}</h4>
          </div>
          <div class="strategy-candidate-tools">
            <span class="strategy-score">
              <small>균형 적합도</small>
              ${escapeHTML(`${balanced.grade} / ${Math.round(balanced.score || score)}`)}
            </span>
            <button class="btn small" type="button" data-strategy-candidate-toggle="${escapeHTML(symbol)}">${selected ? "닫기" : "상세"}</button>
          </div>
        </div>
        <div class="strategy-candidate-meta">
          <span>${escapeHTML(stance)}</span>
          <span>${escapeHTML(`신뢰도 ${confidence}`)}</span>
          <span>${escapeHTML(`리스크 ${row.risk_score ?? "-"}`)}</span>
          ${sources.map((item) => `<span>${escapeHTML(item)}</span>`).join("")}
        </div>
        ${STRATEGY_INTEL_TAB.renderDataWarnings(row, { escapeHTML })}
        ${renderStrategyValuationChips(row)}
        ${STRATEGY_INTEL_TAB.renderSuitabilityBars(row, { escapeHTML })}
        ${STRATEGY_INTEL_TAB.renderScoreComponents(row, { escapeHTML })}
        <div class="strategy-intel-columns">
          <div>
            <strong>왜 보는가</strong>
            <ul>${(reasons.length ? reasons : ["근거 보강 필요"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
          </div>
          <div>
            <strong>확인 조건</strong>
            <ul>${(checks.length ? checks : ["가격/거래대금/섹터 수급 확인"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
          </div>
          <div>
            <strong>반론</strong>
            <ul>${(risks.length ? risks : ["리스크 추가 점검"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
          </div>
        </div>
        ${selected ? renderStrategyCandidateDetail(row) : ""}
      </div>
    </article>
  `;
}

function symbolAnalysisPayload(value) {
  if (!value || typeof value !== "object") return null;
  const analysis = value.analysis && typeof value.analysis === "object" ? value.analysis : null;
  return analysis || value;
}

function symbolAnalysisInputValue() {
  return String(state.symbolAnalysis.input || "").trim();
}

function symbolAnalysisList(value, limit = 8) {
  if (Array.isArray(value)) return value.slice(0, limit);
  if (value === null || value === undefined || value === "") return [];
  return [value];
}

function renderSymbolAnalysisBullets(title, items, fallback) {
  const rows = symbolAnalysisList(items);
  return `
    <div>
      <strong>${escapeHTML(title)}</strong>
      <ul>${(rows.length ? rows : [fallback]).map((item) => `<li>${escapeHTML(formatHelperValue(item))}</li>`).join("")}</ul>
    </div>
  `;
}

function renderSymbolAnalysisChips(label, items) {
  const rows = symbolAnalysisList(items, 10);
  if (!rows.length) return "";
  return `
    <div class="symbol-analysis-chip-line">
      <strong>${escapeHTML(label)}</strong>
      <div class="helper-chip-row">
        ${rows.map((item) => `<span class="symbol-analysis-chip">${escapeHTML(formatHelperValue(item))}</span>`).join("")}
      </div>
    </div>
  `;
}

function renderSymbolAnalysisResult(payload) {
  const row = symbolAnalysisPayload(payload);
  if (!row) {
    return '<div class="notice">분석 결과 또는 히스토리를 선택하면 여기에 표시됩니다.</div>';
  }
  const symbol = String(row.symbol || payload?.symbol || "").trim();
  const name = String(row.name || payload?.name || "").trim();
  const title = `${name || "종목"}${symbol ? ` (${symbol})` : ""}`;
  const confidence = row.confidence === null || row.confidence === undefined ? "-" : fmtNum(row.confidence, 2);
  const model = String(row.model || payload?.model || "-");
  const status = String(row.status || payload?.status || "-");
  const createdAt = row.created_at || payload?.created_at || row.updated_at || payload?.updated_at || "";
  return `
    <section class="symbol-analysis-result">
      <div class="helper-answer-head">
        <div>
          <span class="eyebrow">INSTANT SYMBOL</span>
          <h4>${escapeHTML(title)}</h4>
        </div>
        <span class="pill mono">${escapeHTML(`${model} · ${status}`)}</span>
      </div>
      <div class="strategy-intel-metrics symbol-analysis-metrics">
        <span><strong>${escapeHTML(String(row.stance || "-"))}</strong>stance</span>
        <span><strong>${escapeHTML(String(confidence))}</strong>confidence</span>
        <span><strong>${escapeHTML(createdAt ? fmtKST(createdAt, true) : "--")}</strong>created</span>
        <span><strong>${escapeHTML(String(row.trigger || payload?.trigger || "-"))}</strong>trigger</span>
      </div>
      <p class="symbol-analysis-summary">${escapeHTML(row.summary || "요약 대기")}</p>
      <div class="symbol-analysis-views">
        <article><span>SHORT</span><p>${escapeHTML(row.short_view || "-")}</p></article>
        <article><span>MID</span><p>${escapeHTML(row.mid_view || "-")}</p></article>
        <article><span>LONG</span><p>${escapeHTML(row.long_view || "-")}</p></article>
      </div>
      <div class="strategy-intel-columns symbol-analysis-columns">
        ${renderSymbolAnalysisBullets("근거", row.reasons, "근거 보강 대기")}
        ${renderSymbolAnalysisBullets("리스크", row.risks, "리스크 보강 대기")}
        ${renderSymbolAnalysisBullets("자료 공백", row.data_gaps, "자료 공백 없음")}
        ${renderSymbolAnalysisBullets("트리거", row.triggers, "확인 조건 대기")}
      </div>
      ${renderSymbolAnalysisChips("목표 후보", row.target_candidates)}
      ${renderSymbolAnalysisChips("손절 후보", row.stop_candidates)}
    </section>
  `;
}

function renderSymbolAnalysisHistory() {
  const history = state.symbolAnalysis.history;
  const items = Array.isArray(history?.items)
    ? history.items
    : Array.isArray(history)
      ? history
      : [];
  if (!history) return "";
  if (!items.length) {
    return '<div class="notice">최근 분석 히스토리가 없습니다.</div>';
  }
  return `
    <section class="symbol-analysis-history">
      <div class="helper-row-head">
        <strong>최근 분석</strong>
        <span class="helper-row-status muted">${escapeHTML(String(items.length))}건</span>
      </div>
      <div class="symbol-analysis-history-list">
        ${items
          .slice(0, 10)
          .map((item, index) => {
            const row = symbolAnalysisPayload(item) || {};
            const selected = state.symbolAnalysis.selectedHistoryIndex === index;
            const createdAt = row.created_at || item?.created_at || row.updated_at || "";
            return `
              <button class="symbol-analysis-history-item ${selected ? "active" : ""}" type="button" data-symbol-analysis-history-index="${escapeHTML(String(index))}">
                <span>${escapeHTML(row.trigger || item?.trigger || "history")}</span>
                <strong>${escapeHTML(`${row.stance || "-"} · ${formatHelperValue(row.confidence)}`)}</strong>
                <small>${escapeHTML(createdAt ? fmtKST(createdAt, true) : "--")}</small>
                <p>${escapeHTML(truncateWithEllipsis(row.summary || item?.summary || "-", 96))}</p>
              </button>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function renderSymbolAnalysisSpecialWatch() {
  const payload = state.symbolAnalysis.specialWatch;
  if (!payload) return "";
  const items = Array.isArray(payload?.items) ? payload.items : [];
  if (!items.length) {
    return '<div class="notice">특별대상 종목이 없습니다.</div>';
  }
  return `
    <section class="symbol-analysis-watch">
      <div class="helper-row-head">
        <strong>특별대상</strong>
        <span class="helper-row-status ok">${escapeHTML(String(items.length))}종목</span>
      </div>
      <div class="helper-chip-row">
        ${items
          .map((item) => {
            const symbol = String(item.symbol || "").trim();
            const label = `${item.name || "종목"}${symbol ? ` (${symbol})` : ""}`;
            const meta = [item.reason, item.status].filter((value) => value).join(" · ");
            return `
              <button class="symbol-analysis-watch-chip" type="button" data-symbol-analysis-symbol="${escapeHTML(symbol || item.name || "")}">
                <strong>${escapeHTML(label)}</strong>
                <span>${escapeHTML(meta || "existing-position/adopted")}</span>
              </button>
            `;
          })
          .join("")}
      </div>
    </section>
  `;
}

function renderSymbolAnalysisPanel() {
  const inputValue = escapeHTML(state.symbolAnalysis.input);
  const runDisabled = state.symbolAnalysis.running ? "disabled" : "";
  const loadDisabled = state.symbolAnalysis.loading ? "disabled" : "";
  const busyHtml = state.symbolAnalysis.running
    ? '<div class="notice">종목 즉석 분석 실행 중입니다. 데이터 수집과 LLM 호출이 포함됩니다.</div>'
    : state.symbolAnalysis.loading
      ? '<div class="notice">종목 분석 데이터를 불러오는 중입니다.</div>'
      : "";
  const errorHtml = state.symbolAnalysis.error
    ? `<div class="notice">종목 분석 실패: ${escapeHTML(state.symbolAnalysis.error)}</div>`
    : "";
  return `
    <section class="symbol-analysis-panel">
      <div class="helper-row-head">
        <div>
          <span class="eyebrow">SYMBOL ANALYSIS</span>
          <h4>종목 즉석 분석</h4>
        </div>
        <span class="helper-row-status muted">admin</span>
      </div>
      <form class="symbol-analysis-command" id="symbolAnalysisForm">
        <label>
          6자리 코드 또는 회사명
          <input id="symbolAnalysisInput" type="text" inputmode="search" autocomplete="off" placeholder="예: 033790 또는 스피어파워" value="${inputValue}" />
        </label>
        <div class="strategy-intel-actions compact">
          <button class="btn primary" type="submit" ${runDisabled}>${state.symbolAnalysis.running ? "실행 중" : "분석 실행"}</button>
          <button class="btn" type="button" data-symbol-analysis-action="history" ${loadDisabled}>히스토리</button>
          <button class="btn" type="button" data-symbol-analysis-action="special_watch" ${loadDisabled}>특별대상</button>
        </div>
      </form>
      ${busyHtml}
      ${errorHtml}
      ${renderSymbolAnalysisSpecialWatch()}
      ${renderSymbolAnalysisResult(state.symbolAnalysis.result)}
      ${renderSymbolAnalysisHistory()}
    </section>
  `;
}

function renderStrategyIntelTab() {
  const result = state.strategyIntel.result;
  const quick = strategyIntelQuickQuestions()
    .map((item) => `<button class="helper-chip" type="button" data-strategy-question="${escapeHTML(item)}">${escapeHTML(item)}</button>`)
    .join("");
  const queryValue = escapeHTML(state.strategyIntel.query);
  const disabled = state.strategyIntel.loading ? "disabled" : "";
  const collectDisabled = state.strategyIntel.collectLoading ? "disabled" : "";
  const valuationCollectDisabled = state.strategyIntel.valuationCollectLoading ? "disabled" : "";
  const loadingHtml = state.strategyIntel.loading ? '<div class="notice">전략 인텔리전스 분석 중입니다.</div>' : "";
  const errorHtml = state.strategyIntel.error
    ? `<div class="notice">전략 분석 실패: ${escapeHTML(state.strategyIntel.error)}</div>`
    : "";
  const collectHtml = [
    STRATEGY_INTEL_TAB.renderCollectResult(
      state.strategyIntel.collectResult,
      state.strategyIntel.collectError,
      { escapeHTML, sourceTone },
    ),
    STRATEGY_INTEL_TAB.renderFundamentalsCollectResult(
      state.strategyIntel.valuationCollectResult,
      state.strategyIntel.valuationCollectError,
      { escapeHTML, sourceTone, strategyValuationLabel },
    ),
  ].join("");

  if (!result) {
    return `
      <div class="strategy-intel-shell">
        <form class="strategy-intel-command" id="strategyIntelForm">
          <label>
            전략 질문
            <textarea id="strategyIntelQuery" rows="3" placeholder="예: 다음 거래일 관심 후보를 전략적으로 정리해줘">${queryValue}</textarea>
          </label>
          <div class="strategy-intel-actions">
            <button class="btn primary" type="submit" ${disabled}>빠른 스캔</button>
            <button class="btn" type="button" data-strategy-intel-action="llm" ${disabled}>AI 심층 브리핑</button>
            <button class="btn" type="button" data-strategy-intel-action="collect" ${collectDisabled}>시그널 수집</button>
            <button class="btn" type="button" data-strategy-intel-action="valuation_collect" ${valuationCollectDisabled}>밸류 갱신</button>
          </div>
          <div class="helper-chip-row">${quick}</div>
        </form>
        ${renderSymbolAnalysisPanel()}
        ${collectHtml}
        ${loadingHtml || errorHtml || '<div class="notice">전략 보드를 아직 불러오지 않았습니다.</div>'}
      </div>
    `;
  }

  const regime = result.regime || {};
  const intent = result.intent || {};
  const candidates = Array.isArray(result.candidates) ? result.candidates : [];
  const exclusions = Array.isArray(result.exclusions) ? result.exclusions.slice(0, 5) : [];
  const methods = Array.isArray(result.methodology) ? result.methodology.slice(0, 5) : [];
  const brief = String(result.brief_md || "");
  const sourceCount = Array.isArray(result.sources) ? result.sources.length : 0;
  return `
    <div class="strategy-intel-shell">
      <form class="strategy-intel-command" id="strategyIntelForm">
        <label>
          전략 질문
          <textarea id="strategyIntelQuery" rows="3" placeholder="예: 다음 거래일 관심 후보를 전략적으로 정리해줘">${queryValue}</textarea>
        </label>
        <div class="strategy-intel-actions">
          <button class="btn primary" type="submit" ${disabled}>빠른 스캔</button>
          <button class="btn" type="button" data-strategy-intel-action="llm" ${disabled}>AI 심층 브리핑</button>
          <button class="btn" type="button" data-strategy-intel-action="collect" ${collectDisabled}>${state.strategyIntel.collectLoading ? "수집 중" : "시그널 수집"}</button>
          <button class="btn" type="button" data-strategy-intel-action="valuation_collect" ${valuationCollectDisabled}>${state.strategyIntel.valuationCollectLoading ? "갱신 중" : "밸류 갱신"}</button>
        </div>
        <div class="helper-chip-row">${quick}</div>
      </form>
      ${renderSymbolAnalysisPanel()}
      ${collectHtml}
      ${loadingHtml}
      ${errorHtml}
      <section class="strategy-intel-hero">
        <div>
          <span class="eyebrow">STRATEGY INTELLIGENCE</span>
          <h4>${escapeHTML(regime.label || "mixed")}</h4>
          <p>${escapeHTML(regime.stance || "시장 판단 대기")}</p>
          ${STRATEGY_INTEL_TAB.renderDataHealth(result, { escapeHTML })}
        </div>
        <div class="strategy-intel-metrics">
          <span><strong>${escapeHTML(String(candidates.length))}</strong>후보</span>
          <span><strong>${escapeHTML(intent.label || "-")}</strong>의도</span>
          <span><strong>${escapeHTML(String(sourceCount))}</strong>소스</span>
          <span><strong>${escapeHTML(result.brief_mode || "scan")}</strong>모드</span>
        </div>
      </section>
      <section class="helper-answer-card">
        <div class="helper-answer-head">
          <div>
            <span class="eyebrow">BRIEF</span>
            <h4>${escapeHTML(truncateWithEllipsis(result.query || state.strategyIntel.query, 90))}</h4>
          </div>
          <span class="pill mono">${escapeHTML(result.model || "gpt-5.6-terra")}</span>
        </div>
        <pre class="helper-answer-text">${escapeHTML(brief)}</pre>
      </section>
      <section class="strategy-candidate-board">
        ${candidates.length ? candidates.map(renderStrategyCandidate).join("") : '<div class="notice">후보가 부족합니다.</div>'}
      </section>
      <div class="strategy-intel-grid">
        <article class="helper-card helper-card-wide">
          <h4>소스 상태</h4>
          ${STRATEGY_INTEL_TAB.renderSources(result.sources, { escapeHTML, sourceTone })}
        </article>
        <article class="helper-card">
          <h4>제외/보류</h4>
          <ul class="helper-plain-list">
            ${
              exclusions.length
                ? exclusions.map((row) => `<li>${escapeHTML(`${row.name || row.symbol}(${row.symbol}) · ${row.reason} · score ${row.score}`)}</li>`).join("")
                : "<li>제외 후보 없음</li>"
            }
          </ul>
        </article>
        <article class="helper-card">
          <h4>판단 방식</h4>
          <ul class="helper-plain-list">
            ${methods.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}
          </ul>
        </article>
      </div>
    </div>
  `;
}

function renderMemoryPolicyStrip(memory) {
  return MEMORY_TAB.renderPolicyStrip(memory, {
    escapeHTML,
    truncateWithEllipsis,
  });
}

function renderValidationPolicyScorecards(memory, policyRules = []) {
  const scorecards = (Array.isArray(memory?.policy_scorecards) ? memory.policy_scorecards : [])
    .filter((row) => String(row?.policy_id || "").startsWith("validation."));
  const rulesByPolicy = new Map(
    (Array.isArray(policyRules) ? policyRules : [])
      .filter((row) => String(row?.policy_id || "").startsWith("validation."))
      .map((row) => [String(row.policy_id || ""), row])
  );
  const body = scorecards.slice(0, 6).map((row) => {
    const policyId = String(row.policy_id || "");
    const discipline = row.discipline_id || policyId.replace("validation.", "") || "validation";
    const rule = rulesByPolicy.get(policyId) || {};
    const effect = rule.effect && typeof rule.effect === "object" ? rule.effect : {};
    const confidence = Number(row.confidence || 0);
    return `
      <article class="validation-policy-card ${escapeHTML(row.status || "candidate")}">
        <div>
          <span class="section-kicker">${escapeHTML(row.status || "candidate")} · ${escapeHTML(row.action || "observe")}</span>
          <strong>${escapeHTML(discipline)}</strong>
        </div>
        <div class="validation-policy-metrics">
          <span><b>${escapeHTML(fmtNum(row.sample_count || 0, 0))}</b>표본</span>
          <span><b>${escapeHTML(fmtNum(confidence * 100, 0))}%</b>확신</span>
          <span><b>${escapeHTML(fmtNum(row.avg_pnl_pct || 0, 2))}%</b>평균</span>
        </div>
        <p>${escapeHTML(truncateWithEllipsis(row.reason || "검증 실패가 반복되면 쥬는 진입 크기와 대기 조건을 조정합니다.", 180))}</p>
        <div class="validation-policy-effect">
          <span>${escapeHTML(effect.entry_bias || "review")}</span>
          <span>${escapeHTML(effect.target_stop_review || "target_stop_review")}</span>
          <span>hard_filter=${escapeHTML(String(effect.hard_filter ?? false))}</span>
        </div>
      </article>
    `;
  }).join("");
  return `
    <section class="memory-section validation-policy-panel">
      <div class="panel-head compact">
        <h3>19검증 학습 정책</h3>
        <p>실패한 자동매매 검증 항목이 반복될 때 쥬가 어떤 soft caution으로 운용을 조정하는지 보여줍니다.</p>
      </div>
      <div class="validation-policy-grid">
        ${body || '<div class="notice">아직 검증 실패 기반 정책 scorecard가 없습니다.</div>'}
      </div>
    </section>
  `;
}

function renderValidationRepairBacklog(memory) {
  const backlog = memory?.validation_repair_backlog
    || memory?.context_pack?.validation_repair_backlog
    || {};
  const items = Array.isArray(backlog.items) ? backlog.items : [];
  const status = String(backlog.status || (items.length ? "needs_repair" : "clear"));
  const body = items.slice(0, 8).map((row) => {
    const priority = String(row.priority || "p2").toLowerCase();
    const tone = priority === "p0" ? "danger" : priority === "p1" ? "warn" : "neutral";
    return `
      <article class="validation-repair-card ${tone}">
        <div class="validation-repair-head">
          <span class="strategy-data-chip">${escapeHTML(row.venue || "core")}</span>
          <span class="strategy-data-chip">${escapeHTML(priority.toUpperCase())}</span>
          <span class="strategy-data-chip">${escapeHTML(row.status || "weak")}</span>
        </div>
        <strong>${escapeHTML(row.label || row.discipline_id || "validation")}</strong>
        <p>${escapeHTML(truncateWithEllipsis(row.exit_criteria || row.lane_policy_hint || "다음 검증에서 pass로 회복될 때까지 증액을 보류합니다.", 180))}</p>
        <div class="validation-policy-effect">
          <span>${escapeHTML(row.owner || "validation_lab")}</span>
          <span>${escapeHTML(row.cadence || "next_validation_run")}</span>
          <span>${escapeHTML(row.blocks_scaling || "no_scale_up_until_validated")}</span>
        </div>
        <small>${escapeHTML(row.policy_id || row.event_key || "")}</small>
      </article>
    `;
  }).join("");
  return `
    <section class="memory-section validation-repair-panel">
      <div class="panel-head compact">
        <h3>19검증 수리 우선순위</h3>
        <p>현재 fail/warn 항목이 다음 블록의 수량, 진입 방식, 증액 조건에 어떻게 영향을 주는지 보여줍니다.</p>
      </div>
      <div class="validation-repair-summary">
        <span><b>${escapeHTML(status)}</b>상태</span>
        <span><b>${escapeHTML(backlog.item_count ?? items.length)}</b>수리 항목</span>
      </div>
      <div class="validation-repair-grid">
        ${body || '<div class="notice">현재 수리 대기 중인 19검증 항목이 없습니다.</div>'}
      </div>
    </section>
  `;
}

function renderValidationRepairOpsPanel(payload, scopeLabel = "쥬") {
  const ops = payload?.validation_repair_ops;
  if (!ops || typeof ops !== "object") return "";
  const topBacklog = Array.isArray(ops.top_backlog) ? ops.top_backlog : [];
  const topConstraints = Array.isArray(ops.top_constraints) ? ops.top_constraints : [];
  const recoveryItems = Array.isArray(ops.recovery?.items) ? ops.recovery.items : [];
  const backlogCount = Number(ops.backlog_count || 0);
  const constraintCount = Number(ops.constraint_count || 0);
  const status = String(ops.status || (backlogCount || constraintCount ? "needs_repair" : "clear"));
  if (!backlogCount && !constraintCount && status === "clear") return "";
  const rows = [
    ...topBacklog.map((row) => ({ ...row, source: "repair" })),
    ...topConstraints.map((row) => ({ ...row, source: "constraint" })),
  ].slice(0, 4);
  const cards = rows.map((row) => {
    const checks = Array.isArray(row.required_checks) ? row.required_checks : [];
    const tone = String(row.priority || "").toLowerCase() === "p0"
      || String(row.status || "").toLowerCase().includes("fail")
      ? "danger"
      : String(row.source || "") === "constraint"
        ? "warn"
        : "neutral";
    const budget = Number(row.risk_budget_multiplier || row.max_budget_multiplier || 0);
    const rr = Number(row.min_reward_risk || 0);
    return `
      <article class="validation-repair-card ${tone}">
        <div class="validation-repair-head">
          <span class="strategy-data-chip">${escapeHTML(row.source || "repair")}</span>
          <span class="strategy-data-chip">${escapeHTML(row.discipline_id || row.policy_id || "validation")}</span>
          ${row.scale_blocker ? `<span class="strategy-data-chip warn">${escapeHTML(row.scale_blocker)}</span>` : ""}
        </div>
        <strong>${escapeHTML(row.entry_bias || row.sizing_policy || "검증 수리 대기")}</strong>
        <p>${escapeHTML(truncateWithEllipsis(row.target_stop_review || row.sizing_policy || "수리 항목이 회복될 때까지 증액을 보류하고 대기·소액 진입 중심으로 운용합니다.", 170))}</p>
        <div class="validation-policy-effect">
          ${row.sizing_policy ? `<span>${escapeHTML(row.sizing_policy)}</span>` : ""}
          ${budget > 0 ? `<span>budget x${escapeHTML(fmtNum(budget, 2))}</span>` : ""}
          ${rr > 0 ? `<span>min R/R ${escapeHTML(fmtNum(rr, 2))}</span>` : ""}
          ${checks.slice(0, 2).map((check) => `<span>${escapeHTML(check)}</span>`).join("")}
        </div>
        <small>${escapeHTML(row.policy_id || "")}</small>
      </article>
    `;
  }).join("");
  const recoveryText = recoveryItems.length
    ? recoveryItems
        .slice(0, 3)
        .map((row) => {
          const responses = Array.isArray(row.current_jue_response)
            ? row.current_jue_response
            : row.current_jue_response
              ? [row.current_jue_response]
              : [];
          return `${row.discipline_id || row.policy_id || "validation"}: ${responses.join(", ")}`;
        })
        .join(" · ")
    : "";
  return `
    <section class="memory-section validation-repair-panel">
      <div class="panel-head compact">
        <h3>${escapeHTML(scopeLabel)} 19검증 수리 상태</h3>
        <p>FAIL/WARN 항목이 지금 블록 수량, 대기진입, 목표·손절 검토에 어떻게 내려오는지 보여줍니다.</p>
      </div>
      <div class="validation-repair-summary">
        <span><b>${escapeHTML(status)}</b>상태</span>
        <span><b>${escapeHTML(fmtNum(backlogCount, 0))}</b>수리 대기</span>
        <span><b>${escapeHTML(fmtNum(constraintCount, 0))}</b>설계 제약</span>
        <span><b>${escapeHTML(ops.scope || "-")}</b>scope</span>
      </div>
      <div class="validation-repair-grid">
        ${cards || '<div class="notice compact">현재 표시할 validation repair 제약이 없습니다.</div>'}
      </div>
      ${recoveryText ? `<p class="helper-text">${escapeHTML(recoveryText)}</p>` : ""}
    </section>
  `;
}

function firstArray(...values) {
  return values.find((value) => Array.isArray(value)) || [];
}

function renderEvidencePolicyFlow() {
  const flow = state.evidencePolicy;
  const status = flow.status && typeof flow.status === "object" ? flow.status : {};
  const context = flow.context && typeof flow.context === "object" ? flow.context : {};
  const sourceMap = status.sources && typeof status.sources === "object" && !Array.isArray(status.sources)
    ? status.sources
    : {};
  const sources = firstArray(context.sources, context.evidence_sources, context.evidence);
  const scorecards = firstArray(context.scorecards, context.policy_scorecards, context.pattern_scorecards);
  const policyRules = firstArray(context.policy_rules, status.policy_rules);
  const activePolicies = policyRules.filter((row) => {
    const statusText = String(row?.status || row?.state || "active").toLowerCase();
    return statusText === "active" || statusText.startsWith("active");
  });
  const sourceCountRaw = status.source_count ?? context.source_count ?? sources.length;
  const sourceCount = Number(sourceCountRaw || Object.keys(sourceMap).length || 0);
  const scorecardCount = Number(status.scorecard_count ?? context.scorecard_count ?? scorecards.length ?? 0);
  const activePolicyCount = Number(status.active_policy_count ?? context.active_policy_count ?? activePolicies.length ?? 0);
  const decisionPacket = context.decision_packet && typeof context.decision_packet === "object"
    ? context.decision_packet
    : {};
  const decisionLabel = status.decision_packet_label
    || context.decision_packet_label
    || decisionPacket.label
    || decisionPacket.packet_id
    || (flow.context ? "준비됨" : "대기");
  const busy = flow.loading ? "disabled" : "";
  const policyChips = policyRules.slice(0, 6).map((row) => {
    const label = row?.name || row?.rule_id || row?.policy_id || row?.action || "policy";
    const statusText = row?.status || row?.state || "active";
    return `<span class="evidence-policy-chip">${escapeHTML(label)}<small>${escapeHTML(statusText)}</small></span>`;
  }).join("");

  return `
    <section class="evidence-policy-flow">
      <div class="evidence-policy-head">
        <div>
          <span class="section-kicker">Evidence Policy Flow</span>
          <h3>근거에서 실행 판단까지</h3>
          <p>수집 근거, 스코어카드, 활성 정책, 결정 패킷을 한 흐름으로 봅니다.</p>
        </div>
        <button class="btn tiny ghost" type="button" data-evidence-policy-action="refresh" ${busy}>
          ${flow.loading ? "갱신 중..." : "새로고침"}
        </button>
      </div>
      ${flow.error ? `<div class="notice">근거 정책 조회 실패: ${escapeHTML(flow.error)}</div>` : ""}
      <div class="evidence-flow-grid">
        <article class="evidence-flow-step">
          <span>Evidence</span>
          <strong>${escapeHTML(fmtNum(sourceCount, 0))}</strong>
          <small>소스</small>
        </article>
        <article class="evidence-flow-step">
          <span>Scorecard</span>
          <strong>${escapeHTML(fmtNum(scorecardCount, 0))}</strong>
          <small>검증표</small>
        </article>
        <article class="evidence-flow-step">
          <span>Policy</span>
          <strong>${escapeHTML(fmtNum(activePolicyCount, 0))}</strong>
          <small>활성 정책</small>
        </article>
        <article class="evidence-flow-step decision">
          <span>Decision</span>
          <strong>${escapeHTML(decisionLabel)}</strong>
          <small>패킷</small>
        </article>
      </div>
      <div class="evidence-policy-chips">
        ${policyChips || '<span class="evidence-policy-chip">policy 대기<small>loading</small></span>'}
      </div>
    </section>
  `;
}

function renderPeriodReviewPanel() {
  const reviews = state.memoryReviews || {};
  const revisions = Array.isArray(state.memoryRevisions?.items) ? state.memoryRevisions.items : [];
  const busy = state.memoryReviewRunning ? "disabled" : "";
  const card = (label, review) => {
    const metrics = review?.metrics && typeof review.metrics === "object" ? review.metrics : {};
    return `
      <article class="period-review-card">
        <span class="eyebrow">${escapeHTML(label)}</span>
        <strong>${escapeHTML(review?.period_key || "missing")}</strong>
        <p>${escapeHTML(truncateWithEllipsis(review?.review_md || "아직 누적 반성이 없습니다.", 320))}</p>
        <div class="strategy-data-strip compact">
          <span class="strategy-data-chip">closed ${escapeHTML(String(metrics.closed_blocks ?? 0))}</span>
          <span class="strategy-data-chip">avg ${escapeHTML(fmtNum(metrics.avg_pnl_pct ?? 0, 2))}%</span>
        </div>
      </article>
    `;
  };
  return `
    <section class="memory-section period-review-panel">
      <div class="helper-row-head">
        <div>
          <span class="eyebrow">REFLECTION LOOP</span>
          <h4>주간/월간 운용 반성</h4>
          <p>반성 결과를 정책 개정안으로 바꿔 다음 블록 판단에 반영합니다.</p>
        </div>
        <div class="daily-discovery-actions">
          <button class="btn small" type="button" data-period-review="weekly" ${busy}>주간 반성 실행</button>
          <button class="btn small" type="button" data-period-review="monthly" ${busy}>월간 반성 실행</button>
        </div>
      </div>
      ${state.memoryReviewError ? `<div class="notice warn">${escapeHTML(state.memoryReviewError)}</div>` : ""}
      <div class="period-review-grid">
        ${card("weekly", reviews.weekly)}
        ${card("monthly", reviews.monthly)}
      </div>
      <div class="policy-revision-list">
        ${revisions.length ? revisions.map((row) => `
          <article class="policy-revision-chip">
            <strong>${escapeHTML(row.policy_id || "-")}</strong>
            <span>${escapeHTML(row.status || "-")} · ${escapeHTML(row.scope || "general")}</span>
            <p>${escapeHTML(truncateWithEllipsis(row.reason_md || "", 220))}</p>
          </article>
        `).join("") : '<div class="notice compact">정책 개정안 없음</div>'}
      </div>
    </section>
  `;
}

function renderDecisionSkills(memoryStatus) {
  return MEMORY_TAB.renderDecisionSkills(memoryStatus, {
    escapeHTML,
  });
}

function renderMemoryJournalCard(row) {
  return MEMORY_TAB.renderJournalCard(row, {
    escapeHTML,
    truncateWithEllipsis,
    registerHelperDetail,
  });
}

function renderInvestmentMemoryTab(scope = memoryScopeForTab()) {
  const memory = state.investmentMemory;
  const isBinance = scope === "binance";
  const scopeTitle = isBinance ? "Binance 쥬 메모리" : "KIS 쥬 메모리";
  const scopeDescription = isBinance
    ? "크립토 현물/선물 블록, 레짐, 퀀트·라이브 검증, Binance 반성을 분리해서 축적합니다."
    : "국장 KIS 블록, 리포트/RAG/밸류/수급, 장전·장중·마감 루틴을 분리해서 축적합니다.";
  const errorHtml = state.investmentMemoryError
    ? `<div class="notice">메모리 조회 실패: ${escapeHTML(state.investmentMemoryError)}</div>`
    : "";
  if (!memory) {
    return `${errorHtml || '<div class="notice">투자 메모리를 불러오는 중입니다.</div>'}`;
  }
  const journals = Array.isArray(memory.journals) ? memory.journals : [];
  const latest = Array.isArray(memory.latest_journals) ? memory.latest_journals : [];
  const status = memory.context_pack || {};
  const memoryOps = state.opsReadiness?.memory || {};
  const policyRules = Array.isArray(memory.policy_rules) ? memory.policy_rules : [];
  const activePolicyRules = policyRules.filter((row) => String(row?.status || "").startsWith("active"));
  const busy = state.investmentMemoryRunning ? "disabled" : "";
  return `
    <div class="memory-shell">
      <div class="strategy-intel-actions">
        <button class="btn primary" type="button" data-memory-action="refresh" ${busy}>새로고침</button>
        <button class="btn warm" type="button" data-memory-action="seed_current" ${busy}>현재 상태 시드</button>
        <button class="btn" type="button" data-memory-action="run_due_reflections" ${busy}>반성 처리</button>
        <button class="btn" type="button" data-memory-action="pre_open" ${busy}>장전 마음가짐</button>
        <button class="btn" type="button" data-memory-action="midday" ${busy}>장중 점검</button>
        <button class="btn" type="button" data-memory-action="post_close" ${busy}>마감 리뷰</button>
        <button class="btn warm" type="button" data-memory-action="block_reflection" ${busy}>블록 반성</button>
      </div>
      ${errorHtml}
      <section class="memory-hero">
        <div>
          <span class="section-kicker">Growing Agent Memory</span>
          <h4>${escapeHTML(memory.scope_label || scopeTitle)}</h4>
          <p>${escapeHTML(scopeDescription)}</p>
        </div>
        <div class="block-trader-kpis">
          <span><strong>${escapeHTML(memory.memory_scope || scope)}</strong>Scope</span>
          <span><strong>${escapeHTML(memory.trading_day || memory.today || "-")}</strong>기준일</span>
          <span><strong>${escapeHTML(memory.active_policies?.length ?? 0)}</strong>활성 원칙</span>
          <span><strong>${escapeHTML(memoryOps.seeded ? "완료" : "필요")}</strong>Seed</span>
          <span><strong>${escapeHTML(memoryOps.reflection_count ?? memory.recent_reflections?.length ?? 0)}</strong>반성</span>
          <span><strong>${escapeHTML(memoryOps.scorecard_count ?? memory.policy_scorecards?.length ?? 0)}</strong>Scorecard</span>
          <span><strong>${escapeHTML(memoryOps.active_policy_rule_count ?? activePolicyRules.length)}</strong>Active Rule</span>
          <span><strong>${escapeHTML(journals.length)}</strong>오늘 저널</span>
          <span><strong>${escapeHTML(status.status || "ok")}</strong>컨텍스트</span>
        </div>
      </section>
      ${renderEvidencePolicyFlow()}
      <section class="memory-section">
        <div class="panel-head compact">
          <h3>오늘 적용 중인 운용 원칙</h3>
        </div>
        ${renderMemoryPolicyStrip(memory)}
      </section>
      ${renderValidationRepairBacklog(memory)}
      ${renderValidationPolicyScorecards(memory, policyRules)}
      ${renderPeriodReviewPanel()}
      <section class="memory-section">
        <div class="panel-head compact">
          <h3>쥬 판단 스킬</h3>
        </div>
        ${renderDecisionSkills(memory)}
      </section>
      ${MEMORY_TAB.renderJueSourceManifestPanel({
        sourceManifest: state.jueSourceManifest,
        sourceManifestError: state.jueSourceManifestError,
      }, {
        escapeHTML,
      })}
      ${MEMORY_TAB.renderJueLifecyclePanel({
        lifecycleLatest: state.jueLifecycleLatest,
        lifecycleError: state.jueLifecycleError,
      }, {
        escapeHTML,
        truncateWithEllipsis,
        registerHelperDetail,
      })}
      <section class="memory-board">
        ${journals.length ? journals.map(renderMemoryJournalCard).join("") : '<div class="notice">오늘 저널이 아직 없습니다. 장전 마음가짐부터 생성해보세요.</div>'}
      </section>
      <section class="helper-grid">
        <article class="helper-card">
          <h4>최근 저널</h4>
          <ul class="helper-list">
            ${(latest.length ? latest : journals).slice(0, 5).map((row) => `
              <li>${escapeHTML(row.slot_label || row.slot || "-")} · ${escapeHTML(row.title || "-")}</li>
            `).join("") || "<li>최근 저널 없음</li>"}
          </ul>
        </article>
        <article class="helper-card">
          <h4>페르소나</h4>
          <p class="helper-text">${escapeHTML(truncateWithEllipsis(status.persona || "친근하지만 과열 매매를 막아주는 한국장 투자 파트너", 320))}</p>
        </article>
        <article class="helper-card">
          <h4>정책 성과 카드</h4>
          <ul class="helper-list">
            ${(Array.isArray(memory.policy_scorecards) ? memory.policy_scorecards : []).slice(0, 5).map((row) => `
              <li>${escapeHTML(row.policy_id || "-")} · ${escapeHTML(row.status || "candidate")} · 표본 ${escapeHTML(row.sample_count ?? 0)} · 평균 ${escapeHTML(fmtNum(row.avg_pnl_pct ?? 0, 2))}%</li>
            `).join("") || "<li>성과 카드 없음</li>"}
          </ul>
        </article>
        <article class="helper-card">
          <h4>버전 정책 룰</h4>
          <ul class="helper-list">
            ${policyRules.slice(0, 5).map((row) => `
              <li>${escapeHTML(row.rule_id || row.policy_id || "-")} · ${escapeHTML(row.status || "candidate")} · ${escapeHTML(row.action || "observe")}</li>
            `).join("") || "<li>정책 룰 없음</li>"}
          </ul>
        </article>
        <article class="helper-card">
          <h4>최근 블록 반성</h4>
          <ul class="helper-list">
            ${(Array.isArray(memory.recent_reflections) ? memory.recent_reflections : []).slice(0, 5).map((row) => `
              <li>${escapeHTML(row.block_id || "-")} · ${escapeHTML(row.symbol || "-")} · ${escapeHTML(fmtNum(row.pnl_pct ?? 0, 2))}%</li>
            `).join("") || "<li>반성 기록 없음</li>"}
          </ul>
        </article>
      </section>
      <p class="strategy-footnote">메모리는 LLM 매니저의 판단 보조 자료입니다. kill switch, 현금/보유수량 제한, 중복주문 방지는 항상 우선합니다.</p>
    </div>
  `;
}

function liveAuthorityForVenue(venue, fallback = null) {
  const payload = state.liveAuthority;
  const fromApi = payload?.venues && typeof payload.venues === "object"
    ? payload.venues[venue]
    : null;
  return (fromApi && typeof fromApi === "object")
    ? fromApi
    : (fallback && typeof fallback === "object" ? fallback : {});
}

function formatActiveRevisionEvidenceLabel(status) {
  const key = String(status || "").trim();
  return UI_SHARED.activeRevisionEvidenceLabels?.[key] || key.replaceAll("_", " ");
}

function activeRevisionEvidenceTone(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (normalized === "active_revision_evidence_present") return "good";
  if (
    [
      "active_revision_samples_pending_close",
      "insufficient_active_revision_samples",
      "active_revision_scorecards_missing",
      "no_active_revision_samples",
    ].includes(normalized)
  ) {
    return "warn";
  }
  if (normalized === "active_revision_blocked_by_validation") return "bad";
  return "muted";
}

function repairExecutionTone(status) {
  const normalized = String(status || "").trim().toLowerCase();
  if (["executed", "observed_external_runner", "ok", "complete", "completed"].includes(normalized)) return "good";
  if (normalized.startsWith("queued") || ["pending", "running", "partial"].includes(normalized)) return "warn";
  if (["error", "failed", "blocked"].includes(normalized)) return "bad";
  return "muted";
}

function renderBlockValidationChips(metadata) {
  const source = metadata && typeof metadata === "object" ? metadata : {};
  const authority = source.live_authority && typeof source.live_authority === "object"
    ? source.live_authority
    : {};
  if (!Object.keys(authority).length) return "";
  const matrix = authority.discipline_matrix && typeof authority.discipline_matrix === "object"
    ? authority.discipline_matrix
    : {};
  const summary = matrix.summary && typeof matrix.summary === "object" ? matrix.summary : {};
  const statuses = Array.isArray(matrix.statuses) ? matrix.statuses : [];
  const countByStatus = (status) => statuses.filter((row) => String(row?.status || "").toLowerCase() === status).length;
  const passCount = summary.pass_count ?? countByStatus("pass");
  const warnCount = summary.warn_count ?? countByStatus("warn");
  const failCount = summary.fail_count ?? countByStatus("fail");
  const missingCount = summary.missing_count ?? countByStatus("missing");
  const gateStatus = authority.validation_gate_status || summary.readiness || "";
  const reason = authority.validation_gate_reason || "";
  const expectedCount = matrix.expected_count || authority.expected_discipline_count || 19;
  const actualCount = matrix.actual_count || statuses.length || authority.discipline_count || 0;
  const rowDetailCount = matrix.row_detail_count === null || matrix.row_detail_count === undefined
    ? null
    : asNumber(matrix.row_detail_count, 0);
  const rowDetailComplete = Boolean(matrix.row_detail_complete);
  const rowDetailLabel = rowDetailCount === null
    ? ""
    : ` · row 상세 ${fmtNum(rowDetailCount, 0)}/${fmtNum(expectedCount || 19, 0)}${rowDetailComplete ? "" : " 부분"}`;
  const chips = [];
  if (gateStatus) {
    chips.push(
      `<span class="strategy-data-chip ${escapeHTML(tradingValidationTone(gateStatus))}" title="${escapeHTML(formatValidationGateReason(reason))}">검증 ${escapeHTML(formatValidationGateLabel(gateStatus))}</span>`
    );
  }
  if (actualCount || passCount || warnCount || failCount || missingCount) {
    chips.push(
      `<span class="strategy-data-chip neutral" title="${escapeHTML(`19개 자동매매 검증 스냅샷${rowDetailLabel}`)}">P ${escapeHTML(fmtNum(passCount || 0, 0))} · W ${escapeHTML(fmtNum(warnCount || 0, 0))} · F ${escapeHTML(fmtNum(failCount || 0, 0))} · M ${escapeHTML(fmtNum(missingCount || 0, 0))} · ${escapeHTML(fmtNum(actualCount || 0, 0))}/${escapeHTML(fmtNum(expectedCount || 19, 0))}${escapeHTML(rowDetailLabel)}</span>`
    );
  }
  return chips.join("");
}

function renderValidationPassportChips(metadata) {
  const source = metadata && typeof metadata === "object" ? metadata : {};
  const authority = source.live_authority && typeof source.live_authority === "object"
    ? source.live_authority
    : {};
  const passport = authority.validation_passport && typeof authority.validation_passport === "object"
    ? authority.validation_passport
    : {};
  if (!Object.keys(passport).length) return "";

  const status = String(passport.status || passport.readiness || "").trim();
  const tone = tradingValidationTone(status);
  const score = passport.score === null || passport.score === undefined ? null : asNumber(passport.score, 0);
  const expectedCount = asNumber(passport.expected_count ?? 19, 19);
  const actualCount = asNumber(passport.actual_count ?? 0, 0);
  const rowDetailCount = passport.row_detail_count === null || passport.row_detail_count === undefined
    ? null
    : asNumber(passport.row_detail_count, 0);
  const rowDetailComplete = Boolean(passport.row_detail_complete);
  const rowDetailLabel = rowDetailCount === null
    ? ""
    : `row 상세 ${fmtNum(rowDetailCount, 0)}/${fmtNum(expectedCount || 19, 0)}${rowDetailComplete ? "" : " 부분"}`;
  const failedIds = Array.isArray(passport.failed_ids) ? passport.failed_ids.map((value) => String(value)) : [];
  const weakIds = Array.isArray(passport.weak_ids) ? passport.weak_ids.map((value) => String(value)) : [];
  const requiresRevalidation = Boolean(passport.requires_revalidation);
  const riskAction = passport.risk_governor_action || "";
  const title = [
    `검증 여권 ${passport.version || "v1"}`,
    status ? `상태 ${formatValidationGateLabel(status)}` : "",
    `커버리지 ${fmtNum(actualCount, 0)}/${fmtNum(expectedCount || 19, 0)}`,
    rowDetailLabel,
    score === null ? "" : `점수 ${fmtNum(score, 1)}`,
    failedIds.length ? `실패 ${failedIds.slice(0, 6).join(", ")}` : "",
    weakIds.length ? `취약 ${weakIds.slice(0, 6).join(", ")}` : "",
    riskAction ? `Risk governor ${formatRiskGovernorLabel(riskAction)}` : "",
  ].filter(Boolean).join(" · ");
  const mainLabel = requiresRevalidation
    ? "검증 여권 재검증"
    : `검증 여권 ${formatValidationGateLabel(status || "pass")}`;
  const scoreLabel = score === null
    ? `${fmtNum(actualCount, 0)}/${fmtNum(expectedCount || 19, 0)}${rowDetailLabel ? ` · ${rowDetailLabel}` : ""}`
    : `${fmtNum(actualCount, 0)}/${fmtNum(expectedCount || 19, 0)} · ${fmtNum(score, 1)}${rowDetailLabel ? ` · ${rowDetailLabel}` : ""}`;

  return `
    <span class="strategy-data-chip block-validation-passport-chip ${escapeHTML(tone)}" title="${escapeHTML(title)}">${escapeHTML(mainLabel)}</span>
    <span class="strategy-data-chip block-validation-passport-chip neutral" title="${escapeHTML(title)}">${escapeHTML(scoreLabel)}</span>
  `;
}

function policyEffectFieldLabel(field) {
  const labels = {
    entry_style: "진입",
    target_price: "목표",
    stop_price: "손절",
    qty: "수량",
    risk_note: "리스크",
  };
  return labels[String(field || "").trim()] || String(field || "").trim();
}

function renderBlockPolicyEffectChips(metadata) {
  const source = metadata && typeof metadata === "object" ? metadata : {};
  const audit = source.policy_effect_audit && typeof source.policy_effect_audit === "object"
    ? source.policy_effect_audit
    : {};
  if (!Object.keys(audit).length) return "";
  const rules = Array.isArray(audit.rules) ? audit.rules : [];
  const fields = Array.isArray(audit.affected_fields)
    ? audit.affected_fields.map(policyEffectFieldLabel).filter(Boolean)
    : [];
  const versions = Array.isArray(source.applied_policy_versions)
    ? source.applied_policy_versions.map((value) => String(value || "")).filter(Boolean)
    : rules.map((row) => String(row?.rule_id || row?.policy_id || "")).filter(Boolean);
  const title = [
    `정책 반영 ${audit.version || "v1"}`,
    audit.mode ? `모드 ${audit.mode}` : "",
    fields.length ? `영향 축 ${fields.join(", ")}` : "",
    versions.length ? `룰 ${versions.slice(0, 6).join(", ")}` : "",
  ].filter(Boolean).join(" · ");
  const fieldLabel = fields.length ? fields.slice(0, 4).join("/") : "감사";
  const countLabel = versions.length ? `${versions.length}룰` : `${rules.length}룰`;
  return `
    <span class="strategy-data-chip block-policy-chip warn" title="${escapeHTML(title)}">정책 반영 ${escapeHTML(fieldLabel)}</span>
    <span class="strategy-data-chip block-policy-chip neutral" title="${escapeHTML(title)}">${escapeHTML(countLabel)}</span>
  `;
}

function renderBlockCostFeasibilityChips(metadata) {
  const source = metadata && typeof metadata === "object" ? metadata : {};
  const cost = source.cost_feasibility && typeof source.cost_feasibility === "object"
    ? source.cost_feasibility
    : {};
  if (!Object.keys(cost).length) return "";
  const status = String(cost.status || "").toLowerCase();
  const tone = status === "pass" ? "good" : status === "warn" ? "warn" : status === "fail" ? "bad" : "neutral";
  const netTarget = Number(cost.net_target_profit_after_cost_krw || 0);
  const costMultiple = Number(cost.target_cost_multiple || 0);
  const designNote = String(cost.design_note || "");
  const title = [
    designNote,
    `목표 총익 ${fmtKRW(cost.gross_target_profit_krw || 0)}원`,
    `왕복비용 ${fmtKRW(cost.target_round_trip_cost_krw || 0)}원`,
    `비용 후 손절손실 ${fmtKRW(cost.net_stop_loss_after_cost_krw || 0)}원`,
  ].filter(Boolean).join(" · ");
  const netLabel = `${netTarget >= 0 ? "+" : ""}${fmtKRW(netTarget)}원`;
  return `
    <span class="strategy-data-chip block-cost-chip ${escapeHTML(tone)}" title="${escapeHTML(title)}">비용 후 ${escapeHTML(netLabel)}</span>
    <span class="strategy-data-chip block-cost-chip neutral" title="${escapeHTML(title)}">비용배수 ${escapeHTML(fmtNum(costMultiple, 2))}x</span>
  `;
}

function mergeTradingValidationWithGateMatrix(tradingValidation, validationGate) {
  const source = tradingValidation && typeof tradingValidation === "object" ? tradingValidation : {};
  const matrix = validationGate?.discipline_matrix && typeof validationGate.discipline_matrix === "object"
    ? validationGate.discipline_matrix
    : {};
  if (!Object.keys(matrix).length) return source;
  const nestedPayload = source.payload && typeof source.payload === "object" ? source.payload : {};
  const existingSummary = nestedPayload.summary && typeof nestedPayload.summary === "object"
    ? nestedPayload.summary
    : (source.summary && typeof source.summary === "object" ? source.summary : {});
  const existingDisciplines = Array.isArray(nestedPayload.disciplines)
    ? nestedPayload.disciplines
    : (Array.isArray(source.disciplines) ? source.disciplines : []);
  const existingMetrics = nestedPayload.metrics && typeof nestedPayload.metrics === "object"
    ? nestedPayload.metrics
    : (source.metrics && typeof source.metrics === "object" ? source.metrics : {});
  const matrixSummary = matrix.summary && typeof matrix.summary === "object" ? matrix.summary : {};
  const matrixStatuses = Array.isArray(matrix.statuses) ? matrix.statuses : [];
  const fallbackSummary = {
    total_score: matrixSummary.total_score ?? matrixSummary.score,
    readiness: matrixSummary.readiness,
    pass_count: matrixSummary.pass_count,
    warn_count: matrixSummary.warn_count,
    fail_count: matrixSummary.fail_count,
    missing_count: matrixSummary.missing_count,
  };
  const summary = Object.keys(existingSummary).length ? existingSummary : fallbackSummary;
  const disciplines = existingDisciplines.length ? existingDisciplines : matrixStatuses;
  const gateLaneScorecards = validationGate?.lane_scorecards && typeof validationGate.lane_scorecards === "object"
    ? validationGate.lane_scorecards
    : null;
  const metrics = gateLaneScorecards && !existingMetrics.lane_scorecards
    ? { ...existingMetrics, lane_scorecards: gateLaneScorecards }
    : existingMetrics;
  return {
    ...source,
    summary,
    discipline_matrix: source.discipline_matrix || matrix,
    metrics,
    payload: {
      ...nestedPayload,
      summary,
      disciplines,
      metrics,
      discipline_matrix: nestedPayload.discipline_matrix || matrix,
    },
  };
}

function renderTradingValidationDetails(payload, labels = {}) {
  const source = payload && typeof payload === "object" ? payload : {};
  const nestedPayload = source.payload && typeof source.payload === "object"
    ? source.payload
    : null;
  const validation = nestedPayload || source;
  const summary = validation.summary && typeof validation.summary === "object"
    ? validation.summary
    : (source.summary && typeof source.summary === "object" ? source.summary : {});
  const disciplineMatrix = validation.discipline_matrix && typeof validation.discipline_matrix === "object"
    ? validation.discipline_matrix
    : (source.discipline_matrix && typeof source.discipline_matrix === "object" ? source.discipline_matrix : {});
  const metrics = validation.metrics && typeof validation.metrics === "object"
    ? validation.metrics
    : {};
  const capacity = metrics.capacity && typeof metrics.capacity === "object"
    ? metrics.capacity
    : {};
  const failureAttribution = metrics.failure_attribution && typeof metrics.failure_attribution === "object"
    ? metrics.failure_attribution
    : {};
  const patternLab = metrics.pattern_lab && typeof metrics.pattern_lab === "object"
    ? metrics.pattern_lab
    : {};
  const laneScorecards = metrics.lane_scorecards && typeof metrics.lane_scorecards === "object"
    ? metrics.lane_scorecards
    : {};
  const activeRevisionEvidence = metrics.active_revision_evidence && typeof metrics.active_revision_evidence === "object"
    ? metrics.active_revision_evidence
    : {};
  const remediationPlan = validation.remediation_plan && typeof validation.remediation_plan === "object"
    ? validation.remediation_plan
    : {};
  const sourceScope = String(patternLab.source_scope || "").trim();
  const patternStatus = String(patternLab.status || "").trim();
  const validationSourceLabel = sourceScope === "kis_live_forward_proxy"
    ? "KIS live-forward proxy"
    : (
      sourceScope
        ? sourceScope
        : (
          patternLab.db_path
            ? "crypto pattern lab"
            : "validation samples"
        )
    );
  const validationSourceDetail = sourceScope === "kis_live_forward_proxy"
    ? "국장 실거래 블록 표본만 사용"
    : (
      patternLab.optimized_set_count !== undefined
        ? `optimized sets ${fmtNum(patternLab.optimized_set_count, 0)}`
        : (patternLab.reason || patternLab.note || "source scope 대기")
    );
  const disciplines = Array.isArray(validation.disciplines) ? validation.disciplines : [];
  const operatorGuidance = Array.isArray(validation.operator_guidance)
    ? validation.operator_guidance
    : [];
  const priority = { fail: 0, missing: 1, warn: 2, pass: 3 };
  const weakDisciplines = disciplines
    .filter((row) => String(row?.status || "missing") !== "pass")
    .sort((left, right) => (
      (priority[String(left?.status || "missing")] ?? 9)
      - (priority[String(right?.status || "missing")] ?? 9)
    ))
    .slice(0, 6);
  const score = summary.total_score ?? summary.score ?? validation.total_score;
  const readiness = summary.readiness || "-";
  const validationAgeSec = validation.age_sec ?? source.age_sec;
  const validationStale = validation.stale ?? source.stale;
  const validationStaleReason = validation.stale_reason ?? source.stale_reason;
  const freshness = validationAgeSec === null || validationAgeSec === undefined
    ? "-"
    : `${fmtDurationSec(validationAgeSec)} 전`;
  const staleWarningHtml = validationStale
    ? `
      <div class="trading-validation-capacity bad">
        <span>검증 오래됨</span>
        <strong>freshness 재확인 필요</strong>
        <p>${escapeHTML(validationStaleReason || "stale validation payload")}</p>
      </div>
    `
    : "";
  const capacityExamples = Array.isArray(capacity.examples) ? capacity.examples : [];
  const tightest = capacityExamples[0] && typeof capacityExamples[0] === "object"
    ? capacityExamples[0]
    : {};
  const tightestSymbol = capacity.tightest_symbol || tightest.symbol || "";
  const tightestBlock = capacity.tightest_block_id || tightest.block_id || "";
  const capacityRatio = capacity.min_capacity_ratio ?? tightest.capacity_ratio;
  const capacitySource = capacity.capacity_source || tightest.capacity_source || capacity.capacity_method || "";
  const summaryWeakCount = (
    Number(summary.warn_count || 0)
    + Number(summary.fail_count || 0)
    + Number(summary.missing_count || 0)
  );
  const summaryWeakRows = !weakDisciplines.length && summaryWeakCount > 0;
  const weakRows = weakDisciplines.length
    ? weakDisciplines.map((row) => {
      const status = String(row?.status || "missing");
      const action = row?.action || row?.purpose || "-";
      return `
        <article class="trading-validation-row ${escapeHTML(tradingValidationTone(status))}">
          <span>${escapeHTML(status)}</span>
          <strong>${escapeHTML(row?.label || row?.id || "-")}</strong>
          <p>${escapeHTML(truncateWithEllipsis(action, 120))}</p>
        </article>
      `;
    }).join("")
    : summaryWeakRows
      ? `<article class="trading-validation-row warn"><span>summary</span><strong>summary 기준 취약 항목</strong><p>세부 row 대기 중입니다. summary에는 warn ${escapeHTML(fmtNum(summary.warn_count ?? 0, 0))}, fail ${escapeHTML(fmtNum(summary.fail_count ?? 0, 0))}, missing ${escapeHTML(fmtNum(summary.missing_count ?? 0, 0))} 항목이 있습니다.</p></article>`
    : `<article class="trading-validation-row good"><span>pass</span><strong>취약 테스트 없음</strong><p>현재 저장된 검증 결과 기준으로 우선 경고 항목이 없습니다.</p></article>`;
  const disciplineRows = disciplines.length
    ? disciplines
      .slice()
      .sort((left, right) => (
        (priority[String(left?.status || "missing")] ?? 9)
        - (priority[String(right?.status || "missing")] ?? 9)
      ))
      .map((row) => {
        const status = String(row?.status || "missing");
        return `
          <article class="trading-validation-pill ${escapeHTML(tradingValidationTone(status))}">
            <span>${escapeHTML(status)}</span>
            <strong>${escapeHTML(row?.label || row?.id || "-")}</strong>
          </article>
        `;
      }).join("")
    : `<article class="trading-validation-pill muted"><span>missing</span><strong>검증 row 대기</strong></article>`;
  const expectedDisciplineCount = disciplineMatrix.expected_count || 19;
  const actualDisciplineCount = disciplineMatrix.actual_count ?? disciplines.length;
  const rowDetailCount = disciplineMatrix.row_detail_count ?? disciplines.length;
  const rowDetailComplete = disciplineMatrix.row_detail_complete === undefined
    ? rowDetailCount >= expectedDisciplineCount
    : Boolean(disciplineMatrix.row_detail_complete);
  const summaryOnlyValidation = actualDisciplineCount > 0 && rowDetailCount === 0;
  const rowDetailNote = summaryOnlyValidation
    ? `summary 기준 ${fmtNum(actualDisciplineCount, 0)}/${fmtNum(expectedDisciplineCount, 0)} · row 상세 대기`
    : `row 상세 ${fmtNum(rowDetailCount, 0)}/${fmtNum(expectedDisciplineCount, 0)}${rowDetailComplete ? "" : " · 부분 상세"}`;
  const remediationCategoryLabels = {
    immediate_ops_controls: "운영 즉시조치",
    research_validation_work: "연구/백테스트 보강",
    sizing_risk_controls: "사이징/리스크 제한",
  };
  const remediationCategories = Array.isArray(remediationPlan.categories)
    ? remediationPlan.categories
    : [];
  const remediationWorkQueue = Array.isArray(remediationPlan.work_queue)
    ? remediationPlan.work_queue
    : [];
  const remediationLaneHints = remediationPlan.lane_policy_hints && typeof remediationPlan.lane_policy_hints === "object"
    ? remediationPlan.lane_policy_hints
    : {};
  const remediationCoreMissingIds = Array.isArray(remediationLaneHints.core_missing_ids)
    ? remediationLaneHints.core_missing_ids.filter(Boolean).slice(0, 4)
    : [];
  const remediationCoreFailIds = Array.isArray(remediationLaneHints.core_fail_ids)
    ? remediationLaneHints.core_fail_ids.filter(Boolean).slice(0, 4)
    : [];
  const remediationScaleBlockedIds = Array.isArray(remediationLaneHints.scale_up_blocked_discipline_ids)
    ? remediationLaneHints.scale_up_blocked_discipline_ids.filter(Boolean).slice(0, 5)
    : [];
  const remediationTradeBlocking = remediationPlan.trade_blocking ?? remediationLaneHints.trade_blocking;
  const remediationBlockingScope = String(remediationPlan.blocking_scope || remediationLaneHints.blocking_scope || "").trim();
  const remediationBlockingLabel = remediationTradeBlocking === true
    ? "거래 차단"
    : (
      remediationTradeBlocking === false && remediationBlockingScope === "scale_up_only"
        ? "거래 가능 · 스케일업 제한"
        : (
          remediationTradeBlocking === false
            ? "거래 가능"
            : (remediationBlockingScope ? `scope ${remediationBlockingScope}` : "")
        )
    );
  const remediationHintText = [
    remediationBlockingLabel,
    remediationLaneHints.entry_mode ? `entry_mode ${remediationLaneHints.entry_mode}` : "",
    remediationLaneHints.risk_budget_mode ? `risk ${remediationLaneHints.risk_budget_mode}` : "",
    remediationLaneHints.requires_verified_quotes ? "검증호가 필요" : "",
    remediationLaneHints.requires_capacity_check ? "용량확인 필요" : "",
    remediationLaneHints.active_revision_sample_mode ? `active ${remediationLaneHints.active_revision_sample_mode}` : "",
    Number.isFinite(Number(remediationLaneHints.active_revision_sample_count)) ? `active 표본 ${fmtNum(remediationLaneHints.active_revision_sample_count, 0)}` : "",
    Number.isFinite(Number(remediationLaneHints.legacy_proxy_sample_count)) ? `proxy 표본 ${fmtNum(remediationLaneHints.legacy_proxy_sample_count, 0)}` : "",
    remediationLaneHints.scale_up_allowed === false ? "scale-up 보류" : "",
    remediationCoreMissingIds.length ? `core missing ${remediationCoreMissingIds.join(", ")}` : "",
    remediationCoreFailIds.length ? `core fail ${remediationCoreFailIds.join(", ")}` : "",
    remediationScaleBlockedIds.length ? `blocked ${remediationScaleBlockedIds.join(", ")}` : "",
  ].filter(Boolean).join(" · ");
  const activeRevisionHtml = activeRevisionEvidence.status
    ? `
      <article class="trading-validation-remediation-card trading-validation-active-revision">
        <div>
          <span>active revision</span>
          <strong>${escapeHTML(activeRevisionEvidence.strategy_revision_id || "-")}</strong>
        </div>
        <small>${escapeHTML(activeRevisionEvidence.status || "대기")}</small>
        <p>
          active ${escapeHTML(fmtNum(activeRevisionEvidence.active_sample_count ?? 0, 0))}
          / min ${escapeHTML(fmtNum(activeRevisionEvidence.min_sample_count ?? 0, 0))}
          · proxy ${escapeHTML(fmtNum(activeRevisionEvidence.legacy_proxy_sample_count ?? 0, 0))}
          · proxy scale ${activeRevisionEvidence.can_scale_from_proxy ? "가능" : "불가"}
        </p>
      </article>
    `
    : "";
  const remediationWorkRows = remediationWorkQueue.slice(0, 4).map((item) => {
    const status = String(item?.status || "missing");
    const owner = item?.owner || item?.category_id || "validation_lab";
    const scaling = item?.blocks_scaling || item?.lane_policy_hint || "";
    return `
      <li class="${escapeHTML(tradingValidationTone(status))}">
        <span>${escapeHTML(item?.priority || status)}</span>
        <strong>${escapeHTML(`${owner} · ${item?.discipline_id || "-"}`)}</strong>
        <p>
          ${escapeHTML(truncateWithEllipsis(scaling || item?.exit_criteria || "-", 132))}
          ${item?.cadence ? ` · ${escapeHTML(item.cadence)}` : ""}
        </p>
      </li>
    `;
  }).join("");
  const orderedRemediationCategories = Object.keys(remediationCategoryLabels)
    .map((id) => remediationCategories.find((row) => row?.id === id) || {
      id,
      label: remediationCategoryLabels[id],
      purpose: "",
      items: [],
      weak_count: 0,
      fail_count: 0,
    });
  const remediationCards = orderedRemediationCategories.map((category) => {
    const items = Array.isArray(category.items) ? category.items : [];
    const itemRows = items.slice(0, 4).map((item) => {
      const status = String(item?.status || "missing");
      return `
        <li class="${escapeHTML(tradingValidationTone(status))}">
          <span>${escapeHTML(status)}</span>
          <strong>${escapeHTML(item?.label || item?.discipline_id || "-")}</strong>
          <p>${escapeHTML(truncateWithEllipsis(item?.action || item?.evidence || "-", 118))}</p>
        </li>
      `;
    }).join("");
    return `
      <article class="trading-validation-remediation-card">
        <div>
          <span>${escapeHTML(category.id || "")}</span>
          <strong>${escapeHTML(category.label || remediationCategoryLabels[category.id] || "복구 항목")}</strong>
        </div>
        <small>
          weak ${escapeHTML(fmtNum(category.weak_count ?? items.length, 0))}
          · fail ${escapeHTML(fmtNum(category.fail_count ?? 0, 0))}
        </small>
        ${
          itemRows
            ? `<ul>${itemRows}</ul>`
            : `<p>${escapeHTML(category.purpose || "현재 이 묶음의 우선 복구 항목은 없습니다.")}</p>`
        }
      </article>
    `;
  }).join("");
  const remediationHtml = `
    <section class="trading-validation-remediation">
      <div class="trading-validation-remediation-head">
        <h5>검증 복구 플랜</h5>
        <span>${escapeHTML(remediationPlan.status || "대기")}</span>
      </div>
      ${
        remediationPlan.primary_next_action
          ? `<p class="trading-validation-primary-action">최우선: ${escapeHTML(truncateWithEllipsis(remediationPlan.primary_next_action, 180))}</p>`
          : ""
      }
      ${
        remediationHintText
          ? `<p class="trading-validation-primary-action">Lane hint: ${escapeHTML(remediationHintText)}</p>`
          : ""
      }
      ${
        remediationWorkRows
          ? `
            <article class="trading-validation-remediation-card trading-validation-work-queue">
              <div>
                <span>work_queue</span>
                <strong>회복 작업</strong>
              </div>
              <ul>${remediationWorkRows}</ul>
            </article>
          `
          : ""
      }
      ${activeRevisionHtml}
      <div class="trading-validation-remediation-grid">${remediationCards}</div>
    </section>
  `;
  const guidanceRows = operatorGuidance.slice(0, 4).map((row) => (
    `<li>${escapeHTML(truncateWithEllipsis(row, 150))}</li>`
  )).join("");
  const capacityHtml = tightestSymbol
    ? `
      <div class="trading-validation-capacity">
        <span>${escapeHTML(labels.capacityTitle || "용량 병목")}</span>
        <strong>${escapeHTML(tightestSymbol)}${tightestBlock ? ` · ${escapeHTML(tightestBlock)}` : ""}</strong>
        <p>
          ${escapeHTML(capacityRatio === undefined ? "capacity ratio -" : `capacity ${fmtNum(capacityRatio, 2)}x`)}
          ${capacitySource ? ` · ${escapeHTML(capacitySource)}` : ""}
        </p>
      </div>
    `
    : `
      <div class="trading-validation-capacity muted">
        <span>${escapeHTML(labels.capacityTitle || "용량 병목")}</span>
        <strong>병목 샘플 없음</strong>
        <p>${escapeHTML(capacity.status ? `capacity status ${capacity.status}` : "용량 근거 대기")}</p>
      </div>
    `;
  const recoveryFocus = Array.isArray(failureAttribution.recovery_focus)
    ? failureAttribution.recovery_focus
    : [];
  const worstGroups = Array.isArray(failureAttribution.worst_groups)
    ? failureAttribution.worst_groups
    : [];
  const failureAttributionHtml = recoveryFocus.length || worstGroups.length
    ? `
      <div class="trading-validation-capacity">
        <span>실패 귀속</span>
        <strong>${escapeHTML(truncateWithEllipsis(recoveryFocus[0] || `${worstGroups[0]?.group_type || "group"}=${worstGroups[0]?.group || "-"}`, 92))}</strong>
        <p>
          ${escapeHTML(`groups ${fmtNum(failureAttribution.group_count ?? worstGroups.length, 0)}`)}
          ${worstGroups[0]?.profit_factor !== undefined ? ` · PF ${escapeHTML(fmtNum(worstGroups[0].profit_factor, 2))}` : ""}
          ${worstGroups[0]?.risk_score !== undefined ? ` · risk ${escapeHTML(fmtNum(worstGroups[0].risk_score, 1))}` : ""}
        </p>
      </div>
    `
    : "";
  const patternReasons = Array.isArray(patternLab.validation_reasons)
    ? patternLab.validation_reasons.map((row) => String(row || "").trim()).filter(Boolean)
    : [];
  const topPatternFailedReasons = patternLab.failed_reasons && typeof patternLab.failed_reasons === "object"
    ? Object.entries(patternLab.failed_reasons)
      .map(([reason, count]) => ({
        reason: String(reason || "").trim(),
        count: Number(count || 0),
      }))
      .filter((row) => row.reason)
      .sort((left, right) => (right.count - left.count) || left.reason.localeCompare(right.reason))
      .slice(0, 4)
    : (
      Array.isArray(patternLab.top_rejection_reasons)
        ? patternLab.top_rejection_reasons
          .map((row) => ({
            reason: String(row?.reason || "").trim(),
            count: Number(row?.count || 0),
          }))
          .filter((row) => row.reason)
          .slice(0, 4)
        : []
    );
  const patternRecoveryReasons = patternReasons.length
    ? patternReasons
    : topPatternFailedReasons.map((row) => row.reason);
  const patternActionByReason = {
    active_walk_forward_windows_missing: "rolling WFA window 재생성",
    active_out_of_sample_missing: "OOS evidence 재생성",
    active_overfit_unknown: "overfit risk 재산정",
    active_overfit_high: "high-overfit active set 강등",
    out_of_sample_missing: "OOS evidence 재생성",
    out_of_sample_expectancy_negative: "OOS 기대값 재검증",
    out_of_sample_profit_factor_low: "OOS 수익팩터 재검증",
    walk_forward_pass_rate_low: "rolling WFA window 재검증",
  };
  const patternRecoveryHtml = patternRecoveryReasons.length
    ? `
      <div class="trading-validation-capacity">
        <span>패턴랩 복구</span>
        <strong>${escapeHTML(patternActionByReason[patternRecoveryReasons[0]] || "validation evidence 복구")}</strong>
        <p>
          ${escapeHTML(patternRecoveryReasons.slice(0, 2).join(" · "))}
          ${topPatternFailedReasons.length ? ` · ${escapeHTML(topPatternFailedReasons.map((row) => `${row.reason} ${fmtNum(row.count, 0)}`).slice(0, 2).join(" · "))}` : ""}
          ${patternLab.active_set_count !== undefined ? ` · active ${escapeHTML(fmtNum(patternLab.active_set_count, 0))}` : ""}
          ${patternLab.active_walk_forward_coverage_rate_pct !== undefined ? ` · WFA ${escapeHTML(fmtNum(patternLab.active_walk_forward_coverage_rate_pct, 1))}%` : ""}
        </p>
      </div>
    `
    : "";
  const laneActions = laneScorecards.lane_actions && typeof laneScorecards.lane_actions === "object"
    ? laneScorecards.lane_actions
    : {};
  const laneAuthoritySummary = payload.lane_authority_summary && typeof payload.lane_authority_summary === "object"
    ? payload.lane_authority_summary
    : {};
  const reducedLaneRows = Array.isArray(laneAuthoritySummary.reduced_lanes)
    ? laneAuthoritySummary.reduced_lanes.slice(0, 4)
    : [];
  const laneExecutionPosture = laneAuthoritySummary.execution_posture || "";
  const laneExecutionPostureLabels = {
    scale_allowed: "확대 가능",
    probe_allowed_scale_blocked: "탐색 가능 · 확대 제한",
    probe_allowed_sample_building: "탐색 표본 축적",
    review_required_no_scale: "검토 필요 · 확대 보류",
    normal_selective: "선별 운용",
  };
  const probeLaneCount = Number(laneAuthoritySummary.probe_lane_count ?? 0);
  const scaleBlockedLaneCount = Number(
    laneAuthoritySummary.scale_blocked_lane_count
      ?? laneAuthoritySummary.reduced_lane_count
      ?? reducedLaneRows.length
      ?? 0
  );
  const reducedLaneText = reducedLaneRows.map((row) => {
    const lane = row?.lane || "-";
    const venuePrefix = row?.venue && row.venue !== "aggregate" ? `${row.venue}:` : "";
    const multiplier = row?.authority_multiplier === undefined || row?.authority_multiplier === null
      ? "-"
      : `${fmtNum(row.authority_multiplier, 2)}x`;
    const reasons = Array.isArray(row?.reasons)
      ? row.reasons.slice(0, 3).join("/")
      : "";
    return `${venuePrefix}${lane} ${multiplier}${reasons ? ` ${reasons}` : ""}`;
  }).join(" · ");
  const laneActionEntries = Object.entries(laneActions).slice(0, 4);
  const weakLaneLabels = Array.isArray(laneScorecards.weak_lanes)
    ? laneScorecards.weak_lanes.slice(0, 4).map((row) => String(row || "")).filter(Boolean)
    : [];
  const scaleLaneLabels = Array.isArray(laneScorecards.scale_candidate_lanes)
    ? laneScorecards.scale_candidate_lanes.slice(0, 4).map((row) => String(row || "")).filter(Boolean)
    : [];
  const insufficientLaneLabels = Array.isArray(laneScorecards.insufficient_lanes)
    ? laneScorecards.insufficient_lanes.slice(0, 4).map((row) => String(row || "")).filter(Boolean)
    : [];
  const laneScorecardsHtml = Object.keys(laneScorecards).length
    ? `
      <div class="trading-validation-capacity">
        <span>Lane 성과</span>
        <strong>
          ${
            scaleLaneLabels.length
              ? `확대 ${escapeHTML(scaleLaneLabels.join(", "))}`
              : "확대 lane 대기"
          }
        </strong>
        <p>
          ${weakLaneLabels.length ? `weak_lanes ${escapeHTML(weakLaneLabels.join(", "))}` : "weak_lanes 없음"}
          ${insufficientLaneLabels.length ? ` · insufficient ${escapeHTML(insufficientLaneLabels.join(", "))}` : ""}
          ${laneActionEntries.length ? ` · ${escapeHTML(laneActionEntries.map(([lane, row]) => `${lane}:${row?.action || row?.grade || "-"}`).join(" · "))}` : ""}
        </p>
      </div>
    `
    : "";
  const laneAuthorityHtml = reducedLaneRows.length
    ? `
      <div class="trading-validation-capacity warn">
        <span>탐색/확대 조절 lane</span>
        <strong>${escapeHTML(reducedLaneText || "probe lane 확인")}</strong>
        <p>
          ${escapeHTML(laneExecutionPostureLabels[laneExecutionPosture] || "탐색/확대 상태 확인")}
          ${probeLaneCount ? ` · ${escapeHTML(`탐색 ${fmtNum(probeLaneCount, 0)}개`)}` : ""}
          ${escapeHTML(` · 확대 제한 ${fmtNum(scaleBlockedLaneCount, 0)}개`)}
          ${laneAuthoritySummary.validation_repair_weak_lanes?.length ? ` · validation repair ${escapeHTML(laneAuthoritySummary.validation_repair_weak_lanes.slice(0, 3).join(", "))}` : ""}
          ${laneAuthoritySummary.weak_lanes?.length ? ` · weak ${escapeHTML(laneAuthoritySummary.weak_lanes.slice(0, 3).join(", "))}` : ""}
        </p>
      </div>
    `
    : "";
  return `
    <div class="trading-validation-detail">
      <div class="trading-validation-head">
        <h4>${escapeHTML(labels.summaryTitle || "검증 랩 요약")}</h4>
        <div class="trading-validation-counts">
          <span class="good">P ${escapeHTML(fmtNum(summary.pass_count ?? 0, 0))}</span>
          <span class="warn">W ${escapeHTML(fmtNum(summary.warn_count ?? 0, 0))}</span>
          <span class="bad">F ${escapeHTML(fmtNum(summary.fail_count ?? 0, 0))}</span>
          <span>M ${escapeHTML(fmtNum(summary.missing_count ?? 0, 0))}</span>
        </div>
      </div>
      <div class="trading-validation-summary">
        <div><span>종합 점수</span><strong>${escapeHTML(score === undefined ? "-" : fmtNum(score, 2))}</strong></div>
        <div><span>Readiness</span><strong>${escapeHTML(readiness)}</strong></div>
        <div><span>Freshness</span><strong>${escapeHTML(freshness)}</strong></div>
        <div class="trading-validation-source">
          <span>검증 근거</span>
          <strong>${escapeHTML(validationSourceLabel)}</strong>
          <p>
            ${escapeHTML(patternStatus ? `status ${patternStatus}` : "status -")}
            ${validationSourceDetail ? ` · ${escapeHTML(truncateWithEllipsis(validationSourceDetail, 80))}` : ""}
            ${sourceScope ? ` · ${escapeHTML(sourceScope)}` : " · kis_live_forward_proxy/crypto_pattern_lab 구분 대기"}
          </p>
        </div>
      </div>
      <section class="trading-validation-matrix-section">
        <h5>전체 19검증 · ${escapeHTML(fmtNum(actualDisciplineCount, 0))}/${escapeHTML(fmtNum(expectedDisciplineCount, 0))}</h5>
        <p class="trading-validation-row-detail ${summaryOnlyValidation ? "warn" : "neutral"}">${escapeHTML(rowDetailNote)}</p>
        <div class="trading-validation-matrix">${disciplineRows}</div>
      </section>
      ${remediationHtml}
      <div class="trading-validation-split">
        <section>
          <h5>${escapeHTML(labels.weakTitle || "취약 테스트")}</h5>
          <div class="trading-validation-list">${weakRows}</div>
        </section>
        <section>
          ${staleWarningHtml}
          ${capacityHtml}
          ${failureAttributionHtml}
          ${laneAuthorityHtml}
          ${laneScorecardsHtml}
          ${patternRecoveryHtml}
          <h5>우선 조치</h5>
          ${
            guidanceRows
              ? `<ul class="trading-validation-guidance">${guidanceRows}</ul>`
              : `<p class="trading-validation-empty">operator guidance 대기</p>`
          }
        </section>
      </div>
    </div>
  `;
}

function liveAuthorityPanelOptions() {
  return {
    activeRevisionEvidenceTone,
    formatActiveRevisionEvidenceLabel,
    liveAuthorityError: state.liveAuthorityError,
    mergeTradingValidationWithGateMatrix,
    renderTradingValidationDetails,
    repairExecutionTone,
  };
}

function binancePatternDirection(row) {
  const direct = String(row?.direction || "").trim().toLowerCase();
  if (direct) return direct;
  const parts = String(row?.pattern_key || "").split(":");
  return parts.length >= 2 ? parts[1].trim().toLowerCase() : "";
}

function binanceQuantSignalBySymbol() {
  const rows = Array.isArray(state.binanceTrader.quantSignals)
    ? state.binanceTrader.quantSignals
    : [];
  return rows.reduce((acc, row) => {
    const symbol = String(row?.symbol || "").toUpperCase();
    if (symbol && !acc[symbol]) acc[symbol] = row;
    return acc;
  }, {});
}

function binanceBacktestConfluenceStatus({ patternSet, quantRow, liveAuthority }) {
  const direction = binancePatternDirection(patternSet);
  const signal = quantRow?.signal || {};
  const metrics = signal.metrics || {};
  const bias = String(signal.bias || quantRow?.bias || "").toLowerCase();
  const spread = asNumber(metrics.spread_bps ?? quantRow?.spread_bps, 0);
  const grade = String(liveAuthority?.live_grade || liveAuthority?.status || "").toLowerCase();
  const reasons = [];
  if (!bias) reasons.push("퀀트 대기");
  if (bias && direction && bias !== direction) reasons.push(`퀀트 ${bias}`);
  if (spread > 35) reasons.push(`스프레드 ${fmtNum(spread, 1)}bps`);
  if (["observe_only", "restricted", "missing", "error"].includes(grade)) reasons.push(`권한 ${grade || "대기"}`);
  if (!reasons.length) {
    return { label: "정렬", tone: "good", reasons: ["패턴·퀀트·권한 확인"] };
  }
  if (bias && direction && bias !== direction) {
    return { label: "충돌", tone: "bad", reasons };
  }
  return { label: "대기", tone: "warn", reasons };
}

function renderBinanceBacktestConfluencePanel(payload = {}) {
  const context = state.binanceTrader.patternContext || {};
  const optimizedSets = Array.isArray(context.optimized_strategy_sets) ? context.optimized_strategy_sets : [];
  const qualifiedRows = Array.isArray(context.qualified_scorecards) ? context.qualified_scorecards : [];
  const optimization = context.optimization && typeof context.optimization === "object" ? context.optimization : {};
  const liveAuthority = liveAuthorityForVenue("binance", payload.live_authority);
  const quantBySymbol = binanceQuantSignalBySymbol();
  if (state.binanceTrader.patternError) {
    return `<section class="memory-section backtest-confluence-panel"><div class="notice">백테스트 교차검증 조회 실패: ${escapeHTML(state.binanceTrader.patternError)}</div></section>`;
  }
  if (!optimizedSets.length && !qualifiedRows.length) {
    return `
      <section class="memory-section backtest-confluence-panel">
        <div class="panel-head compact">
          <h3>백테스트 라이브 교차검증</h3>
          <p>아직 블록 화면에 표시할 최적화 세트가 없습니다. 크립토 리서치에서 패턴 랩을 갱신하세요.</p>
        </div>
      </section>
    `;
  }
  const alignedCount = optimizedSets.filter((row) => {
    const status = binanceBacktestConfluenceStatus({
      patternSet: row,
      quantRow: quantBySymbol[String(row.symbol || "").toUpperCase()],
      liveAuthority,
    });
    return status.tone === "good";
  }).length;
  const cards = optimizedSets.slice(0, 5).map((row) => {
    const symbol = String(row.symbol || "").toUpperCase();
    const params = row.parameter_set && typeof row.parameter_set === "object" ? row.parameter_set : {};
    const quantRow = quantBySymbol[symbol] || {};
    const status = binanceBacktestConfluenceStatus({ patternSet: row, quantRow, liveAuthority });
    const direction = binancePatternDirection(row) || "-";
    return `
      <article class="confluence-card ${escapeHTML(status.tone)}">
        <div class="confluence-card-head">
          <div>
            <strong>${escapeHTML(symbol || "-")}</strong>
            <span>${escapeHTML(row.pattern_key || "-")}</span>
          </div>
          <span class="strategy-data-chip ${escapeHTML(status.tone)}">${escapeHTML(status.label)}</span>
        </div>
        <div class="optimized-param-row">
          <span><b>${escapeHTML(direction)}</b>방향</span>
          <span><b>${escapeHTML(fmtPercent(asNumber(params.stop_pct, 0) * 100, 2))}</b>손절</span>
          <span><b>${escapeHTML(fmtPercent(asNumber(params.target_pct, 0) * 100, 2))}</b>목표</span>
        </div>
        <div class="optimized-set-metrics compact">
          <span><b>${escapeHTML(fmtNum(row.objective_score, 1))}</b>objective</span>
          <span><b>${escapeHTML(fmtPercent(asNumber(row.win_rate, 0) * 100, 1))}</b>승률</span>
          <span><b>${escapeHTML(fmtNum(row.expectancy_r, 2))}R</b>기대값</span>
        </div>
        <p>${escapeHTML(status.reasons.join(" · "))}</p>
      </article>
    `;
  }).join("");
  return `
    <section class="memory-section backtest-confluence-panel">
      <div class="panel-head compact">
        <div>
          <h3>백테스트 라이브 교차검증</h3>
          <p>최적화 세트가 퀀트·오더북/스프레드·펀딩·Live Authority와 맞을 때만 블록화 후보가 됩니다.</p>
        </div>
        <div class="strategy-chip-row compact">
          <span class="strategy-data-chip good">${escapeHTML(fmtNum(alignedCount, 0))} aligned</span>
          <span class="strategy-data-chip">${escapeHTML(fmtNum(optimization.set_count || optimizedSets.length, 0))} optimized</span>
          <span class="strategy-data-chip">${escapeHTML(fmtNum(qualifiedRows.length, 0))} qualified</span>
        </div>
      </div>
      <div class="confluence-card-grid">${cards}</div>
    </section>
  `;
}

function computeExitQuality(blocks) {
  const rows = (Array.isArray(blocks) ? blocks : [])
    .map((block) => (block?.performance && typeof block.performance === "object" ? block.performance : null))
    .filter(Boolean);
  if (!rows.length) {
    return {
      block_count: 0,
      avg_giveback_pct: 0,
      max_giveback_pct: 0,
      avg_mfe_pct: 0,
    };
  }
  const givebacks = rows.map((row) => asNumber(row.giveback_pct, 0));
  const mfes = rows.map((row) => asNumber(row.mfe_pct, 0));
  return {
    block_count: rows.length,
    avg_giveback_pct: givebacks.reduce((sum, value) => sum + value, 0) / rows.length,
    max_giveback_pct: Math.max(...givebacks),
    avg_mfe_pct: mfes.reduce((sum, value) => sum + value, 0) / rows.length,
  };
}

function updateJueDiagnosticState(blocks = []) {
  const coverage = state.marketJudge.result?.candidate_coverage
    || state.marketJudge.result?.run?.source_snapshot?.candidate_coverage
    || state.candidateCoverage
    || {};
  const scoreComponents = state.marketPulse.result?.score_components || {};
  const riskCap = scoreComponents.risk_cap || state.marketPulse.result?.risk_cap || state.marketRiskCap || {};
  state.candidateCoverage = coverage;
  state.marketRiskCap = riskCap;
  state.exitQuality = computeExitQuality(blocks);
}

function renderJueDiagnosticStrip(payload) {
  const allBlocks = Array.isArray(payload?.blocks) ? payload.blocks : [];
  updateJueDiagnosticState(allBlocks);
  const coverage = state.candidateCoverage || {};
  const exitQuality = state.exitQuality || {};
  const riskCap = state.marketRiskCap || {};
  const etfStatus = state.etfResearch.status || {};
  const etfUniverseCount = normalizeNonNegativeInt(
    etfStatus.expanded_universe_count
      ?? etfStatus.universe_count
      ?? etfStatus.configured_universe?.length
      ?? 0,
  ) ?? 0;
  const coverageSource = coverage.coverage && typeof coverage.coverage === "object"
    ? coverage.coverage
    : {};
  const chips = [
    {
      label: "후보 풀",
      value: fmtNum(coverage.pool_count || 0, 0),
      sub: coverage.status || "not_scanned",
      tone: Number(coverage.pool_count || 0) >= 200 ? "good" : "warn",
    },
    {
      label: "LLM 집중",
      value: fmtNum(coverage.llm_focus_limit || 0, 0),
      sub: `quote ${fmtNum(coverage.quote_limit || 0, 0)}`,
      tone: "neutral",
    },
    {
      label: "ETF 유니버스",
      value: fmtNum(etfUniverseCount, 0),
      sub: `usable ${fmtNum(etfStatus.usable_research_count || 0, 0)}`,
      tone: etfUniverseCount > 3 ? "good" : "warn",
    },
    {
      label: "최대수익 반납",
      value: fmtPercent(exitQuality.avg_giveback_pct || 0, 1),
      sub: `MFE ${fmtPercent(exitQuality.avg_mfe_pct || 0, 1)} · ${fmtNum(exitQuality.block_count || 0, 0)}블록`,
      tone: Number(exitQuality.avg_giveback_pct || 0) >= 4 ? "warn" : "neutral",
    },
    {
      label: "리스크 캡",
      value: riskCap.active ? `ON ${fmtNum(riskCap.cap || 0, 0)}` : "OFF",
      sub: Array.isArray(riskCap.reasons) && riskCap.reasons.length ? riskCap.reasons.slice(0, 2).join(" · ") : "pressure clear",
      tone: riskCap.active ? "warn" : "good",
    },
    {
      label: "데이터 소스",
      value: fmtNum(
        asNumber(coverageSource.symbol_count, 0)
          + asNumber(coverageSource.report_count, 0)
          + asNumber(coverageSource.insight_count, 0),
        0,
      ),
      sub: `리포트 ${fmtNum(coverageSource.report_count || 0, 0)} · 인사이트 ${fmtNum(coverageSource.insight_count || 0, 0)}`,
      tone: "neutral",
    },
  ];
  return `
    <section class="jue-diagnostic-strip" aria-label="쥬 진단 지표">
      ${chips.map((chip) => `
        <div class="jue-diagnostic-chip ${escapeHTML(chip.tone || "neutral")}">
          <span>${escapeHTML(chip.label)}</span>
          <strong>${escapeHTML(chip.value)}</strong>
          <small>${escapeHTML(chip.sub || "")}</small>
        </div>
      `).join("")}
    </section>
  `;
}

function etfResearchRows(payload) {
  return ETF_TAB.researchRows(payload);
}

function etfUniverseRows(status) {
  return ETF_TAB.universeRows(status);
}

function coreEtfAllocation(payload, blocks) {
  return ETF_TAB.coreAllocation(
    payload,
    blocks,
    {
      horizonFn: (row) => {
        if (row?.status !== undefined || row?.block_id !== undefined) {
          return KIS_TRADER_TAB.blockHorizonForBlock(row);
        }
        return KIS_TRADER_TAB.normalizeBlockHorizon(row?.horizon || row?.key || row?.type);
      },
    },
  );
}

function etfSnapshotChip(snapshot) {
  return ETF_TAB.snapshotChip(snapshot, { escapeHTML });
}

function etfResearchStale(isoString) {
  return ETF_TAB.researchStale(isoString);
}

function etfScoreChips(score) {
  return ETF_TAB.scoreChips(score, { escapeHTML, fmtNum });
}

function renderEtfCandidateRow(item) {
  return ETF_TAB.renderCandidateRow(item, {
    escapeHTML,
    fmtKRW,
    fmtNum,
    asNumber,
  });
}

function renderEtfCoreBoard(payload, blocks) {
  return ETF_TAB.renderCoreBoard(payload, blocks, {
    research: state.etfResearch,
    reportRepository: state.reportsStatus?.repository || {},
    escapeHTML,
    fmtKRW,
    fmtNum,
    fmtKST,
    asNumber,
    normalizeNonNegativeInt,
    blockHorizonWeight: (value) => KIS_TRADER_TAB.blockHorizonWeight(value, asNumber, fmtNum),
    horizonFn: (row) => {
      if (row?.status !== undefined || row?.block_id !== undefined) {
        return KIS_TRADER_TAB.blockHorizonForBlock(row);
      }
      return KIS_TRADER_TAB.normalizeBlockHorizon(row?.horizon || row?.key || row?.type);
    },
  });
}

function ensureBlockHistoryDate(dates) {
  if (!dates.length) {
    state.kisBlockHistory.date = "";
    return "";
  }
  if (!state.kisBlockHistory.date || !dates.includes(state.kisBlockHistory.date)) {
    state.kisBlockHistory.date = dates[0];
  }
  return state.kisBlockHistory.date;
}

function moveBlockHistoryDate(direction) {
  const payload = state.kisBlockStatus || {};
  const blocks = Array.isArray(payload.blocks) ? payload.blocks : [];
  const dates = KIS_TRADER_TAB.historyDates(blocks);
  if (!dates.length) return;
  const selected = ensureBlockHistoryDate(dates);
  const index = Math.max(dates.indexOf(selected), 0);
  const nextIndex = Math.min(Math.max(index + direction, 0), dates.length - 1);
  state.kisBlockHistory.date = dates[nextIndex];
  state.kisBlockHistory.selectedBlockId = "";
}

const OPS_VALIDATION_VENUES = Object.freeze(["kis", "binance"]);

function opsSignalVenue(value) {
  const key = String(value || "").trim().toLowerCase();
  return OPS_VALIDATION_VENUES.find((venue) => key.endsWith(`_${venue}`)) || "";
}

function filterOpsSignalsForVenue(items, venue) {
  const cleanVenue = String(venue || "").trim().toLowerCase();
  if (!cleanVenue) return Array.isArray(items) ? items : [];
  return (Array.isArray(items) ? items : []).filter((item) => {
    const signalVenue = opsSignalVenue(item);
    return !signalVenue || signalVenue === cleanVenue;
  });
}

function filterOpsDetailsForVenue(details, venue) {
  const cleanVenue = String(venue || "").trim().toLowerCase();
  if (!cleanVenue) return Array.isArray(details) ? details : [];
  return (Array.isArray(details) ? details : []).filter((row) => {
    if (!row || typeof row !== "object") return false;
    const rowVenue = String(row.venue || "").trim().toLowerCase();
    if (rowVenue) return rowVenue === cleanVenue;
    const signalVenue = opsSignalVenue(row.signal);
    return !signalVenue || signalVenue === cleanVenue;
  });
}

function filterTradingValidationRowsForVenue(rows, venue) {
  const cleanVenue = String(venue || "").trim().toLowerCase();
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!row || typeof row !== "object") return false;
    const rowVenue = String(row.venue || "").trim().toLowerCase();
    return !rowVenue || rowVenue === cleanVenue;
  });
}

function tradingValidationForVenue(tradingValidation, venue) {
  const payload = tradingValidation && typeof tradingValidation === "object"
    ? tradingValidation
    : {};
  const cleanVenue = String(venue || "").trim().toLowerCase();
  const venuePayload = payload.venues && typeof payload.venues === "object"
    ? payload.venues[cleanVenue]
    : null;
  if (!cleanVenue || !venuePayload || typeof venuePayload !== "object") return payload;
  const venueSummary = venuePayload.summary && typeof venuePayload.summary === "object"
    ? venuePayload.summary
    : {};
  return {
    ...venuePayload,
    status: venuePayload.status || payload.status,
    db_path: payload.db_path,
    latest_run_id: venuePayload.latest_run_id || payload.latest_run_id,
    latest_at: venuePayload.latest_at || venuePayload.computed_at || payload.latest_at,
    summary: venueSummary,
    readiness: venuePayload.readiness || venueSummary.readiness || payload.readiness,
    diagnostic_status: (
      venuePayload.diagnostic_status
      || venueSummary.diagnostic_status
      || payload.diagnostic_status
    ),
    score: venuePayload.score ?? venueSummary.total_score ?? payload.score,
    discipline_count: venuePayload.discipline_count ?? payload.discipline_count,
    expected_discipline_count: venuePayload.expected_discipline_count ?? payload.expected_discipline_count,
    venues: { [cleanVenue]: venuePayload },
    lane_authority_summary: venuePayload.lane_authority_summary || {},
    bottlenecks: filterTradingValidationRowsForVenue(payload.bottlenecks, cleanVenue),
    primary_next_actions: filterTradingValidationRowsForVenue(payload.primary_next_actions, cleanVenue),
    status_endpoint: payload.status_endpoint,
    run_once_endpoint: payload.run_once_endpoint,
  };
}

function opsReadinessForVenue(ops, venue) {
  const payload = ops && typeof ops === "object" ? ops : {};
  const cleanVenue = String(venue || "").trim().toLowerCase();
  if (!cleanVenue) return payload;
  return {
    ...payload,
    warnings: filterOpsSignalsForVenue(payload.warnings, cleanVenue),
    advisories: filterOpsSignalsForVenue(payload.advisories, cleanVenue),
    blockers: filterOpsSignalsForVenue(payload.blockers, cleanVenue),
    advisory_details: filterOpsDetailsForVenue(payload.advisory_details, cleanVenue),
    trading_validation_advisory_details: filterOpsDetailsForVenue(
      payload.trading_validation_advisory_details,
      cleanVenue,
    ),
    trading_validation: tradingValidationForVenue(payload.trading_validation, cleanVenue),
  };
}

function renderBlockOpsReadiness(venue = "") {
  if (!state.opsReadiness) return "";
  const ops = opsReadinessForVenue(state.opsReadiness, venue);
  const warnings = Array.isArray(ops.warnings) ? ops.warnings : [];
  const advisories = Array.isArray(ops.advisories) ? ops.advisories : [];
  const blockers = Array.isArray(ops.blockers) ? ops.blockers : [];
  const tone = ops.status === "green" ? "good" : ops.status === "red" ? "warn" : "neutral";
  const llmTotalTokens = state.llmUsage?.total?.total_tokens;
  const llmTokenChip = llmTotalTokens === undefined || llmTotalTokens === null
    ? ""
    : `<span class="strategy-data-chip">LLM ${escapeHTML(fmtKRW(llmTotalTokens))} tokens</span>`;
  const remediationHtml = renderOpsRemediationActions(ops.remediation_actions, 4);
  const validationBottleneckHtml = renderTradingValidationBottleneckSummary(ops.trading_validation, 6);
  const advisoryDetailHtml = renderOpsAdvisoryDetails(ops.advisory_details, 5);
  return `
    <section class="memory-section">
      <div class="panel-head compact">
        <h3>쥬 운영 준비도</h3>
      </div>
      <div class="strategy-chip-row">
        <span class="strategy-data-chip ${tone}">${escapeHTML(ops.status || "-")}</span>
        <span class="strategy-data-chip">${escapeHTML(ops.live_trading_enabled ? "실주문 활성" : "Paper/실주문 비활성")}</span>
        <span class="strategy-data-chip">${escapeHTML(ops.memory?.seeded ? "메모리 seed 완료" : "메모리 seed 필요")}</span>
        <span class="strategy-data-chip">${escapeHTML(ops.market_judge?.enabled ? "장중 판단 on" : "장중 판단 off")}</span>
        ${llmTokenChip}
        ${[...blockers, ...warnings].slice(0, 5).map((item) => `<span class="strategy-data-chip warn">${escapeHTML(formatOpsSignalLabel(item))}</span>`).join("")}
        ${advisories.slice(0, 5).map((item) => `<span class="strategy-data-chip neutral">${escapeHTML(formatOpsSignalLabel(item))}</span>`).join("")}
      </div>
      ${remediationHtml}
      ${validationBottleneckHtml}
      ${advisoryDetailHtml}
    </section>
  `;
}

function renderKisBlockTradingTab() {
  const payload = state.kisBlockStatus;
  const errorHtml = state.kisBlockError
    ? `<div class="notice">블록 트레이딩 조회 실패: ${escapeHTML(state.kisBlockError)}</div>`
    : "";
  if (!payload) {
    return `${errorHtml || '<div class="notice">블록 트레이딩 상태를 불러오는 중입니다.</div>'}`;
  }
  const allBlocks = Array.isArray(payload.blocks) ? payload.blocks : [];
  const blocks = Array.isArray(payload.active_blocks)
    ? payload.active_blocks
    : allBlocks.filter((block) => ["proposed", "entry_pending", "open", "exit_pending"].includes(String(block.status || "")));
  const killEnabled = Boolean(payload.summary?.kill_switch?.enabled);
  return `
    <div class="block-trader-shell">
      ${UI_SHELL.renderWorkspaceJumpNav("kis", { escapeHTML })}
      <section id="kis-workspace-overview" class="venue-workspace-section" tabindex="-1">
      <div class="strategy-intel-actions">
        ${renderActiveRefreshChip("kis_trader")}
        <button class="btn primary" type="button" data-block-action="refresh">새로고침</button>
        <button class="btn warm" type="button" data-block-action="adopt">쥬 기존 보유분 블록화</button>
        <button class="btn" type="button" data-block-action="manager">LLM 매니저 1회</button>
        <button class="btn" type="button" data-block-action="tick">룰엔진 tick</button>
        <button class="btn ${killEnabled ? "" : "danger"}" type="button" data-block-action="${killEnabled ? "kill-release" : "kill"}">${killEnabled ? "킬스위치 해제" : "킬스위치"}</button>
      </div>
      ${errorHtml}
      ${renderBlockOpsReadiness("kis")}
      <section class="memory-section">
        <div class="panel-head compact">
          <h3>오늘 적용 중인 메모리 정책</h3>
        </div>
      ${renderMemoryPolicyStrip(state.investmentMemory)}
      </section>
      ${KIS_TRADER_TAB.renderBlockHero(payload, {
        escapeHTML,
        renderAccountCashLine,
        opsReadiness: state.opsReadiness || {},
      })}
      ${renderKisAccountHoldingsPanel(payload)}
      ${UI_LIVE_AUTHORITY.renderLiveAuthorityPanel(
        "kis",
        liveAuthorityForVenue("kis", payload.summary?.live_authority),
        liveAuthorityPanelOptions(),
      )}
      ${renderValidationRepairOpsPanel(payload, "KIS 쥬")}
      ${renderJueDiagnosticStrip(payload)}
      </section>
      <section id="kis-workspace-active" class="venue-workspace-section" tabindex="-1">
      ${KIS_TRADER_TAB.renderKisHoldDecision(payload, {
        escapeHTML,
        fmtKST,
        fmtNum,
        registerHelperDetail,
      })}
      ${KIS_TRADER_TAB.renderKisCreativeHypotheses(payload, {
        escapeHTML,
        fmtKST,
        fmtNum,
        registerHelperDetail,
      })}
      ${KIS_TRADER_TAB.renderHorizonAllocation(payload, blocks, {
        escapeHTML,
        asNumber,
        fmtNum,
        fmtKRW,
      })}
      ${KIS_TRADER_TAB.renderDailyDiscoveryPanel({
        payload: state.dailyDiscovery || {},
        loading: state.dailyDiscoveryLoading,
        running: state.dailyDiscoveryRunning,
        error: state.dailyDiscoveryError,
      }, {
        escapeHTML,
        fmtNum,
        fmtKST,
        normalizeNonNegativeInt,
      })}
      ${renderEtfCoreBoard(payload, blocks)}
      ${KIS_TRADER_TAB.renderHorizonBlockGroups(blocks, {
        escapeHTML,
        horizonFn: (block) => KIS_TRADER_TAB.blockHorizonForBlock(block),
        renderBlockCard: (block) => KIS_TRADER_TAB.renderBlockCard(block, {
          escapeHTML,
          asNumber,
          fmtKRW,
          fmtMaybeKRW,
          fmtPercent,
          renderBlockValidationChips: (metadata) => renderBlockValidationChips(metadata),
          renderValidationPassportChips: (metadata) => renderValidationPassportChips(metadata),
          renderBlockCostFeasibilityChips: (metadata) => renderBlockCostFeasibilityChips(metadata),
          renderBlockPolicyEffectChips: (metadata) => renderBlockPolicyEffectChips(metadata),
        }),
      })}
      </section>
      <section id="kis-workspace-history" class="venue-workspace-section" tabindex="-1">
      ${KIS_TRADER_TAB.renderBlockHistoryBoard(payload, {
        historyState: state.kisBlockHistory,
        escapeHTML,
        asNumber,
        fmtKRW,
        fmtMaybeKRW,
        fmtNum,
        fmtKST,
        truncateWithEllipsis,
        renderBlockValidationChips: (metadata) => renderBlockValidationChips(metadata),
        renderValidationPassportChips: (metadata) => renderValidationPassportChips(metadata),
        renderBlockCostFeasibilityChips: (metadata) => renderBlockCostFeasibilityChips(metadata),
        renderBlockPolicyEffectChips: (metadata) => renderBlockPolicyEffectChips(metadata),
      })}
      </section>
      <section class="venue-workspace-section workspace-evidence-section" aria-label="KIS 주문·이벤트·매니저 근거">
      <div class="helper-grid">
        ${KIS_TRADER_TAB.renderBlockAllocation(payload, {
          escapeHTML,
          asNumber,
          fmtKRW,
        })}
        ${KIS_TRADER_TAB.renderBlockEventFeed(payload, {
          escapeHTML,
          fmtKRW,
        })}
        ${KIS_TRADER_TAB.renderBlockManagerRun(payload, {
          escapeHTML,
          fmtKST,
          stringifySafe,
        })}
      </div>
      <p class="strategy-footnote">블록 트레이딩은 독립 블록 단위 관리 도구입니다. 실주문은 별도 실행 플래그가 켜져야 동작합니다.</p>
      </section>
    </div>
  `;
}

function cryptoResearchNotesMap(context) {
  return CRYPTO_RESEARCH_TAB.notesMap(context);
}

function cryptoResearchFeaturesMap(context) {
  return CRYPTO_RESEARCH_TAB.featuresMap(context);
}

function binanceHoldObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function binanceHasDiagnosticValue(value) {
  return value !== undefined && value !== null && value !== "";
}

function binanceDiagnosticNumber(value, maxFractionDigits = 4) {
  return binanceHasDiagnosticValue(value) ? fmtNum(value, maxFractionDigits) : "-";
}

function binanceBookErrorLine(row) {
  if (!row || typeof row !== "object") return String(row || "-");
  return [
    row.market || "spot",
    row.symbol || "-",
    row.error || row.reason || row.message || row.error_message || "-",
  ].join(" · ");
}

function binanceCandidateBookLine(row) {
  if (!row || typeof row !== "object") return "";
  const calculated = binanceHoldObject(row.calculated);
  const marketInputs = binanceHoldObject(calculated.market_inputs);
  if (!Object.keys(marketInputs).length) return "";
  const bookFresh = marketInputs.book_fresh === undefined
    ? "-"
    : String(Boolean(marketInputs.book_fresh));
  const bid = marketInputs.bid_price ?? marketInputs.bid;
  const ask = marketInputs.ask_price ?? marketInputs.ask;
  const spread = marketInputs.spread_bps ?? marketInputs.spread;
  const source = marketInputs.book_source || marketInputs.source || "-";
  return [
    `${row.market || marketInputs.book_market || "spot"} · ${row.symbol || "-"}`,
    `book_fresh ${bookFresh}`,
    `bid ${binanceDiagnosticNumber(bid, 6)}`,
    `ask ${binanceDiagnosticNumber(ask, 6)}`,
    `spread ${binanceDiagnosticNumber(spread, 2)}`,
    `source ${source}`,
  ].join(" · ");
}

function binancePulseDataGaps(pulse) {
  if (Array.isArray(pulse.data_gaps)) return pulse.data_gaps;
  return [];
}

function binancePulseDataGapCount(pulse, fallbackGaps = null) {
  if (Array.isArray(pulse.data_gaps)) return pulse.data_gaps.length;
  if (binanceHasDiagnosticValue(pulse.data_gap_count)) return Number(pulse.data_gap_count || 0);
  if (Array.isArray(fallbackGaps)) return fallbackGaps.length;
  return null;
}

function binanceCryptoPulseLines(pulse) {
  if (!Object.keys(pulse).length) return "- 없음";
  const avgMajorChange = pulse.avg_major_change ?? pulse.avg_major_change_pct_24h;
  const avgSpread = pulse.avg_spread ?? pulse.avg_spread_bps;
  const dataGaps = binancePulseDataGaps(pulse);
  const dataGapCount = binancePulseDataGapCount(pulse);
  const dataGapText = dataGaps.length
    ? dataGaps.map((item) => String(item || "-")).join(" · ")
    : (dataGapCount === null ? "-" : fmtNum(dataGapCount, 0));
  const brief = binanceHoldObject(pulse.regime_brief);
  const laneBias = binanceHoldObject(pulse.lane_bias || brief.lane_bias);
  const horizonBias = binanceHoldObject(pulse.horizon_bias || brief.horizon_bias);
  const externalNotes = Array.isArray(pulse.external_notes)
    ? pulse.external_notes
    : (Array.isArray(brief.external_notes) ? brief.external_notes : []);
  const derivativesNotes = Array.isArray(pulse.derivatives_notes)
    ? pulse.derivatives_notes
    : (Array.isArray(brief.derivatives_notes) ? brief.derivatives_notes : []);
  const compactObject = (value) => Object.entries(value || {})
    .map(([key, row]) => `${key}=${row}`)
    .join(" · ") || "-";
  const compactNote = (row) => {
    if (!row || typeof row !== "object") return String(row || "-");
    return [
      row.source_id || row.symbol || row.key || "note",
      row.key && row.source_id ? row.key : "",
      row.headline || row.squeeze_risk || "",
      row.funding_rate !== undefined ? `funding ${binanceDiagnosticNumber(row.funding_rate, 8)}` : "",
      row.basis_pct !== undefined ? `basis ${binanceDiagnosticNumber(row.basis_pct, 4)}%` : "",
    ].filter(Boolean).join(" · ");
  };
  const lines = [
    `- status/regime: ${pulse.status || "-"} / ${pulse.regime || "-"}`,
    `- market_direction: ${pulse.market_direction || brief.market_direction || "-"}`,
    `- risk_posture: ${pulse.risk_posture || brief.risk_posture || "-"}`,
    `- operator_summary: ${pulse.operator_summary_ko || brief.operator_summary_ko || "-"}`,
    `- lane_bias: ${compactObject(laneBias)}`,
    `- horizon_bias: ${compactObject(horizonBias)}`,
    `- major_count: ${binanceDiagnosticNumber(pulse.major_count, 0)}`,
    `- avg_major_change: ${binanceDiagnosticNumber(avgMajorChange, 4)}%`,
    `- avg_spread: ${binanceDiagnosticNumber(avgSpread, 2)} bps`,
    `- candidate flow: total ${binanceDiagnosticNumber(pulse.candidate_count, 0)} · long ${binanceDiagnosticNumber(pulse.long_candidate_count, 0)} · short ${binanceDiagnosticNumber(pulse.short_candidate_count, 0)} · hold ${binanceDiagnosticNumber(pulse.hold_candidate_count, 0)}`,
    `- data_gaps: ${dataGapText}`,
  ];
  if (externalNotes.length) {
    lines.push(`- external_notes: ${externalNotes.slice(0, 4).map(compactNote).join(" / ")}`);
  }
  if (derivativesNotes.length) {
    lines.push(`- derivatives_notes: ${derivativesNotes.slice(0, 4).map(compactNote).join(" / ")}`);
  }
  return lines.join("\n");
}

function binanceEntryPreflightBlockedEvents(events) {
  return (Array.isArray(events) ? events : []).filter((row) => (
    String(row?.event_type || row?.type || "") === "entry_preflight_blocked"
  ));
}

function binancePreflightEventLine(row) {
  if (!row || typeof row !== "object") return String(row || "-");
  const payload = binanceHoldObject(row.payload);
  const spread = payload.spread_bps ?? payload.spread;
  const spreadSuffix = binanceHasDiagnosticValue(spread)
    ? ` · spread ${fmtNum(spread, 2)}${payload.spread_bps !== undefined ? " bps" : ""}`
    : "";
  return [
    row.created_at ? fmtKST(row.created_at, true) : "",
    payload.market || row.market || "",
    payload.symbol || row.symbol || row.block_id || "-",
    payload.reason || row.message || "entry_preflight_blocked",
  ].filter(Boolean).join(" · ") + spreadSuffix;
}

function renderBinanceHoldDecisionDetailText({
  latest,
  hold,
  summary,
  reasons,
  watchSymbols,
  triggers,
  gaps,
  riskNotes,
  plannedActions,
  events,
}) {
  const prompt = latest?.prompt && typeof latest.prompt === "object"
    ? latest.prompt
    : (latest?.decision_context && typeof latest.decision_context === "object" ? latest.decision_context : {});
  const generation = prompt.candidate_generation && typeof prompt.candidate_generation === "object"
    ? prompt.candidate_generation
    : {};
  const bookErrors = Array.isArray(generation.book_errors) ? generation.book_errors : [];
  const cryptoPulse = binanceHoldObject(prompt.crypto_market_pulse);
  const rawRefs = prompt.raw_context_refs && typeof prompt.raw_context_refs === "object" ? prompt.raw_context_refs : {};
  const researchRef = rawRefs.crypto_research && typeof rawRefs.crypto_research === "object"
    ? rawRefs.crypto_research
    : {};
  const decisionPacket = prompt.decision_packet && typeof prompt.decision_packet === "object"
    ? prompt.decision_packet
    : {};
  const diagnostics = prompt.diagnostics && typeof prompt.diagnostics === "object"
    ? prompt.diagnostics
    : {};
  const blockers = Array.isArray(diagnostics.top_blockers) ? diagnostics.top_blockers : [];
  const promptCandidates = Array.isArray(prompt.candidates) ? prompt.candidates : [];
  const evidenceRows = Array.isArray(decisionPacket.evidence)
    ? decisionPacket.evidence
    : Array.isArray(decisionPacket.items)
      ? decisionPacket.items
      : [];
  const candidateBookRows = promptCandidates.map(binanceCandidateBookLine).filter(Boolean);
  const preflightEvents = binanceEntryPreflightBlockedEvents(events);
  const section = (title, rows, formatter) => {
    if (!Array.isArray(rows) || !rows.length) return `${title} (0)\n- 없음`;
    return `${title} (${rows.length})\n${rows.map((row, index) => `${index + 1}. ${formatter(row)}`).join("\n")}`;
  };
  const triggerLine = (row) => {
    if (!row || typeof row !== "object") return String(row || "-");
    const symbol = row.symbol || "-";
    const market = row.market || "spot";
    const price = row.price ? ` @ ${fmtNum(row.price, 6)}` : "";
    const condition = row.condition || "조건 감시";
    const reason = row.reason ? ` · ${row.reason}` : "";
    return `${market} · ${symbol}${price} · ${condition}${reason}`;
  };
  const candidateLine = (row) => {
    if (!row || typeof row !== "object") return String(row || "-");
    const calculated = row.calculated && typeof row.calculated === "object" ? row.calculated : {};
    const entry = row.entry_price || calculated.entry_price;
    const target = row.target_price || calculated.target_price;
    const stop = row.stop_price || calculated.stop_price;
    const rr = calculated.reward_risk || row.reward_risk || row.rr;
    const entryStyle = row.entry_style || calculated.entry_style || "-";
    return [
      `${row.market || "spot"} · ${row.symbol || "-"}`,
      row.side || "-",
      row.horizon || "-",
      `entry ${entry ? fmtNum(entry, 6) : "-"}`,
      `target ${target ? fmtNum(target, 6) : "-"}`,
      `stop ${stop ? fmtNum(stop, 6) : "-"}`,
      `R/R ${rr ? fmtNum(rr, 2) : "-"}`,
      `style ${entryStyle}`,
    ].join(" · ");
  };
  const evidenceLine = (row) => {
    if (!row || typeof row !== "object") return String(row || "-");
    return [
      row.source || row.source_id || "source",
      row.symbol || "-",
      row.signal_type || row.type || "-",
      row.confidence !== undefined ? `conf ${fmtNum(row.confidence, 2)}` : "",
    ].filter(Boolean).join(" · ");
  };
  return [
    `판단 시각: ${latest?.run_at ? fmtKST(latest.run_at) : "-"}`,
    `모델: ${latest?.model || "-"}`,
    `상태: ${latest?.status || "-"}`,
    `액션 수: ${fmtNum(Number(latest?.action_count ?? hold?.action_count ?? 0), 0)}`,
    "",
    "요약",
    summary || "-",
    "",
    "후보/근거 흐름",
    `- 리서치 후보: ${researchRef.candidate_count ?? generation.research_candidate_count ?? "--"}`,
    `- 리서치 아이템: ${researchRef.item_count ?? "--"}`,
    `- 실행 설계 후보: ${generation.candidate_count ?? promptCandidates.length ?? "--"}`,
    `- LLM 입력 후보: ${promptCandidates.length || "--"}`,
    `- 의사결정 근거: ${evidenceRows.length || researchRef.evidence_count || "--"}`,
    `- book_enriched_count: ${generation.book_enriched_count ?? "--"}`,
    `- book_errors: ${bookErrors.length}`,
    `- blocker tags: ${blockers.length ? blockers.map((row) => `${row.tag}:${row.count}`).join(", ") : "--"}`,
    `- growth governor: ${diagnostics.growth_governor_mode || "--"} / ${diagnostics.growth_governor_scope || "--"}`,
    `- live authority: ${diagnostics.live_authority_grade || "--"}`,
    `- prompt budget: warn=${diagnostics.prompt_over_warn ? "yes" : "no"} max=${diagnostics.prompt_over_max ? "yes" : "no"}`,
    "",
    section("blocker tags", blockers, (row) => `${row.tag || "-"} · ${row.count || 0}`),
    "",
    section("book_errors", bookErrors, binanceBookErrorLine),
    "",
    "crypto_market_pulse",
    binanceCryptoPulseLines(cryptoPulse),
    "",
    section("후보별 book_fresh", candidateBookRows, (row) => row),
    "",
    section("entry_preflight_blocked", preflightEvents, binancePreflightEventLine),
    "",
    section("watch symbols", watchSymbols, (row) => String(row || "-")),
    "",
    section("관망 이유", reasons, (row) => String(row || "-")),
    "",
    section("계획 액션", plannedActions, (row) => String(row || "-")),
    "",
    section("다음 트리거", triggers, triggerLine),
    "",
    section("데이터 공백", gaps, (row) => String(row || "-")),
    "",
    section("리스크 노트", riskNotes, (row) => String(row || "-")),
    "",
    section("실행 설계 후보", promptCandidates, candidateLine),
    "",
    section("근거 패킷", evidenceRows, evidenceLine),
  ].join("\n");
}

function renderBinanceHoldDecision(payload) {
  const runs = Array.isArray(payload?.manager_runs) ? payload.manager_runs : [];
  const latest = runs[0] || {};
  const response = latest.response && typeof latest.response === "object"
    ? latest.response
    : (latest.decision_payload && typeof latest.decision_payload === "object" ? { payload: latest.decision_payload } : {});
  const hold = latest.hold_decision && typeof latest.hold_decision === "object"
    ? latest.hold_decision
    : (response.hold_decision && typeof response.hold_decision === "object" ? response.hold_decision : {});
  const reasons = Array.isArray(hold.reasons) ? hold.reasons : [];
  const watchSymbols = Array.isArray(hold.watch_symbols) ? hold.watch_symbols : [];
  const triggers = Array.isArray(hold.next_triggers) ? hold.next_triggers : [];
  const plannedActions = Array.isArray(hold.planned_actions)
    ? hold.planned_actions
    : (Array.isArray(response.payload?.next_actions) ? response.payload.next_actions : []);
  const gaps = Array.isArray(hold.data_gaps) ? hold.data_gaps : [];
  const riskNotes = Array.isArray(hold.risk_notes) ? hold.risk_notes : [];
  const events = Array.isArray(payload?.events)
    ? payload.events
    : (Array.isArray(payload?.recent_events) ? payload.recent_events : []);
  const actionCount = Number(latest.action_count ?? hold.action_count ?? 0);
  const summary = hold.summary
    || response.payload?.claim
    || response.payload?.thesis
    || (latest.status === "ok" && actionCount === 0
      ? "최근 판단은 관망입니다. 다음 매니저 실행에서 조건을 다시 봅니다."
      : latest.error_message || "아직 저장된 관망 판단이 없습니다.");
  const prompt = latest.prompt && typeof latest.prompt === "object" ? latest.prompt : {};
  const compactContext = latest.decision_context && typeof latest.decision_context === "object"
    ? latest.decision_context
    : {};
  const contextForDiagnostics = Object.keys(prompt).length ? prompt : compactContext;
  const diagnostics = contextForDiagnostics.diagnostics && typeof contextForDiagnostics.diagnostics === "object"
    ? contextForDiagnostics.diagnostics
    : {};
  const blockers = Array.isArray(diagnostics.top_blockers) ? diagnostics.top_blockers : [];
  const generation = prompt.candidate_generation && typeof prompt.candidate_generation === "object"
    ? prompt.candidate_generation
    : compactContext.candidate_generation && typeof compactContext.candidate_generation === "object"
      ? compactContext.candidate_generation
    : {};
  const bookErrors = Array.isArray(generation.book_errors) ? generation.book_errors : [];
  const cryptoPulse = binanceHoldObject(prompt.crypto_market_pulse);
  const dataGapCount = binancePulseDataGapCount(cryptoPulse, Array.isArray(hold.data_gaps) ? gaps : null);
  const preflightEvents = binanceEntryPreflightBlockedEvents(events);
  const preflightHint = preflightEvents.length ? binancePreflightEventLine(preflightEvents[0]) : "";
  const rawRefs = prompt.raw_context_refs && typeof prompt.raw_context_refs === "object" ? prompt.raw_context_refs : {};
  const researchRef = rawRefs.crypto_research && typeof rawRefs.crypto_research === "object"
    ? rawRefs.crypto_research
    : {};
  const promptCandidates = Array.isArray(prompt.candidates) ? prompt.candidates : [];
  const detailId = registerHelperDetail({
    title: "쥬 관망 노트 전체보기",
    subtitle: "Binance Manager Hold Decision",
    body: renderBinanceHoldDecisionDetailText({
      latest,
      hold,
      summary,
      reasons,
      watchSymbols,
      triggers,
      gaps,
      riskNotes,
      plannedActions,
      events,
    }),
    meta: [
      latest.run_at ? fmtKST(latest.run_at, true) : "판단 대기",
      `watch ${watchSymbols.length}`,
      `triggers ${triggers.length}`,
      `design ${generation.candidate_count ?? promptCandidates.length ?? "--"}`,
    ],
  });
  const flowChips = [
    `리서치 후보 ${researchRef.candidate_count ?? generation.research_candidate_count ?? "--"}`,
    `실행 설계 ${generation.candidate_count ?? promptCandidates.length ?? "--"}`,
    `입력 후보 ${promptCandidates.length || "--"}`,
    `트리거 ${triggers.length}`,
  ];
  const diagnosticChips = [
    diagnostics.growth_governor_mode ? `governor ${diagnostics.growth_governor_mode}` : "",
    diagnostics.live_authority_grade ? `authority ${diagnostics.live_authority_grade}` : "",
    diagnostics.prompt_over_warn ? "prompt over warn" : "",
    generation.book_enriched_count !== undefined ? `book enriched ${generation.book_enriched_count}` : "",
    Array.isArray(generation.book_errors) ? `book errors ${bookErrors.length}` : "",
    Object.keys(cryptoPulse).length ? `pulse ${cryptoPulse.status || "-"}` : "",
    cryptoPulse.regime ? `regime ${cryptoPulse.regime}` : "",
    cryptoPulse.market_direction ? `direction ${cryptoPulse.market_direction}` : "",
    cryptoPulse.risk_posture ? `posture ${cryptoPulse.risk_posture}` : "",
    dataGapCount !== null ? `data gaps ${dataGapCount}` : "",
    preflightEvents.length ? `preflight blocked ${preflightEvents.length}` : "",
  ].filter(Boolean);
  const blockerRows = blockers.slice(0, 5).map((row) => `
    <span class="strategy-data-chip warn">${escapeHTML(`${String(row.tag || "-").replaceAll("_", " ")} ${row.count || 0}`)}</span>
  `).join("");
  const triggerRows = triggers.slice(0, 4).map((row) => `
    <div>
      <span>${escapeHTML(`${row.market || "spot"} · ${row.symbol || "-"}`)}</span>
      <strong>${escapeHTML(row.price ? `${fmtNum(row.price, 6)} · ${row.condition || "조건 감시"}` : row.condition || "조건 감시")}</strong>
      ${row.reason ? `<small class="helper-text">${escapeHTML(row.reason)}</small>` : ""}
    </div>
  `).join("");
  return `
    <section class="memory-section binance-edge-panel">
      <div class="panel-head compact">
        <div>
          <h3>쥬 관망 노트</h3>
          <p>${escapeHTML(latest.run_at ? `${fmtKST(latest.run_at)} 판단 · 액션 ${fmtNum(actionCount, 0)}개` : "최근 매니저 판단 대기")}</p>
        </div>
        <button class="btn tiny ghost" type="button" data-helper-detail-id="${escapeHTML(detailId)}">전체보기</button>
      </div>
      <p class="helper-text">${escapeHTML(summary)}</p>
      <div class="strategy-chip-row binance-hold-flow">
        ${[...flowChips, ...diagnosticChips].map((chip) => `<span class="strategy-data-chip neutral">${escapeHTML(chip)}</span>`).join("")}
      </div>
      ${blockerRows ? `<div class="strategy-chip-row">${blockerRows}</div>` : ""}
      <div class="strategy-chip-row">
        ${watchSymbols.slice(0, 8).map((symbol) => `<span class="strategy-data-chip">${escapeHTML(symbol)}</span>`).join("")
          || '<span class="strategy-data-chip">watch 대기</span>'}
        ${watchSymbols.length > 8 ? `<span class="strategy-data-chip neutral">+${escapeHTML(String(watchSymbols.length - 8))}</span>` : ""}
      </div>
      ${reasons.length ? `
        <div class="helper-card">
          <h4>관망 이유</h4>
          <p class="helper-text">${escapeHTML(reasons.slice(0, 4).join(" · "))}</p>
        </div>
      ` : ""}
      ${plannedActions.length ? `
        <div class="helper-card">
          <h4>계획 액션</h4>
          <p class="helper-text">${escapeHTML(plannedActions.slice(0, 4).join(" · "))}</p>
        </div>
      ` : ""}
      ${triggerRows ? `
        <div class="binance-edge-grid">
          ${triggerRows}
        </div>
      ` : ""}
      ${preflightHint ? `
        <div class="helper-card">
          <h4>entry_preflight_blocked</h4>
          <p class="helper-text">${escapeHTML(preflightHint)}</p>
        </div>
      ` : ""}
      ${gaps.length || riskNotes.length ? `
        <div class="helper-card">
          <h4>데이터 공백 / 리스크</h4>
          <p class="helper-text">${escapeHTML([...gaps, ...riskNotes].slice(0, 6).join(" · "))}</p>
        </div>
      ` : ""}
    </section>
  `;
}

const BINANCE_LANES = BINANCE_TAB.lanes || [
  { id: "short", label: "단기 현물", description: "빠른 모멘텀·촉매 대응" },
  { id: "mid", label: "중기 현물", description: "스윙 thesis 관리" },
  { id: "long", label: "장기 현물", description: "포지션 thesis 관리" },
  { id: "futures", label: "선물", description: "고위험 방향성 블록" },
  { id: "upbit_spot", label: "업비트 현물", description: "KRW 현물 블록" },
  { id: "volatile_attack", label: "초변동 공격", description: "소액·넓은 손절·대기진입" },
];
const BINANCE_HISTORY_STATUSES = BINANCE_TAB.historyStatuses || ["closed", "error"];

function renderBinanceTraderTab() {
  const payload = state.binanceTrader.status;
  if (state.binanceTrader.error) {
    return `<div class="notice">바이낸스 상태 조회 실패: ${escapeHTML(state.binanceTrader.error)}</div>`;
  }
  if (!payload) {
    return '<div class="notice">바이낸스 블록 상태를 불러오는 중입니다.</div>';
  }

  const risk = payload.risk || payload.risk_budget || {};
  const performance = payload.performance || payload.performance_feedback || {};
  const performanceToday = payload.performance_today || {};
  const pnlPerformance = Object.keys(performanceToday).length ? performanceToday : performance;
  const improvementPoints = Array.isArray(pnlPerformance.improvement_points)
    ? pnlPerformance.improvement_points
    : Array.isArray(performance.improvement_points)
      ? performance.improvement_points
    : [];
  const blocks = BINANCE_TAB.activeBlocks(payload);
  const killEnabled = Boolean(payload.kill_switch?.enabled || payload.summary?.kill_switch?.enabled);
  const execution = payload.execution || {};
  const model = payload.model || execution.model || payload.config?.llm_model || "gpt-5.6-sol";

  return `
    <div class="binance-trader-shell">
      ${UI_SHELL.renderWorkspaceJumpNav("binance", { escapeHTML })}
      <section id="binance-workspace-overview" class="venue-workspace-section" tabindex="-1">
      <section class="block-trader-hero">
        <div>
          <span class="section-kicker">24H Crypto Branch</span>
          <h3>바이낸스 쥬 브랜치</h3>
          <p>현물과 USD-M 선물을 별도 게이트로 관리하고, ${escapeHTML(model)}가 24시간 핵심 판단을 맡습니다.</p>
        </div>
        <div class="strategy-intel-actions">
          ${renderActiveRefreshChip("binance_trader")}
          <button class="btn ghost" type="button" data-binance-action="refresh">새로고침</button>
          <button class="btn" type="button" data-binance-action="tick">룰엔진 tick</button>
          <button class="btn warm" type="button" data-binance-action="manager" ${state.binanceTrader.running ? "disabled" : ""}>
            ${state.binanceTrader.running ? "쥬 판단 중..." : "쥬 판단 1회"}
          </button>
          <button class="btn ${killEnabled ? "" : "danger"}" type="button" data-binance-action="${killEnabled ? "kill-release" : "kill"}">
            ${killEnabled ? "킬스위치 해제" : "킬스위치"}
          </button>
        </div>
      </section>
      ${BINANCE_TAB.renderKpiGrid(payload, blocks, {
        escapeHTML,
        fmtNum,
        fmtUSDT,
        fmtPercent,
        asNumber,
      })}
      ${UI_LIVE_AUTHORITY.renderLiveAuthorityPanel(
        "binance",
        liveAuthorityForVenue("binance", payload.live_authority),
        liveAuthorityPanelOptions(),
      )}
      ${renderValidationRepairOpsPanel(payload, "바이낸스 쥬")}
      ${renderBinanceBacktestConfluencePanel(payload)}
      ${BINANCE_TAB.renderUniversePipeline(payload, { escapeHTML, fmtNum, asNumber })}
      ${BINANCE_TAB.renderGrowthTarget(payload, { escapeHTML, fmtPercent, fmtUSDT, asNumber })}
      ${BINANCE_TAB.renderGrowthGovernor(payload, {
        escapeHTML,
        fmtNum,
        fmtPercent,
        asNumber,
      })}
      ${BINANCE_TAB.renderGrowthUnlock(payload, { escapeHTML })}
      ${BINANCE_TAB.renderRiskGuard(payload, {
        escapeHTML,
        fmtNum,
        fmtUSDT,
        fmtPercent,
        asNumber,
      })}
      ${BINANCE_TAB.renderLaneEdgePanel(payload, {
        escapeHTML,
        fmtNum,
        fmtUSDT,
        fmtPercent,
        asNumber,
      })}
      <section class="memory-section binance-edge-panel">
        <div class="panel-head compact">
          <h3>리스크·성과 피드백</h3>
          <p>쥬 제안은 설정된 native 모델이 만들고, 수량과 노출은 리스크 게이트가 확정합니다.</p>
        </div>
        <div class="binance-edge-grid">
          <div>
            <span>총 노출 한도</span>
            <strong>${escapeHTML(risk.max_total_exposure_usdt ? `${fmtNum(risk.max_total_exposure_usdt, 0)} USDT` : "계좌 기반")}</strong>
          </div>
          <div>
            <span>심볼 노출 한도</span>
            <strong>${escapeHTML(fmtPercent(risk.max_symbol_exposure_pct ?? 0, 1))}</strong>
          </div>
          <div>
            <span>성과 표본</span>
            <strong>${escapeHTML(fmtNum(performance.sample_count ?? performance.closed_block_count ?? 0, 0))}</strong>
          </div>
          <div>
            <span>성과 USDT</span>
            <strong class="${asNumber(pnlPerformance.realized_pnl_usdt, 0) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtUSDT(pnlPerformance.realized_pnl_usdt, 4))}</strong>
          </div>
          <div>
            <span>최근 피드백</span>
            <strong>${escapeHTML(performance.last_feedback || performance.status || "-")}</strong>
          </div>
        </div>
        ${improvementPoints.length ? `
          <div class="helper-card">
            <h4>성과 개선포인트</h4>
            <p class="helper-text">${escapeHTML(improvementPoints.slice(0, 4).join(" · "))}</p>
          </div>
        ` : ""}
      </section>
      <section class="memory-section">
        <div class="panel-head compact">
          <h3>바이낸스 실행 상태</h3>
          <p>spot/futures 실주문 플래그가 각각 켜져야 주문이 전송됩니다.</p>
        </div>
        <div class="strategy-chip-row">
          <span class="strategy-data-chip">${escapeHTML(payload.enabled ? "runner enabled" : "runner disabled")}</span>
          <span class="strategy-data-chip">${escapeHTML(payload.status || "-")}</span>
          <span class="strategy-data-chip">${escapeHTML(model)}</span>
          <span class="strategy-data-chip">${escapeHTML(payload.reasoning_effort || payload.config?.llm_reasoning_effort || "high")}</span>
        </div>
      </section>
      ${renderBinanceHoldDecision(payload)}
      </section>
      <section id="binance-workspace-active" class="venue-workspace-section" tabindex="-1">
      ${BINANCE_TAB.renderLaneBoard(payload, blocks, {
        lanes: BINANCE_LANES,
        escapeHTML,
        fmtNum,
        fmtPercent,
        renderBlockCard: (block) => BINANCE_TAB.renderBlockCard(block, {
          escapeHTML,
          fmtNum,
          renderBlockValidationChips,
          renderValidationPassportChips,
          renderBlockPolicyEffectChips,
        }),
      })}
      </section>
      <section id="binance-workspace-history" class="venue-workspace-section" tabindex="-1">
      ${BINANCE_TAB.renderBlockHistory(payload, {
        state: state.binanceTrader,
        lanes: BINANCE_LANES,
        statuses: BINANCE_HISTORY_STATUSES,
        escapeHTML,
        fmtNum,
        fmtUSDT,
        fmtKST,
        asNumber,
      })}
      </section>
    </div>
  `;
}

function renderStatusHelperTab(payload, errorMessage, loadingLabel) {
  if (errorMessage) {
    return `<div class="notice">${escapeHTML(loadingLabel)} 상태 조회 실패: ${escapeHTML(errorMessage)}</div>`;
  }
  if (!payload) {
    return `<div class="notice">${escapeHTML(loadingLabel)} 상태를 불러오는 중입니다.</div>`;
  }

  if (typeof payload !== "object") {
    return `
      <div class="helper-grid">
        <article class="helper-card">
          <h4>${escapeHTML(loadingLabel)}</h4>
          <p class="helper-text">${escapeHTML(formatHelperValue(payload))}</p>
        </article>
      </div>
    `;
  }

  const scalarRows = Object.entries(payload)
    .filter(([, value]) => value === null || ["string", "number", "boolean"].includes(typeof value) || Array.isArray(value))
    .slice(0, 10)
    .map(
      ([key, value]) => `
        <article class="helper-card">
          <h4>${escapeHTML(key)}</h4>
          <p class="helper-text">${escapeHTML(formatHelperValue(value))}</p>
        </article>
      `
    );

  const nestedRows = Object.entries(payload)
    .filter(([, value]) => value && typeof value === "object" && !Array.isArray(value))
    .slice(0, 4)
    .map(
      ([key, value]) => `
        <article class="helper-card">
          <h4>${escapeHTML(key)}</h4>
          <pre class="helper-json mono">${escapeHTML(stringifySafe(value, true))}</pre>
        </article>
      `
    );

  const cards = [...scalarRows, ...nestedRows];
  if (!cards.length) {
    return '<div class="notice">표시할 상태 데이터가 없습니다.</div>';
  }
  return `<div class="helper-grid">${cards.join("")}</div>`;
}

function renderJueWikiAppliedIntelligencePanel() {
  const status = state.jueWikiApplicationStatus || {};
  const effectiveness = state.jueWikiApplicationEffectiveness || {};
  const pages = Array.isArray(effectiveness.pages) ? effectiveness.pages.slice(0, 8) : [];
  const latest = status.latest_recommendation && typeof status.latest_recommendation === "object"
    ? status.latest_recommendation
    : {};
  const sampleRows = pages.length
    ? pages.map((page) => {
      const label = [
        page.decision_scope || "",
        page.venue || "",
        page.horizon || "",
      ].filter(Boolean).join(" · ");
      return `
        <article class="helper-card">
          <h4>${escapeHTML(page.page_id || "wiki page")}</h4>
          <p class="helper-text">${escapeHTML(page.status || "probe")} · helpful ${escapeHTML(fmtNum(page.helpful_score || 0, 2))}</p>
          <div class="helper-research-context">
            <span>samples ${escapeHTML(fmtNum(page.sample_count || 0, 0))}</span>
            <span>win ${escapeHTML(fmtNum((page.win_rate || 0) * 100, 1))}%</span>
            <span>${escapeHTML(label || "all")}</span>
          </div>
        </article>
      `;
    }).join("")
    : `
      <article class="helper-card helper-card-wide">
        <h4>Applied Intelligence</h4>
        <p class="helper-text">아직 위키 선택과 실제 결과가 연결된 표본이 없습니다.</p>
      </article>
    `;
  return `
    <section class="wiki-effectiveness">
      <div class="helper-headline compact">
        <div>
          <span class="eyebrow">Applied Intelligence</span>
          <h4>선택 지식 효과성</h4>
        </div>
        <div class="helper-research-context">
          <span>metrics ${escapeHTML(fmtNum(status.effectiveness_count || pages.length || 0, 0))}</span>
          <span>degraded ${escapeHTML(fmtNum(status.degraded_count || 0, 0))}</span>
          <span>mode ${escapeHTML(latest.recommended_mode || "probe")}</span>
        </div>
      </div>
      <div class="helper-grid">
        ${sampleRows}
      </div>
    </section>
  `;
}

function renderJueWikiTab() {
  if (state.jueWikiError) {
    return `<div class="notice">Jue Wiki 상태 조회 실패: ${escapeHTML(state.jueWikiError)}</div>`;
  }
  if (!state.jueWikiStatus && !state.jueWikiContext && !state.jueWikiSearch) {
    return `<div class="notice">Jue Wiki 상태를 불러오는 중입니다.</div>`;
  }

  const status = state.jueWikiStatus || {};
  const context = state.jueWikiContext || {};
  const search = state.jueWikiSearch || {};
  const findings = state.jueWikiFindings || {};
  const repair = state.jueWikiRepair || {};
  const searchPages = Array.isArray(search.pages) ? search.pages : [];
  const contextPages = Array.isArray(context.pages) ? context.pages : [];
  const pages = searchPages.length ? searchPages : contextPages;
  const lintFindings = Array.isArray(findings.findings) ? findings.findings : [];
  const statusChip = helperStateChip(status.status || context.status || "unknown");
  const scope = String(state.jueWikiScope || context.target_scope || "kis");
  const selectedPage = pages.find((page) => page.page_id === state.jueWikiSelectedPageId) || pages[0] || null;
  const selectedSources = Array.isArray(selectedPage?.source_refs) ? selectedPage.source_refs : [];
  const scopeButtons = ["all", "kis", "binance"].map((item) => {
    const activeClass = item === scope ? "active" : "";
    const label = item === "binance" ? "Binance" : item === "kis" ? "KIS" : "All";
    return `
      <button class="btn tiny ghost ${activeClass}" type="button" data-jue-wiki-scope="${escapeHTML(item)}">
        ${escapeHTML(label)}
      </button>
    `;
  }).join("");
  const pageRows = pages.map((page) => {
    const meta = [
      page.scope || scope,
      page.page_type || "",
      page.freshness || "",
      page.confidence === undefined ? "" : `confidence ${fmtNum(page.confidence, 2)}`,
    ].filter((item) => item !== "");
    return `
      <article class="helper-card">
        <h4>${escapeHTML(page.title || page.page_id || "Wiki page")}</h4>
        <p class="helper-text">${escapeHTML(page.summary || page.page_id || "요약 없음")}</p>
        <div class="helper-research-context">
          ${meta.map((item) => `<span>${escapeHTML(item)}</span>`).join("")}
        </div>
        <button class="btn tiny ghost" type="button" data-jue-wiki-page-id="${escapeHTML(page.page_id || "")}">
          상세
        </button>
      </article>
    `;
  }).join("");
  const detailHtml = selectedPage
    ? `
      <article class="helper-card helper-card-wide">
        <h4>${escapeHTML(selectedPage.title || selectedPage.page_id || "Wiki detail")}</h4>
        <p class="helper-text mono">${escapeHTML(selectedPage.page_id || "")}</p>
        <p class="helper-text">${escapeHTML(selectedPage.summary || "요약 없음")}</p>
        <div class="helper-research-context">
          <span>${escapeHTML(selectedPage.scope || scope || "all")}</span>
          <span>${escapeHTML(selectedPage.page_type || "page")}</span>
          <span>sources ${escapeHTML(fmtNum(selectedPage.source_count || selectedSources.length || 0, 0))}</span>
          <span>score ${escapeHTML(fmtNum(selectedPage.score || selectedPage.confidence || 0, 2))}</span>
        </div>
        <pre class="helper-json mono">${escapeHTML(stringifySafe(selectedSources.slice(0, 5), true))}</pre>
      </article>
    `
    : "";
  const repairHtml = repair && Object.keys(repair).length
    ? `<div class="notice">최근 복구 실행: ${escapeHTML(repair.status || "unknown")} · ${escapeHTML(fmtNum((repair.actions || []).length || 0, 0))} actions</div>`
    : "";

  return `
    <div class="helper-headline">
      <div>
        <span class="eyebrow">Jue Wiki</span>
        <h4>쥬 위키 컨텍스트</h4>
      </div>
      <div class="helper-actions">
        ${scopeButtons}
        <button class="btn small" type="button" data-jue-wiki-action="refresh" ${state.jueWikiLoading ? "disabled" : ""}>
          새로고침
        </button>
        <button class="btn small ghost" type="button" data-jue-wiki-action="repair" ${state.jueWikiRepairRunning ? "disabled" : ""}>
          복구
        </button>
      </div>
    </div>
    <form class="symbol-analysis-command" id="jueWikiSearchForm">
      <label>
        Wiki search
        <input id="jue-wiki-search" class="jue-wiki-search" type="search" autocomplete="off" placeholder="예: 삼성전자 또는 BTCUSDT" value="${escapeHTML(state.jueWikiSearchQuery)}" />
      </label>
      <div class="strategy-intel-actions compact">
        <button class="btn primary" type="submit" ${state.jueWikiLoading ? "disabled" : ""}>검색</button>
      </div>
    </form>
    <section class="helper-grid">
      <article class="helper-card">
        <h4>Status</h4>
        <p class="helper-text ${escapeHTML(`status-${statusChip.cls}`)}">${escapeHTML(statusChip.text)}</p>
      </article>
      <article class="helper-card">
        <h4>Pages</h4>
        <p class="helper-text">${escapeHTML(fmtNum(status.page_count || 0, 0))}</p>
      </article>
      <article class="helper-card">
        <h4>Context</h4>
        <p class="helper-text">${escapeHTML(fmtNum(context.char_count || 0, 0))} chars</p>
      </article>
      <article class="helper-card">
        <h4>Open lint</h4>
        <p class="helper-text">${escapeHTML(fmtNum(lintFindings.length || 0, 0))}</p>
      </article>
    </section>
    ${renderJueWikiAppliedIntelligencePanel()}
    ${repairHtml}
    <section class="helper-grid">
      ${pageRows || '<article class="helper-card helper-card-wide"><h4>Selected pages</h4><p class="helper-text">아직 선택된 위키 페이지가 없습니다.</p></article>'}
      ${detailHtml}
    </section>
  `;
}

function renderSettingsPageTab() {
  if (typeof SETTINGS_TAB.renderPage !== "function") {
    return '<div class="notice">설정 렌더러를 불러오지 못했습니다.</div>';
  }
  return SETTINGS_TAB.renderPage(state.settingsPage, { escapeValue: escapeHTML, formatValue: formatHelperValue });
}

function renderHelperAgent() {
  renderGlobalExecutionMode();
  const tabsRoot = qs("helperTabs");
  const contentRoot = qs("helperContent");
  const updatedRoot = qs("helperUpdatedAt");
  const scoreRoot = qs("helperScorePill");
  if (!contentRoot || !updatedRoot || !scoreRoot) return;

  if (!HELPER_TABS.has(state.activeHelperTab)) {
    state.activeHelperTab = ASK_HELPER_TAB;
  }
  renderKisQuickStrip();
  state.helperDetailRegistry = {};
  state.helperDetailSeq = 0;

  if (tabsRoot) {
    tabsRoot.querySelectorAll("[data-helper-tab]").forEach((button) => {
      const active = button.dataset.helperTab === state.activeHelperTab;
      button.classList.toggle("active", active);
    });
  }

  let updatedAt = state.dashboard?.clock_utc || "";
  let contentHtml = "";
  const score = normalizeScore100(state.dashboard?.research?.agent_self_score_100);
  scoreRoot.textContent = score === null ? "현재 역량 --/100" : `현재 역량 ${score}/100`;
  if (state.auth.required && !state.dashboard) {
    contentHtml = renderAuthRequiredHelperPanel();
  } else if (state.activeHelperTab === "runtime") {
    contentHtml = RUNTIME_TAB.renderTab(
      {
        healthStatus: state.healthStatus,
        healthError: state.healthError,
        reportsStatus: state.reportsStatus,
        dashboard: state.dashboard,
        kisBlockStatus: state.kisBlockStatus,
        auth: state.auth,
        hasAdminToken: hasAdminToken(),
        runtimeStorage: state.runtimeStorage,
        runtimeStorageCleanup: state.runtimeStorageCleanup,
        opsReadiness: state.opsReadiness,
        llmUsage: state.llmUsage,
        llmUsageError: state.llmUsageError,
        llmUsagePeriod: state.llmUsagePeriod,
      },
      {
        escapeHTML,
        fmtKRW,
        fmtKST,
        fmtBytes,
        helperStateChip,
        normalizeNonNegativeInt,
      },
    );
    updatedAt = pickUpdatedAt(state.healthStatus) || pickUpdatedAt(state.reportsStatus) || updatedAt;
  } else if (state.activeHelperTab === "settings") {
    contentHtml = renderSettingsPageTab();
    updatedAt = pickUpdatedAt(state.settingsPage.catalog) || updatedAt;
  } else if (state.activeHelperTab === "strategy_intel") {
    contentHtml = renderStrategyIntelTab();
    updatedAt = pickUpdatedAt(state.strategyIntel.result) || updatedAt;
  } else if (isMemoryTab(state.activeHelperTab)) {
    contentHtml = renderInvestmentMemoryTab(memoryScopeForTab());
    updatedAt = pickUpdatedAt(state.investmentMemory) || updatedAt;
  } else if (state.activeHelperTab === "jue_wiki") {
    contentHtml = renderJueWikiTab();
    updatedAt = pickUpdatedAt(state.jueWikiStatus) || pickUpdatedAt(state.jueWikiContext) || updatedAt;
  } else if (state.activeHelperTab === "market_judge") {
    contentHtml = MARKET_JUDGE_TAB.renderTab(
      {
        marketJudge: state.marketJudge,
        marketPulse: state.marketPulse,
      },
      {
        escapeHTML,
        fmtNum,
        fmtKRW,
        fmtKST,
        asNumber,
      },
    );
    updatedAt = pickUpdatedAt(state.marketJudge.result?.run) || pickUpdatedAt(state.marketJudge.result) || pickUpdatedAt(state.marketPulse.result) || updatedAt;
  } else if (state.activeHelperTab === "ask") {
    contentHtml = renderAskHelperTab();
    updatedAt = pickUpdatedAt(state.helperAsk.result) || updatedAt;
  } else if (state.activeHelperTab === "rebalance") {
    contentHtml = REBALANCE_TAB.renderTab(
      state.rebalanceStatus,
      state.rebalanceError,
      {
        escapeHTML,
        fmtNum,
        fmtKRW,
        fmtKST,
        asNumber,
        helperStateChip,
      },
    );
    updatedAt = pickUpdatedAt(state.rebalanceStatus) || updatedAt;
  } else if (state.activeHelperTab === "kis_trader") {
    contentHtml = renderKisBlockTradingTab();
    updatedAt = pickUpdatedAt(state.kisBlockStatus) || pickUpdatedAt(state.dailyDiscovery) || state.etfResearch.status?.latest_score_at || state.etfResearch.status?.latest_snapshot_at || updatedAt;
  } else if (state.activeHelperTab === "binance_trader") {
    contentHtml = renderBinanceTraderTab();
    updatedAt = pickUpdatedAt(state.binanceTrader.status) || updatedAt;
  } else if (state.activeHelperTab === "crypto_research") {
    contentHtml = CRYPTO_RESEARCH_TAB.renderLabTab(state, {
      escapeHTML,
      fmtNum,
      fmtPercent,
      fmtKST,
      asNumber,
      renderEvidencePolicyFlow,
    });
    updatedAt = pickUpdatedAt(state.cryptoResearch.status) || pickUpdatedAt(state.cryptoAlpha.status) || updatedAt;
  } else if (state.activeHelperTab === "reports") {
    contentHtml = renderStatusHelperTab(state.reportsStatus, state.reportsError, "리포트 수집");
    updatedAt = pickUpdatedAt(state.reportsStatus) || updatedAt;
  } else {
    contentHtml = renderResearchHelperTab();
    updatedAt = pickUpdatedAt(state.dashboard?.research) || updatedAt;
  }

  const titleMap = {
    research: "리서치 근거",
    strategy_intel: "전략 워치",
    kis_memory: "KIS 메모리",
    binance_memory: "Binance 메모리",
    jue_wiki: "Jue Wiki / 쥬 위키",
    market_judge: "장중 판단",
    ask: "AI 질문",
    runtime: "운영/데이터",
    settings: "운영 설정",
    rebalance: "리밸런싱",
    kis_trader: "국장 블록",
    binance_trader: "바이낸스",
    crypto_research: "크립토 리서치",
    reports: "리포트 수집",
  };
  const titleRoot = qs("helperStageTitle");
  if (titleRoot) {
    titleRoot.textContent = titleMap[state.activeHelperTab] || "투자 도움 에이전트";
  }
  contentRoot.innerHTML = contentHtml;
  const modalRoot = qs("helperModalRoot");
  if (modalRoot) {
    modalRoot.innerHTML = renderHelperDetailModal();
  } else {
    contentRoot.innerHTML += renderHelperDetailModal();
  }
  updatedRoot.textContent = updatedAt ? `업데이트 KST ${fmtKST(updatedAt, true)}` : "업데이트 --";
  renderPageMode();
}

async function getJSON(path, options = {}) {
  const authHeaders = adminAuthHeaders();
  const headers = {
    "Content-Type": "application/json",
    ...authHeaders,
    ...(options.headers || {}),
  };
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers,
  });
  let data = {};
  try {
    data = await response.json();
  } catch (_) {
    data = {};
  }
  if (!response.ok) {
    const message = data.detail || "request failed";
    const error = new Error(message);
    if (response.status === 401 || response.status === 403) {
      error.authRequired = true;
      markAuthRequired(message);
    }
    throw error;
  }
  if (state.auth.required && requestHasAdminToken(headers) && isProtectedApiPath(path)) {
    clearAuthRequired();
  }
  return data;
}

async function loadSettingsCatalog() {
  state.settingsPage.loading = true;
  state.settingsPage.error = "";
  renderHelperAgent();
  try {
    state.settingsPage.catalog = await getJSON("/settings/catalog");
  } catch (error) {
    state.settingsPage.error = getErrorMessage(error);
  } finally {
    state.settingsPage.loading = false;
    renderHelperAgent();
  }
}

async function loadJueWorkflowStatus() {
  state.settingsPage.jueWorkflowLoading = true;
  state.settingsPage.jueWorkflowError = "";
  renderHelperAgent();
  try {
    state.settingsPage.jueWorkflowStatus = await getJSON("/jue/workflows/status");
  } catch (error) {
    state.settingsPage.jueWorkflowError = getErrorMessage(error);
  } finally {
    state.settingsPage.jueWorkflowLoading = false;
    renderHelperAgent();
  }
}

async function loadCodexNativeStatus(force = false) {
  state.settingsPage.codexNativeLoading = true;
  state.settingsPage.codexNativeError = "";
  renderHelperAgent();
  try {
    state.settingsPage.codexNativeStatus = await getJSON(
      force ? "/codex/native/check" : "/codex/native/status",
      force ? { method: "POST", body: JSON.stringify({}) } : {}
    );
  } catch (error) {
    state.settingsPage.codexNativeError = getErrorMessage(error);
  } finally {
    state.settingsPage.codexNativeLoading = false;
    renderHelperAgent();
  }
}

async function saveSettingsDraft() {
  const updates = { ...state.settingsPage.draft };
  const keys = Object.keys(updates);
  if (!keys.length) return;
  const items = Array.isArray(state.settingsPage.catalog?.items)
    ? state.settingsPage.catalog.items
    : [];
  const itemByKey = new Map(items.map((item) => [item.key, item]));
  const highRiskKeys = keys.filter((key) => itemByKey.get(key)?.risk === "danger");
  let confirmHighRisk = false;
  if (highRiskKeys.length) {
    confirmHighRisk = window.confirm(
      `실주문/보안 위험 설정이 포함되어 있습니다: ${highRiskKeys.join(", ")}\n저장할까요?`
    );
    if (!confirmHighRisk) return;
  }
  state.settingsPage.saving = true;
  state.settingsPage.error = "";
  state.settingsPage.saveResult = null;
  renderHelperAgent();
  try {
    const result = await getJSON("/settings/values", {
      method: "PATCH",
      body: JSON.stringify({
        updates,
        confirm_high_risk: confirmHighRisk,
      }),
    });
    state.settingsPage.saveResult = result;
    state.settingsPage.catalog = result.catalog || state.settingsPage.catalog;
    state.settingsPage.draft = {};
  } catch (error) {
    state.settingsPage.error = getErrorMessage(error);
  } finally {
    state.settingsPage.saving = false;
    renderHelperAgent();
  }
}

async function restartRunnersForSettings() {
  const confirmed = window.confirm(
    "control과 주요 runner를 재시작해 현재 .env 설정을 반영할까요?"
  );
  if (!confirmed) return;
  const kisSummary = state.opsReadiness?.kis_blocks?.summary || {};
  const kisActiveCount = Number(kisSummary.open_block_count || 0)
    + Number(kisSummary.waiting_entry_block_count || 0)
    + Number(kisSummary.pending_order_count || 0);
  const marketOpen = Boolean(state.opsReadiness?.market_clock?.is_market_open);
  let confirmActiveTradingRestart = false;
  if (marketOpen && kisActiveCount > 0) {
    confirmActiveTradingRestart = window.confirm(
      `KIS 장중 블록 ${fmtNum(kisActiveCount, 0)}개가 활성 상태입니다. 그래도 러너 재시작을 예약할까요?`
    );
    if (!confirmActiveTradingRestart) return;
  }
  state.settingsPage.restarting = true;
  state.settingsPage.error = "";
  state.settingsPage.restartResult = null;
  renderHelperAgent();
  try {
    const result = await getJSON("/ops/restart", {
      method: "POST",
      body: JSON.stringify({
        confirm_active_trading_restart: confirmActiveTradingRestart,
      }),
    });
    const keys = Array.isArray(result.keys) ? result.keys.join(", ") : "";
    state.settingsPage.restartResult = {
      ...result,
      message: keys ? `재시작 예약됨: ${keys}` : "재시작 예약됨",
    };
    window.setTimeout(() => {
      loadSettingsCatalog();
      loadCodexNativeStatus();
      loadOpsReadiness();
    }, 5000);
  } catch (error) {
    state.settingsPage.restartResult = {
      message: "재시작 요청 중 연결이 끊겼다면 control 재기동 중일 수 있습니다.",
    };
    state.settingsPage.error = getErrorMessage(error);
  } finally {
    state.settingsPage.restarting = false;
    renderHelperAgent();
  }
}

async function submitHelperAsk() {
  const query = String(state.helperAsk.query || "").trim();
  if (!query) {
    state.helperAsk.error = "질문을 입력해주세요.";
    renderHelperAgent();
    return;
  }
  state.helperAsk.loading = true;
  state.helperAsk.error = "";
  renderHelperAgent();
  try {
    const payload = {
      query,
      symbol: String(state.helperAsk.symbol || "").trim(),
      limit: 8,
    };
    state.helperAsk.result = await getJSON("/helper/ask", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  } catch (error) {
    state.helperAsk.error = getErrorMessage(error);
  } finally {
    state.helperAsk.loading = false;
    renderHelperAgent();
  }
}

async function loadStrategyIntel(useLlm = false) {
  const query = String(state.strategyIntel.query || "").trim() || "다음 거래일 관심 후보를 전략적으로 정리해줘";
  state.strategyIntel.query = query;
  state.strategyIntel.loading = true;
  state.strategyIntel.error = "";
  renderHelperAgent();
  try {
    state.strategyIntel.result = await getJSON("/strategy/brief", {
      method: "POST",
      body: JSON.stringify({
        query,
        limit: 8,
        use_llm: Boolean(useLlm),
        compact: true,
      }),
    });
    const candidates = Array.isArray(state.strategyIntel.result?.candidates)
      ? state.strategyIntel.result.candidates
      : [];
    if (
      state.strategyIntel.selectedSymbol
      && !candidates.some((row) => String(row?.symbol || "") === state.strategyIntel.selectedSymbol)
    ) {
      state.strategyIntel.selectedSymbol = "";
    }
  } catch (error) {
    state.strategyIntel.error = getErrorMessage(error);
  } finally {
    state.strategyIntel.loading = false;
    renderHelperAgent();
  }
}

async function collectStrategyInsights() {
  state.strategyIntel.collectLoading = true;
  state.strategyIntel.collectError = "";
  renderHelperAgent();
  try {
    state.strategyIntel.collectResult = await getJSON("/strategy/insights/collect", {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadStrategyIntel(false);
  } catch (error) {
    state.strategyIntel.collectError = getErrorMessage(error);
  } finally {
    state.strategyIntel.collectLoading = false;
    renderHelperAgent();
  }
}

async function collectStrategyValuations() {
  state.strategyIntel.valuationCollectLoading = true;
  state.strategyIntel.valuationCollectError = "";
  renderHelperAgent();
  try {
    state.strategyIntel.valuationCollectResult = await getJSON("/symbols/fundamentals/collect", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    });
    try {
      state.reportsStatus = await getJSON("/reports/status");
      state.reportsError = "";
    } catch (error) {
      state.reportsError = getErrorMessage(error);
    }
    await loadStrategyIntel(false);
  } catch (error) {
    state.strategyIntel.valuationCollectError = getErrorMessage(error);
  } finally {
    state.strategyIntel.valuationCollectLoading = false;
    renderHelperAgent();
  }
}

async function loadReportsStatus(options = {}) {
  const compact = Boolean(options.compact);
  const silent = Boolean(options.silent);
  const path = compact ? "/reports/status?compact=true" : "/reports/status";
  state.reportsLoading = true;
  if (!silent) {
    renderHelperAgent();
  }
  try {
    state.reportsStatus = await getJSON(path);
    state.reportsError = "";
  } catch (error) {
    state.reportsError = getErrorMessage(error);
  } finally {
    state.reportsLoading = false;
    if (!silent) {
      renderHelperAgent();
    }
  }
}

async function runSymbolAnalysis() {
  const input = symbolAnalysisInputValue();
  if (!input) {
    state.symbolAnalysis.error = "종목 코드 또는 회사명을 입력해주세요.";
    renderHelperAgent();
    return;
  }
  state.symbolAnalysis.input = input;
  state.symbolAnalysis.running = true;
  state.symbolAnalysis.error = "";
  state.symbolAnalysis.history = null;
  state.symbolAnalysis.selectedHistoryIndex = null;
  renderHelperAgent();
  try {
    const payload = await getJSON(`/symbols/${encodeURIComponent(input)}/analysis/run`, {
      method: "POST",
      body: JSON.stringify({
        trigger: "ui_symbol_analysis",
        force_collect: true,
      }),
    });
    state.symbolAnalysis.result = payload.analysis || payload;
  } catch (error) {
    state.symbolAnalysis.error = getErrorMessage(error);
  } finally {
    state.symbolAnalysis.running = false;
    renderHelperAgent();
  }
}

async function loadSymbolAnalysisHistory() {
  const input = symbolAnalysisInputValue();
  if (!input) {
    state.symbolAnalysis.error = "히스토리를 조회할 종목 코드 또는 회사명을 입력해주세요.";
    renderHelperAgent();
    return;
  }
  state.symbolAnalysis.input = input;
  state.symbolAnalysis.loading = true;
  state.symbolAnalysis.error = "";
  renderHelperAgent();
  try {
    const payload = await getJSON(`/symbols/${encodeURIComponent(input)}/analysis/history?limit=10`);
    state.symbolAnalysis.history = payload;
    const items = Array.isArray(payload?.items) ? payload.items : [];
    state.symbolAnalysis.selectedHistoryIndex = items.length ? 0 : null;
    state.symbolAnalysis.result = items.length ? symbolAnalysisPayload(items[0]) : state.symbolAnalysis.result;
  } catch (error) {
    state.symbolAnalysis.error = getErrorMessage(error);
  } finally {
    state.symbolAnalysis.loading = false;
    renderHelperAgent();
  }
}

async function loadSymbolAnalysisSpecialWatch() {
  state.symbolAnalysis.loading = true;
  state.symbolAnalysis.error = "";
  renderHelperAgent();
  try {
    state.symbolAnalysis.specialWatch = await getJSON("/symbols/special-watch");
  } catch (error) {
    state.symbolAnalysis.error = getErrorMessage(error);
  } finally {
    state.symbolAnalysis.loading = false;
    renderHelperAgent();
  }
}

async function loadMarketJudge(run = false) {
  state.marketJudge.loading = !run;
  state.marketJudge.running = Boolean(run);
  state.marketJudge.error = "";
  renderHelperAgent();
  try {
    state.marketJudge.result = await getJSON(
      run ? "/market/judgments/run-once" : "/market/judgments/latest",
      { method: run ? "POST" : "GET" },
    );
    state.candidateCoverage = state.marketJudge.result?.candidate_coverage || state.candidateCoverage;
  } catch (error) {
    state.marketJudge.error = getErrorMessage(error);
  } finally {
    state.marketJudge.loading = false;
    state.marketJudge.running = false;
    renderHelperAgent();
  }
}

async function loadMarketPulse(run = false) {
  state.marketPulse.loading = !run;
  state.marketPulse.running = Boolean(run);
  state.marketPulse.error = "";
  renderHelperAgent();
  try {
    state.marketPulse.result = await getJSON(
      run ? "/market/pulse/run-once" : "/market/pulse/latest",
      { method: run ? "POST" : "GET" },
    );
    state.marketRiskCap = state.marketPulse.result?.score_components?.risk_cap
      || state.marketPulse.result?.risk_cap
      || state.marketRiskCap;
  } catch (error) {
    state.marketPulse.error = getErrorMessage(error);
  } finally {
    state.marketPulse.loading = false;
    state.marketPulse.running = false;
    renderHelperAgent();
  }
}

async function loadEtfResearch(shouldRender = true) {
  state.etfResearch.loading = true;
  state.etfResearch.error = "";
  if (shouldRender) renderHelperAgent();
  const [statusResult, candidatesResult] = await Promise.allSettled([
    getJSON("/etf/research/status"),
    getJSON("/etf/research/candidates"),
  ]);
  if (statusResult.status === "fulfilled") {
    state.etfResearch.status = statusResult.value;
  }
  if (candidatesResult.status === "fulfilled") {
    state.etfResearch.candidates = candidatesResult.value;
  }
  const errors = [];
  if (statusResult.status === "rejected") errors.push(getErrorMessage(statusResult.reason));
  if (candidatesResult.status === "rejected") errors.push(getErrorMessage(candidatesResult.reason));
  state.etfResearch.error = [...new Set(errors)].join(" · ");
  state.etfResearch.loading = false;
  if (shouldRender) renderHelperAgent();
}

async function runEtfResearchCollect() {
  state.etfResearch.running = true;
  state.etfResearch.error = "";
  renderHelperAgent();
  try {
    await getJSON("/etf/research/collect", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    });
    const [etfResult, blockResult] = await Promise.allSettled([
      loadEtfResearch(false),
      loadKisBlocks({ includeEtf: false }),
    ]);
    const errors = [];
    if (etfResult.status === "rejected") errors.push(getErrorMessage(etfResult.reason));
    if (blockResult.status === "rejected") errors.push(getErrorMessage(blockResult.reason));
    if (errors.length) {
      state.etfResearch.error = [...new Set(errors)].join(" · ");
    }
  } catch (error) {
    state.etfResearch.error = getErrorMessage(error);
  } finally {
    state.etfResearch.running = false;
    renderHelperAgent();
  }
}

async function loadDailyDiscovery(shouldRender = true) {
  state.dailyDiscoveryLoading = true;
  state.dailyDiscoveryError = "";
  if (shouldRender) renderHelperAgent();
  try {
    state.dailyDiscovery = await getJSON("/discovery/latest");
  } catch (error) {
    state.dailyDiscoveryError = getErrorMessage(error);
  } finally {
    state.dailyDiscoveryLoading = false;
    if (shouldRender) renderHelperAgent();
  }
}

async function runDailyDiscovery() {
  state.dailyDiscoveryRunning = true;
  state.dailyDiscoveryError = "";
  renderHelperAgent();
  try {
    state.dailyDiscovery = await getJSON("/discovery/run-once", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    });
    await loadDailyDiscovery(false);
  } catch (error) {
    state.dailyDiscoveryError = getErrorMessage(error);
  } finally {
    state.dailyDiscoveryRunning = false;
    renderHelperAgent();
  }
}

async function loadKisBlocks(options = {}) {
  state.kisBlockError = "";
  state.kisBlockLoading = true;
  const activeOnly = Boolean(options.activeOnly);
  const includeEtf = options.includeEtf !== false;
  const includeDiscovery = options.includeDiscovery !== false;
  const includeJudge = options.includeJudge !== false;
  const includeOpsReadiness = options.includeOpsReadiness !== false;
  const silent = Boolean(options.silent);
  if (!silent) {
    renderHelperAgent();
  }
  try {
    const path = activeOnly
      ? "/kis/blocks?compact=true&active_only=true"
      : "/kis/blocks?compact=true";
    const payload = await getJSON(path);
    state.kisBlockStatus = activeOnly
      ? mergeKisBlockStatus(payload)
      : normalizeKisBlockPayload(payload);
    const dashboardChanged = syncDashboardKisVenueFromBlockStatus(state.kisBlockStatus);
    mergeOpsReadinessFromKisPayload(payload.readiness);
    state.exitQuality = computeExitQuality(kisBlocksForUi(state.kisBlockStatus));
    if (dashboardChanged) {
      renderDashboard();
    }
    if (!silent) {
      renderHelperAgent();
    }
    const shouldLoadOpsReadiness = includeOpsReadiness && !(activeOnly && silent);
    const auxiliaryLoads = [
      shouldLoadOpsReadiness ? loadOpsReadiness() : Promise.resolve(),
      includeEtf ? loadEtfResearch(false) : Promise.resolve(),
      includeDiscovery ? loadDailyDiscovery(false) : Promise.resolve(),
      includeJudge ? loadMarketJudge(false) : Promise.resolve(),
    ];
    await Promise.allSettled(auxiliaryLoads);
  } catch (error) {
    state.kisBlockError = getErrorMessage(error);
    const auxiliaryLoads = [
      includeEtf ? loadEtfResearch(false) : Promise.resolve(),
      includeDiscovery ? loadDailyDiscovery(false) : Promise.resolve(),
      includeJudge ? loadMarketJudge(false) : Promise.resolve(),
    ];
    await Promise.allSettled(auxiliaryLoads);
  } finally {
    state.kisBlockLoading = false;
    renderKisQuickStrip();
    renderGlobalExecutionMode();
    renderHelperAgent();
  }
}

function mergeKisBlockRows(...groups) {
  const rows = [];
  const seen = new Set();
  for (const group of groups) {
    if (!Array.isArray(group)) continue;
    for (const row of group) {
      if (!row || typeof row !== "object") continue;
      const key = String(row.block_id || `${row.symbol || ""}:${row.status || ""}:${row.created_at || ""}`);
      if (key && seen.has(key)) continue;
      if (key) seen.add(key);
      rows.push(row);
    }
  }
  return rows;
}

function kisBlocksForUi(payload) {
  if (!payload || typeof payload !== "object") return [];
  if (Array.isArray(payload.blocks)) return payload.blocks;
  return mergeKisBlockRows(payload.active_blocks, payload.block_history);
}

function normalizeKisBlockPayload(payload) {
  if (!payload || typeof payload !== "object") return payload;
  const activeBlocks = Array.isArray(payload.active_blocks) ? payload.active_blocks : [];
  const historyBlocks = Array.isArray(payload.block_history) ? payload.block_history : [];
  if (Boolean(payload.compact) && (activeBlocks.length || historyBlocks.length)) {
    return {
      ...payload,
      blocks: mergeKisBlockRows(activeBlocks, historyBlocks),
    };
  }
  if (!Array.isArray(payload.blocks) && (activeBlocks.length || historyBlocks.length)) {
    return {
      ...payload,
      blocks: mergeKisBlockRows(activeBlocks, historyBlocks),
    };
  }
  return payload;
}

function mergeKisBlockStatus(nextStatus) {
  const normalized = normalizeKisBlockPayload(nextStatus);
  if (!normalized || typeof normalized !== "object") return state.kisBlockStatus;
  const previous = state.kisBlockStatus && typeof state.kisBlockStatus === "object"
    ? state.kisBlockStatus
    : {};
  if (!normalized.active_only) {
    return normalized;
  }
  const previousBlocks = new Map(
    kisBlocksForUi(previous).map((block) => [String(block?.block_id || ""), block])
  );
  const activeBlocks = (Array.isArray(normalized.active_blocks) ? normalized.active_blocks : [])
    .map((block) => {
      const blockId = String(block?.block_id || "");
      const previousBlock = previousBlocks.get(blockId);
      return previousBlock && typeof previousBlock === "object"
        ? { ...previousBlock, ...block }
        : block;
    });
  const historyBlocks = Array.isArray(previous.block_history) ? previous.block_history : [];
  return {
    ...previous,
    ...normalized,
    active_blocks: activeBlocks,
    block_history: historyBlocks,
    blocks: mergeKisBlockRows(activeBlocks, historyBlocks),
    orders: normalized.orders || previous.orders || [],
    events: normalized.events || previous.events || [],
    latest_manager_run: normalized.latest_manager_run || previous.latest_manager_run || {},
    memory: normalized.memory || previous.memory || {},
  };
}

function mergeOpsReadinessFromKisPayload(readiness) {
  if (!readiness || typeof readiness !== "object") return;
  const previous = state.opsReadiness && typeof state.opsReadiness === "object"
    ? state.opsReadiness
    : {};
  state.opsReadiness = {
    ...previous,
    ...readiness,
    memory: readiness.memory || previous.memory || {},
    market_judge: readiness.market_judge || previous.market_judge || {},
    market_pulse: readiness.market_pulse || previous.market_pulse || {},
    kis_block_trader: readiness.kis_block_trader || previous.kis_block_trader || {},
    trading_validation: readiness.trading_validation || previous.trading_validation || {},
    remediation_actions: readiness.remediation_actions || previous.remediation_actions || [],
    operational_remediation_actions: readiness.operational_remediation_actions || previous.operational_remediation_actions || [],
    advisory_actions: readiness.advisory_actions || previous.advisory_actions || [],
    advisory_details: readiness.advisory_details || previous.advisory_details || [],
  };
  state.opsReadinessError = "";
}

function mergeBinanceStatus(nextStatus) {
  if (!nextStatus || typeof nextStatus !== "object") return state.binanceTrader.status;
  const previous = state.binanceTrader.status && typeof state.binanceTrader.status === "object"
    ? state.binanceTrader.status
    : {};
  const compact = Boolean(nextStatus.compact);
  return {
    ...previous,
    ...nextStatus,
    account: compact ? (previous.account || nextStatus.account || {}) : (nextStatus.account || previous.account || {}),
    execution: nextStatus.execution || previous.execution || {},
    risk: nextStatus.risk || previous.risk || {},
    performance: nextStatus.performance || previous.performance || {},
    config: nextStatus.config || previous.config || {},
    blocks: nextStatus.blocks || previous.blocks || [],
    active_blocks: nextStatus.active_blocks || previous.active_blocks || [],
    block_history: nextStatus.block_history || previous.block_history || [],
    lane_allocation: nextStatus.lane_allocation || previous.lane_allocation || {},
    orders: nextStatus.orders || previous.orders || [],
    events: nextStatus.events || previous.events || [],
    manager_runs: nextStatus.manager_runs || previous.manager_runs || [],
  };
}

async function loadBinanceBlocks(action = "refresh", options = {}) {
  const runManager = action === "manager";
  const runTick = action === "tick";
  const passive = action === "auto";
  const silent = Boolean(options.silent || passive);
  const includeContext = options.includeContext !== false && !passive;
  const liveExecution = binanceLiveExecutionEnabled();
  if (runManager && !confirmBinanceLiveManualAction("LLM 매니저 1회 실행")) return;
  if (runTick && !confirmBinanceLiveManualAction("룰엔진 tick 실행")) return;
  state.binanceTrader.loading = !silent && !runManager && !runTick;
  state.binanceTrader.running = runManager || runTick;
  state.binanceTrader.error = "";
  if (!silent) {
    renderHelperAgent();
  }
  try {
    if (runManager) {
      await getJSON("/binance/blocks/manager/run-once", {
        method: "POST",
        body: JSON.stringify(
          liveExecution ? { confirm_live_manager_run: true } : {}
        ),
      });
    } else if (runTick) {
      await getJSON("/binance/blocks/executor/tick", {
        method: "POST",
        body: JSON.stringify(
          liveExecution ? { confirm_live_executor_tick: true } : {}
        ),
      });
    } else if (action === "kill") {
      await getJSON("/binance/blocks/kill-switch", {
        method: "POST",
        body: JSON.stringify({ reason: "ui" }),
      });
    } else if (action === "kill-release") {
      await getJSON("/binance/blocks/kill-switch/release", {
        method: "POST",
        body: JSON.stringify({ reason: "ui_release" }),
      });
    }
    const statusPath = passive ? "/binance/blocks/status?compact=1" : "/binance/blocks/status";
    state.binanceTrader.status = mergeBinanceStatus(await getJSON(statusPath));
    if (includeContext) {
      try {
        const quantPayload = await getJSON("/binance/quant/signals?limit=24");
        state.binanceTrader.quantSignals = Array.isArray(quantPayload.items) ? quantPayload.items : [];
        state.binanceTrader.quantError = "";
      } catch (error) {
        state.binanceTrader.quantSignals = [];
        state.binanceTrader.quantError = getErrorMessage(error);
      }
      try {
        state.binanceTrader.patternContext = await getJSON("/binance/patterns/context?limit=12");
        state.binanceTrader.patternError = "";
      } catch (error) {
        state.binanceTrader.patternContext = null;
        state.binanceTrader.patternError = getErrorMessage(error);
      }
    }
  } catch (error) {
    state.binanceTrader.error = getErrorMessage(error);
  } finally {
    state.binanceTrader.loading = false;
    state.binanceTrader.running = false;
    renderHelperAgent();
  }
}

async function loadCryptoResearch(action = "refresh") {
  const runCollect = action === "collect";
  const runResearch = action === "run";
  state.cryptoResearch.loading = !runCollect && !runResearch;
  state.cryptoResearch.running = runCollect || runResearch;
  state.cryptoResearch.error = "";
  renderHelperAgent();
  try {
    if (runCollect) {
      state.cryptoResearch.result = await getJSON("/crypto/research/collect", {
        method: "POST",
        body: JSON.stringify({}),
      });
    } else if (runResearch) {
      state.cryptoResearch.result = await getJSON("/crypto/research/run-once", {
        method: "POST",
        body: JSON.stringify({}),
      });
    }
    const [status, context] = await Promise.all([
      getJSON("/crypto/research/status"),
      getJSON("/crypto/research/context?limit=12"),
    ]);
    state.cryptoResearch.status = status;
    state.cryptoResearch.context = context;
  } catch (error) {
    state.cryptoResearch.error = getErrorMessage(error);
  } finally {
    state.cryptoResearch.loading = false;
    state.cryptoResearch.running = false;
    renderHelperAgent();
  }
}

async function loadCryptoAlpha(action = "refresh") {
  const runCollect = action === "collect";
  const runOutcomes = action === "outcomes";
  state.cryptoAlpha.loading = !runCollect && !runOutcomes;
  state.cryptoAlpha.running = runCollect || runOutcomes;
  state.cryptoAlpha.error = "";
  renderHelperAgent();
  try {
    if (runCollect) {
      state.cryptoAlpha.result = await getJSON("/crypto/alpha/collect", {
        method: "POST",
      });
    } else if (runOutcomes) {
      state.cryptoAlpha.result = await getJSON("/crypto/alpha/outcomes/run-once", {
        method: "POST",
      });
    }
    const [status, context] = await Promise.all([
      getJSON("/crypto/alpha/status"),
      getJSON("/crypto/alpha/context?limit=12"),
    ]);
    state.cryptoAlpha.status = status;
    state.cryptoAlpha.context = context;
  } catch (error) {
    state.cryptoAlpha.error = getErrorMessage(error);
  } finally {
    state.cryptoAlpha.loading = false;
    state.cryptoAlpha.running = false;
    renderHelperAgent();
  }
}

async function loadEvidencePolicy(shouldRender = true) {
  state.evidencePolicy.loading = true;
  state.evidencePolicy.error = "";
  if (shouldRender) renderHelperAgent();
  const shouldRefreshVisibleFlow = () => (
    state.activePage === "helper"
    && (state.activeHelperTab === "crypto_research" || isMemoryTab(state.activeHelperTab))
  );
  try {
    const [status, context] = await Promise.all([
      getJSON("/evidence-policy/status"),
      getJSON(`${EVIDENCE_POLICY_CONTEXT_PATH}?limit=12`),
    ]);
    state.evidencePolicy.status = status;
    state.evidencePolicy.context = context;
  } catch (error) {
    state.evidencePolicy.error = getErrorMessage(error);
  } finally {
    state.evidencePolicy.loading = false;
    if (shouldRender || shouldRefreshVisibleFlow()) renderHelperAgent();
  }
}

async function saveKisBlockDirective(blockId) {
  state.kisBlockError = "";
  const cards = Array.from(document.querySelectorAll("[data-kis-block-id]"));
  const card = cards.find((node) => String(node.dataset.kisBlockId || "") === String(blockId));
  const message = String(card?.querySelector("[data-block-directive-message]")?.value || "").trim();
  const preferredHorizon = String(card?.querySelector("[data-block-directive-horizon]")?.value || "").trim();
  if (!message) {
    state.kisBlockError = "쥬에게 전달할 블록 의견을 입력해 주세요.";
    renderHelperAgent();
    return;
  }
  renderHelperAgent();
  try {
    await getJSON(`/kis/blocks/${encodeURIComponent(blockId)}/directive`, {
      method: "POST",
      body: JSON.stringify({
        message,
        preferred_horizon: preferredHorizon,
      }),
    });
    await loadKisBlocks();
  } catch (error) {
    state.kisBlockError = getErrorMessage(error);
    renderHelperAgent();
  }
}

async function loadOpsReadiness() {
  try {
    state.opsReadiness = await getJSON("/ops/readiness?compact=true");
    state.opsReadinessError = "";
  } catch (error) {
    state.opsReadiness = null;
    state.opsReadinessError = getErrorMessage(error);
  } finally {
    renderOpsBanner();
  }
}

async function loadRebalanceStatus() {
  try {
    state.rebalanceStatus = await getJSON("/rebalance/kis-status");
    state.rebalanceError = "";
  } catch (error) {
    state.rebalanceStatus = null;
    state.rebalanceError = getErrorMessage(error);
  } finally {
    if (state.activePage === "helper" && state.activeHelperTab === "rebalance") {
      renderHelperAgent();
    }
  }
}

async function loadLLMUsage() {
  try {
    state.llmUsage = await getJSON(llmUsageSummaryPath());
    state.llmUsageError = "";
  } catch (error) {
    state.llmUsage = null;
    state.llmUsageError = getErrorMessage(error);
  }
}

async function loadRuntimeStorage() {
  if (state.runtimeStorageLoading) return;
  state.runtimeStorageLoading = true;
  try {
    state.runtimeStorage = await getJSON("/runtime/storage");
    state.runtimeStorageError = "";
  } catch (error) {
    state.runtimeStorage = null;
    state.runtimeStorageError = getErrorMessage(error);
  } finally {
    state.runtimeStorageLoading = false;
    if (state.activePage === "helper" && state.activeHelperTab === "runtime") {
      renderHelperAgent();
    }
  }
}

async function runRuntimeStorageCleanup(dryRun = true) {
  if (!dryRun) {
    const proceed = window.confirm(
      "runtime cleanup 후보 파일을 실제 삭제합니다. dry-run 결과를 확인했다면 계속 진행할까요?",
    );
    if (!proceed) return;
  }
  state.runtimeStorageCleanup.running = true;
  state.runtimeStorageCleanup.error = "";
  renderHelperAgent();
  try {
    const result = await getJSON(`/runtime/storage/cleanup?dry_run=${dryRun ? "true" : "false"}`, {
      method: "POST",
    });
    state.runtimeStorageCleanup.result = result;
    state.runtimeStorageCleanup.error = "";
    if (result?.after) {
      state.runtimeStorage = result.after;
      state.runtimeStorageError = "";
    }
  } catch (error) {
    state.runtimeStorageCleanup.error = getErrorMessage(error);
  } finally {
    state.runtimeStorageCleanup.running = false;
    renderHelperAgent();
  }
}

function llmUsageSummaryPath() {
  const period = String(state.llmUsagePeriod || "today").trim() || "today";
  return `/llm/usage/summary?period=${encodeURIComponent(period)}`;
}

async function loadMemoryReviews(shouldRender = true) {
  if (shouldRender) renderHelperAgent();
  try {
    const [weekly, monthly, revisions] = await Promise.all([
      getJSON("/memory/reviews/latest?period_type=weekly"),
      getJSON("/memory/reviews/latest?period_type=monthly"),
      getJSON("/memory/policies/revisions?limit=12"),
    ]);
    state.memoryReviews = { weekly, monthly };
    state.memoryRevisions = revisions;
    state.memoryReviewError = "";
  } catch (error) {
    state.memoryReviewError = getErrorMessage(error);
  } finally {
    if (shouldRender) renderHelperAgent();
  }
}

async function loadInvestmentMemory(scope = memoryScopeForTab()) {
  if (state.investmentMemoryLoading) return;
  state.investmentMemoryLoading = true;
  state.investmentMemoryError = "";
  state.investmentMemoryScope = scope;
  renderHelperAgent();
  try {
    const [memoryResult, sourceResult, lifecycleResult] = await Promise.allSettled([
      getJSON(memoryTodayPath(scope)),
      getJSON("/jue/source-manifest"),
      getJSON("/jue/lifecycle/latest?limit=12"),
    ]);
    if (memoryResult.status === "fulfilled") {
      state.investmentMemory = memoryResult.value;
      state.investmentMemoryScope = scope;
      state.investmentMemoryError = "";
    } else {
      state.investmentMemory = null;
      state.investmentMemoryError = getErrorMessage(memoryResult.reason);
    }
    if (sourceResult.status === "fulfilled") {
      state.jueSourceManifest = sourceResult.value;
      state.jueSourceManifestError = "";
    } else {
      state.jueSourceManifestError = getErrorMessage(sourceResult.reason);
    }
    if (lifecycleResult.status === "fulfilled") {
      state.jueLifecycleLatest = lifecycleResult.value;
      state.jueLifecycleError = "";
    } else {
      state.jueLifecycleError = getErrorMessage(lifecycleResult.reason);
    }
    await loadMemoryReviews(false);
    await loadOpsReadiness();
  } catch (error) {
    state.investmentMemoryError = getErrorMessage(error);
  } finally {
    state.investmentMemoryLoading = false;
    renderHelperAgent();
  }
}

async function loadJueWiki(scope = state.jueWikiScope || memoryScopeForTab()) {
  state.jueWikiLoading = true;
  state.jueWikiError = "";
  state.jueWikiScope = String(scope || "kis");
  renderHelperAgent();
  try {
    const scopeParam = state.jueWikiScope === "all" ? "" : state.jueWikiScope;
    const searchParams = new URLSearchParams();
    searchParams.set("query", String(state.jueWikiSearchQuery || "").trim());
    if (scopeParam) {
      searchParams.set("scope", scopeParam);
    }
    const findingsParams = new URLSearchParams();
    findingsParams.set("status", "open");
    if (scopeParam) {
      findingsParams.set("scope", scopeParam);
    }
    const effectivenessParams = new URLSearchParams();
    if (scopeParam) {
      effectivenessParams.set("scope", scopeParam);
    }
    const contextPath = scopeParam
      ? `/wiki/context?scope=${encodeURIComponent(scopeParam)}`
      : "/wiki/context";
    const effectivenessPath = effectivenessParams.toString()
      ? `/wiki/application/effectiveness?${effectivenessParams.toString()}`
      : "/wiki/application/effectiveness";
    const [status, context, search, findings, applicationStatus, applicationEffectiveness] = await Promise.all([
      getJSON("/wiki/status"),
      getJSON(contextPath),
      getJSON(`/wiki/search?${searchParams.toString()}`),
      getJSON(`/wiki/lint/findings?${findingsParams.toString()}`),
      getJSON("/wiki/application/status"),
      getJSON(effectivenessPath),
    ]);
    state.jueWikiStatus = status;
    state.jueWikiContext = context;
    state.jueWikiSearch = search;
    state.jueWikiFindings = findings;
    state.jueWikiApplicationStatus = applicationStatus;
    state.jueWikiApplicationEffectiveness = applicationEffectiveness;
  } catch (error) {
    state.jueWikiError = getErrorMessage(error);
  } finally {
    state.jueWikiLoading = false;
    renderHelperAgent();
  }
}

async function runJueWikiRepair() {
  state.jueWikiRepairRunning = true;
  state.jueWikiError = "";
  renderHelperAgent();
  try {
    const scopeParam = state.jueWikiScope === "all" ? "" : state.jueWikiScope;
    state.jueWikiRepair = await getJSON("/wiki/repair/run-once", {
      method: "POST",
      body: JSON.stringify({ scope: scopeParam }),
    });
    await loadJueWiki(state.jueWikiScope);
  } catch (error) {
    state.jueWikiError = getErrorMessage(error);
  } finally {
    state.jueWikiRepairRunning = false;
    renderHelperAgent();
  }
}

async function loadJueMemoryContextPanels() {
  try {
    const [sourceManifest, lifecycle] = await Promise.all([
      getJSON("/jue/source-manifest"),
      getJSON("/jue/lifecycle/latest?limit=12"),
    ]);
    state.jueSourceManifest = sourceManifest;
    state.jueLifecycleLatest = lifecycle;
    state.jueSourceManifestError = "";
    state.jueLifecycleError = "";
  } catch (error) {
    const message = getErrorMessage(error);
    state.jueSourceManifestError = message;
    state.jueLifecycleError = message;
  }
}

async function runMemoryPeriodReview(periodType) {
  state.memoryReviewRunning = true;
  state.memoryReviewError = "";
  renderHelperAgent();
  try {
    await getJSON("/memory/reviews/run-once", {
      method: "POST",
      body: JSON.stringify({ period_type: periodType, force: true }),
    });
    await loadMemoryReviews(false);
    await loadInvestmentMemory();
  } catch (error) {
    state.memoryReviewError = getErrorMessage(error);
  } finally {
    state.memoryReviewRunning = false;
    renderHelperAgent();
  }
}

async function runInvestmentMemoryAction(action) {
  state.investmentMemoryError = "";
  state.investmentMemoryRunning = action !== "refresh";
  renderHelperAgent();
  try {
    if (action === "refresh_jue_context") {
      await loadJueMemoryContextPanels();
      return;
    }
    if (action === "refresh") {
      await loadInvestmentMemory();
      return;
    }
    if (action === "seed_current") {
      await getJSON("/memory/seed-current", {
        method: "POST",
        body: JSON.stringify({ force: false }),
      });
      await loadInvestmentMemory();
      return;
    }
    if (action === "run_due_reflections") {
      await getJSON("/memory/reflections/run-due", {
        method: "POST",
        body: JSON.stringify({ force: false }),
      });
      await loadInvestmentMemory();
      return;
    }
    await getJSON("/memory/rituals/run-once", {
      method: "POST",
      body: JSON.stringify({ slot: action, force: true }),
    });
    await loadInvestmentMemory();
  } catch (error) {
    state.investmentMemoryError = getErrorMessage(error);
  } finally {
    state.investmentMemoryRunning = false;
    renderHelperAgent();
  }
}

async function runKisBlockAction(action, blockId = "") {
  state.kisBlockError = "";
  renderHelperAgent();
  try {
    if (action === "manager") {
      await getJSON("/kis/blocks/manager/run-once", { method: "POST", body: JSON.stringify({}) });
    } else if (action === "adopt") {
      await getJSON("/kis/blocks/adopt-existing/run-once", { method: "POST", body: JSON.stringify({}) });
    } else if (action === "tick") {
      await getJSON("/kis/blocks/executor/tick", { method: "POST", body: JSON.stringify({}) });
    } else if (action === "kill") {
      await getJSON("/kis/blocks/kill-switch", { method: "POST", body: JSON.stringify({ reason: "ui" }) });
    } else if (action === "kill-release") {
      await getJSON("/kis/blocks/kill-switch/release", { method: "POST", body: JSON.stringify({ reason: "ui" }) });
    } else if (action === "pause" && blockId) {
      await getJSON(`/kis/blocks/${encodeURIComponent(blockId)}/pause`, { method: "POST", body: JSON.stringify({ reason: "ui" }) });
    } else if (action === "resume" && blockId) {
      await getJSON(`/kis/blocks/${encodeURIComponent(blockId)}/resume`, { method: "POST", body: JSON.stringify({ reason: "ui" }) });
    } else if (action === "close" && blockId) {
      await getJSON(`/kis/blocks/${encodeURIComponent(blockId)}/close`, { method: "POST", body: JSON.stringify({ reason: "ui_close" }) });
    } else if (action === "cancel-order" && blockId) {
      await getJSON(`/kis/blocks/orders/${encodeURIComponent(blockId)}/cancel`, { method: "POST", body: JSON.stringify({ reason: "ui_cancel" }) });
    }
    await loadKisBlocks();
  } catch (error) {
    state.kisBlockError = getErrorMessage(error);
    renderHelperAgent();
  }
}

function renderDashboard() {
  renderGlobalExecutionMode();
  renderHomeOpsSummary();
  renderTopMetrics();
  renderKisQuickStrip();
  renderVenueTabs();
  renderActiveVenue();
  renderBacktestSessionOptions();

  const webhookMessage = String(
    state.dashboard?.telegram?.last_webhook_message || ""
  ).trim();
  const telegramFeed =
    webhookMessage && webhookMessage !== state.lastRenderedWebhookMessage
      ? [{ type: "telegram", message: `Webhook: ${webhookMessage}` }]
      : [];
  if (webhookMessage) {
    state.lastRenderedWebhookMessage = webhookMessage;
  }
  renderEvents([...(state.dashboard?.events || []), ...telegramFeed]);
  renderHelperAgent();
  renderPageMode();
}

function dashboardHasStaleVenues(payload) {
  const venues = Array.isArray(payload?.venues) ? payload.venues : [];
  return venues.some((venue) => {
    const status = String(venue?.status || "").toLowerCase();
    const cacheStatus = String(venue?.cache_status || "").toLowerCase();
    return status === "stale" || cacheStatus === "stale";
  });
}

function scheduleStaleDashboardRefresh(options = {}) {
  if (state.dashboardLiveRefreshInFlight || !hasAdminToken()) return;
  state.dashboardLiveRefreshInFlight = true;
  const skipKisBlocks = Boolean(options.skipKisBlocks);
  window.setTimeout(async () => {
    try {
      // Automatic stale-cache repair must not bypass backend cache/cooldown gates.
      // KIS account balance endpoints are especially sensitive to forced polling;
      // explicit user refresh still keeps the force-refresh path below.
      await refreshDashboard({
        skipKisBlocks,
        forceRefresh: false,
        autoRefreshStale: false,
      });
    } catch (error) {
      if (isAuthError(error)) {
        markAuthRequired(getErrorMessage(error));
      } else {
        setHealth("Dashboard live refresh failed", false);
      }
    } finally {
      state.dashboardLiveRefreshInFlight = false;
    }
  }, 0);
}

async function refreshDashboard(options = {}) {
  const skipKisBlocks = Boolean(options.skipKisBlocks);
  const forceRefresh = Boolean(options.forceRefresh);
  const autoRefreshStale = options.autoRefreshStale !== false;
  const useKisActiveOnly = !(state.activePage === "helper" && state.activeHelperTab === "kis_trader");
  const kisBlocksPath = useKisActiveOnly
    ? "/kis/blocks?compact=true&active_only=true"
    : "/kis/blocks?compact=true";
  state.dashboard = await getJSON(forceRefresh ? "/dashboard?refresh=true" : "/dashboard");
  setHealth("API online", true);
  renderDashboard();

  const [
    kisBlockResult,
    reportsResult,
    healthResult,
    memoryResult,
    pulseResult,
    opsResult,
    llmUsageResult,
    liveAuthorityResult,
  ] = await Promise.allSettled([
    skipKisBlocks ? Promise.resolve(null) : getJSON(kisBlocksPath),
    getJSON("/reports/status?compact=true"),
    getJSON("/health"),
    getJSON(memoryTodayPath(memoryScopeForTab())),
    getJSON("/market/pulse/latest"),
    getJSON("/ops/readiness?compact=true"),
    getJSON(llmUsageSummaryPath()),
    getJSON("/live/authority?compact=1"),
  ]);
  if (skipKisBlocks) {
    // KIS block data was prioritized by the visible KIS tab loader.
  } else if (kisBlockResult.status === "fulfilled") {
    state.kisBlockStatus = useKisActiveOnly
      ? mergeKisBlockStatus(kisBlockResult.value)
      : normalizeKisBlockPayload(kisBlockResult.value);
    syncDashboardKisVenueFromBlockStatus(state.kisBlockStatus);
    state.kisBlockError = "";
    state.exitQuality = computeExitQuality(kisBlocksForUi(state.kisBlockStatus));
  } else {
    state.kisBlockStatus = null;
    state.kisBlockError = getErrorMessage(kisBlockResult.reason);
  }
  if (liveAuthorityResult.status === "fulfilled") {
    state.liveAuthority = liveAuthorityResult.value;
    state.liveAuthorityError = "";
  } else {
    state.liveAuthority = null;
    state.liveAuthorityError = getErrorMessage(liveAuthorityResult.reason);
  }

  if (reportsResult.status === "fulfilled") {
    state.reportsStatus = reportsResult.value;
    state.reportsError = "";
  } else {
    state.reportsStatus = null;
    state.reportsError = getErrorMessage(reportsResult.reason);
  }

  if (healthResult.status === "fulfilled") {
    state.healthStatus = healthResult.value;
    state.healthError = "";
    setHealth("API online", healthResult.value?.status === "ok");
  } else {
    state.healthStatus = null;
    state.healthError = getErrorMessage(healthResult.reason);
    setHealth("API offline", false);
  }

  if (memoryResult.status === "fulfilled") {
    state.investmentMemory = memoryResult.value;
    state.investmentMemoryScope = memoryScopeForTab();
    state.investmentMemoryError = "";
    await loadMemoryReviews(false);
  } else {
    state.investmentMemory = null;
    state.investmentMemoryError = getErrorMessage(memoryResult.reason);
  }

  if (pulseResult.status === "fulfilled") {
    state.marketPulse.result = pulseResult.value;
    state.marketPulse.error = "";
    state.marketRiskCap = state.marketPulse.result?.score_components?.risk_cap
      || state.marketPulse.result?.risk_cap
      || state.marketRiskCap;
  } else {
    state.marketPulse.result = null;
    state.marketPulse.error = getErrorMessage(pulseResult.reason);
  }

  if (opsResult.status === "fulfilled") {
    state.opsReadiness = opsResult.value;
    state.opsReadinessError = "";
  } else {
    state.opsReadiness = null;
    state.opsReadinessError = getErrorMessage(opsResult.reason);
  }

  if (llmUsageResult.status === "fulfilled") {
    state.llmUsage = llmUsageResult.value;
    state.llmUsageError = "";
  } else {
    state.llmUsage = null;
    state.llmUsageError = getErrorMessage(llmUsageResult.reason);
  }

  renderOpsBanner();
  renderDashboard();
  renderHelperAgent();
  if (!forceRefresh && autoRefreshStale && dashboardHasStaleVenues(state.dashboard)) {
    scheduleStaleDashboardRefresh({ skipKisBlocks });
  }
}

function renderTelegramStatus(status) {
  const text = status.ready
    ? "Telegram 연결됨"
    : "Telegram 미연결: .env에 토큰/채팅ID 설정 후 서버 재시작 필요";
  qs("telegramStatus").textContent = text;
}

async function loadTelegramStatus() {
  const status = await getJSON("/telegram/status");
  renderTelegramStatus(status);
}

async function checkHealth() {
  try {
    const payload = await getJSON("/health");
    setHealth("API online", payload.status === "ok");
  } catch (_) {
    setHealth("API offline", false);
  }
}

function applyBacktestStatus(payload) {
  state.backtest.status = payload || {};
  BACKTEST_TAB.renderStatus(payload, {
    root: document,
    escapeHTML,
    asNumber,
    fmtKRW,
    fmtKST,
    fmtNum,
  });
}

async function refreshBacktestStatus() {
  const payload = await getJSON("/backtest/status");
  applyBacktestStatus(payload);

  const status = String(payload?.job?.status || "");
  if (status === "running") {
    if (!state.backtest.pollTimer) {
      state.backtest.pollTimer = setInterval(async () => {
        try {
          const next = await getJSON("/backtest/status");
          applyBacktestStatus(next);
          if (String(next?.job?.status || "") !== "running" && state.backtest.pollTimer) {
            clearInterval(state.backtest.pollTimer);
            state.backtest.pollTimer = null;
          }
        } catch (_) {}
      }, 1500);
    }
  } else if (state.backtest.pollTimer) {
    clearInterval(state.backtest.pollTimer);
    state.backtest.pollTimer = null;
  }
}

async function loadBacktestScenarios() {
  const payload = await getJSON("/backtest/scenarios");
  state.backtest.scenarios = BACKTEST_TAB.renderScenarios(payload.scenarios || [], {
    root: document,
    escapeHTML,
  });
}

async function loadBacktestDataStatus() {
  const payload = await getJSON("/backtest/data-status");
  state.backtest.dataStatus = BACKTEST_TAB.renderDataStatus(payload, {
    root: document,
  });
}

async function startBacktestFromUI() {
  const payload = BACKTEST_TAB.buildStartPayload(
    (id) => qs(id)?.value,
    {
      root: document,
      asNumber,
    },
  );
  if (!payload.session_ids.length) {
    throw new Error("세션을 1개 이상 선택하세요.");
  }
  await getJSON("/backtest/start", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  await refreshBacktestStatus();
  await loadBacktestDataStatus();
}

async function stopBacktestFromUI() {
  await getJSON("/backtest/stop", { method: "POST" });
  await refreshBacktestStatus();
}

async function init() {
  state.auth.token = readAdminToken();
  restoreUiState();
  applyTheme(getInitialTheme());
  qs("themeToggle").addEventListener("click", toggleTheme);
  bindEvent("authBannerToggleBtn", "click", () => {
    setAuthPromptExpanded(!state.auth.expanded, { focus: !state.auth.expanded });
  });
  bindEvent("authTokenSaveBtn", "click", async () => {
    const token = String(qs("authTokenInput")?.value || "").trim();
    state.auth.token = token;
    writeAdminToken(token);
    clearAuthRequired();
    try {
      await refreshDashboard();
      if (state.activePage === "helper") {
        ensureHelperTabData(state.activeHelperTab);
      }
      syncSystemMetricsRefresh();
    } catch (error) {
      if (isAuthError(error)) {
        markAuthRequired(getErrorMessage(error));
      } else {
        setHealth("API offline", false);
      }
    }
  });
  bindEvent("authTokenClearBtn", "click", () => {
    state.auth.token = "";
    writeAdminToken("");
    markAuthRequired("운영 토큰이 지워졌습니다.");
    setAuthPromptExpanded(false);
    syncSystemMetricsRefresh();
  });
  bindEvent("authTokenInput", "keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    qs("authTokenSaveBtn")?.click();
  });
  const mainNavBtn = qs("mainNavBtn");
  if (mainNavBtn) {
    mainNavBtn.addEventListener("click", openMainPage);
  }
  const helperNavBtn = qs("helperNavBtn");
  if (helperNavBtn) {
    helperNavBtn.addEventListener("click", () => {
      openHelperPage(resolveInitialHelperTab());
      ensureHelperTabData();
    });
  }
  document.querySelectorAll("[data-nav-helper-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      openHelperPage(String(button.dataset.navHelperTab || resolveInitialHelperTab()));
      ensureHelperTabData();
    });
  });
  document.querySelectorAll("[data-mobile-page]").forEach((button) => {
    button.addEventListener("click", () => {
      if (String(button.dataset.mobilePage || "") === "main") openMainPage();
    });
  });
  document.querySelectorAll("[data-mobile-helper-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      openHelperPage(String(button.dataset.mobileHelperTab || resolveInitialHelperTab()));
      ensureHelperTabData();
    });
  });
  bindEvent("mobileNavMoreBtn", "click", () => {
    setMobileMenuOpen(!state.mobileMenuOpen);
  });
  bindEvent("helperBackBtn", "click", openMainPage);
  qs("refreshBtn").addEventListener("click", async () => {
    try {
      await refreshDashboard({ forceRefresh: true });
      loadSystemMetrics({ silent: true });
    } catch (error) {
      if (isAuthError(error)) {
        markAuthRequired(getErrorMessage(error));
      } else {
        setHealth("API offline", false);
      }
    }
  });
  const systemMetricsWidget = qs("systemMetricsWidget");
  if (systemMetricsWidget) {
    systemMetricsWidget.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const action = target ? target.closest("[data-system-metrics-action]") : null;
      if (!action) return;
      const kind = String(action.dataset.systemMetricsAction || "");
      if (kind === "toggle") {
        state.systemMetrics.collapsed = !state.systemMetrics.collapsed;
        saveUiState();
        syncSystemMetricsRefresh();
      } else if (kind === "refresh") {
        loadSystemMetrics({ force: true });
      }
    });
  }
  const homeOpsSummary = qs("homeOpsSummary");
  if (homeOpsSummary) {
    homeOpsSummary.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      if (target.closest("[data-auth-focus]")) {
        focusAuthTokenInput();
        return;
      }
      const workspaceButton = target.closest("[data-open-helper]");
      if (!workspaceButton) return;
      openHelperPage(String(workspaceButton.dataset.openHelper || ASK_HELPER_TAB));
      ensureHelperTabData();
    });
  }
  qs("venueTabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-venue]");
    if (!button || !state.dashboard) return;
    state.activeVenueId = button.dataset.venue;
    saveUiState();
    renderDashboard();
  });
  ["kisQuickStrip", "helperKisQuickStrip"].forEach((id) => {
    const kisQuickStrip = qs(id);
    if (!kisQuickStrip) return;
    kisQuickStrip.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const authFocus = target ? target.closest("[data-auth-focus]") : null;
      if (authFocus) {
        focusAuthTokenInput();
        return;
      }
      const button = target ? target.closest("[data-venue]") : null;
      if (!button || !state.dashboard) return;
      state.activeVenueId = button.dataset.venue;
      saveUiState();
      renderDashboard();
    });
  });
  const helperTabs = qs("helperTabs");
  if (helperTabs) {
    helperTabs.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const button = target ? target.closest("[data-helper-tab]") : null;
      if (!button) return;
      state.activeHelperTab = String(button.dataset.helperTab || ASK_HELPER_TAB);
      state.helperDetailModal = null;
      saveUiState();
      renderHelperAgent();
      ensureHelperTabData();
      syncActiveBlockRefresh();
    });
  }
  const helperContent = qs("helperContent");
  if (!helperContent) return;
  helperContent.addEventListener("input", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.id === "helperAskQuery") {
      state.helperAsk.query = target.value;
    } else if (target.id === "helperAskSymbol") {
      state.helperAsk.symbol = target.value.replace(/\D/g, "").slice(0, 6);
      target.value = state.helperAsk.symbol;
    } else if (target.id === "strategyIntelQuery") {
      state.strategyIntel.query = target.value;
    } else if (target.id === "symbolAnalysisInput") {
      state.symbolAnalysis.input = target.value.trim();
    } else if (target.id === "jue-wiki-search") {
      state.jueWikiSearchQuery = target.value;
    } else if (target.id === "blockHistoryDate") {
      state.kisBlockHistory.date = target.value;
      state.kisBlockHistory.selectedBlockId = "";
      renderHelperAgent();
    } else if (target.id === "blockHistoryQuery") {
      state.kisBlockHistory.query = target.value;
      state.kisBlockHistory.selectedBlockId = "";
      renderHelperAgent();
    } else if (target.id === "binanceHistoryDate") {
      state.binanceTrader.historyDate = target.value;
      renderHelperAgent();
    } else if (target.id === "binanceHistoryQuery") {
      state.binanceTrader.historyQuery = target.value;
      renderHelperAgent();
    } else if (target.id === "settingsSearch") {
      state.settingsPage.filter = target.value;
      renderHelperAgent();
    } else if (target.dataset?.settingInput) {
      const key = String(target.dataset.settingInput || "");
      if (target.type === "checkbox") {
        state.settingsPage.draft[key] = target.checked;
      } else {
        state.settingsPage.draft[key] = target.value;
      }
      const dirtyCount = Object.keys(state.settingsPage.draft).length;
      const saveButton = qs("helperContent")?.querySelector('[data-settings-action="save"]');
      const resetButton = qs("helperContent")?.querySelector('[data-settings-action="reset"]');
      if (saveButton) {
        saveButton.disabled = dirtyCount === 0 || state.settingsPage.saving;
        saveButton.textContent = `저장 (${dirtyCount})`;
      }
      if (resetButton) {
        resetButton.disabled = dirtyCount === 0;
      }
      target.closest(".settings-row")?.classList.add("dirty");
    }
  });
  helperContent.addEventListener("change", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.id === "binanceHistoryLane") {
      state.binanceTrader.historyLane = target.value;
      renderHelperAgent();
    }
  });
  helperContent.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const workspaceJump = target ? target.closest("[data-workspace-jump]") : null;
    if (workspaceJump) {
      const section = qs(String(workspaceJump.dataset.workspaceJump || ""));
      if (section) {
        const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
        section.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
        section.focus({ preventScroll: true });
      }
      return;
    }
    const authFocus = target ? target.closest("[data-auth-focus]") : null;
    if (authFocus) {
      focusAuthTokenInput();
      return;
    }
    const llmUsagePeriodAction = target ? target.closest("[data-llm-usage-period]") : null;
    if (llmUsagePeriodAction) {
      state.llmUsagePeriod = String(llmUsagePeriodAction.dataset.llmUsagePeriod || "today");
      renderHelperAgent();
      loadLLMUsage();
      return;
    }
    const runtimeStorageCleanupAction = target ? target.closest("[data-runtime-storage-cleanup]") : null;
    if (runtimeStorageCleanupAction) {
      const mode = String(runtimeStorageCleanupAction.dataset.runtimeStorageCleanup || "dry-run");
      runRuntimeStorageCleanup(mode !== "apply");
      return;
    }
    if (target?.dataset?.helperDetailClose === "true") {
      state.helperDetailModal = null;
      renderHelperAgent();
      return;
    }
    const detailButton = target ? target.closest("[data-helper-detail-id]") : null;
    if (detailButton) {
      const detailId = String(detailButton.dataset.helperDetailId || "");
      const detail = state.helperDetailRegistry[detailId];
      if (detail) {
        state.helperDetailModal = detail;
        renderHelperAgent();
      }
      return;
    }
    const strategyQuestion = target ? target.closest("[data-strategy-question]") : null;
    if (strategyQuestion) {
      state.strategyIntel.query = String(strategyQuestion.dataset.strategyQuestion || "").trim();
      state.activeHelperTab = "strategy_intel";
      saveUiState();
      syncActiveBlockRefresh();
      loadStrategyIntel(false);
      return;
    }
    const candidateToggle = target ? target.closest("[data-strategy-candidate-toggle]") : null;
    if (candidateToggle) {
      const symbol = String(candidateToggle.dataset.strategyCandidateToggle || "").trim();
      state.strategyIntel.selectedSymbol = state.strategyIntel.selectedSymbol === symbol ? "" : symbol;
      renderHelperAgent();
      return;
    }
    const strategyAction = target ? target.closest("[data-strategy-intel-action]") : null;
    if (strategyAction) {
      if (strategyAction.dataset.strategyIntelAction === "collect") {
        collectStrategyInsights();
        return;
      }
      if (strategyAction.dataset.strategyIntelAction === "valuation_collect") {
        collectStrategyValuations();
        return;
      }
      state.strategyIntel.query = String(qs("strategyIntelQuery")?.value || state.strategyIntel.query).trim();
      loadStrategyIntel(strategyAction.dataset.strategyIntelAction === "llm");
      return;
    }
    const symbolHistoryItem = target ? target.closest("[data-symbol-analysis-history-index]") : null;
    if (symbolHistoryItem) {
      const index = Number(symbolHistoryItem.dataset.symbolAnalysisHistoryIndex);
      const items = Array.isArray(state.symbolAnalysis.history?.items) ? state.symbolAnalysis.history.items : [];
      if (Number.isInteger(index) && items[index]) {
        state.symbolAnalysis.selectedHistoryIndex = index;
        state.symbolAnalysis.result = symbolAnalysisPayload(items[index]);
        renderHelperAgent();
      }
      return;
    }
    const symbolWatchItem = target ? target.closest("[data-symbol-analysis-symbol]") : null;
    if (symbolWatchItem) {
      state.symbolAnalysis.input = String(symbolWatchItem.dataset.symbolAnalysisSymbol || "").trim();
      loadSymbolAnalysisHistory();
      return;
    }
    const symbolAnalysisAction = target ? target.closest("[data-symbol-analysis-action]") : null;
    if (symbolAnalysisAction) {
      const action = String(symbolAnalysisAction.dataset.symbolAnalysisAction || "");
      if (action === "history") {
        loadSymbolAnalysisHistory();
      } else if (action === "special_watch") {
        loadSymbolAnalysisSpecialWatch();
      }
      return;
    }
    const marketJudgeAction = target ? target.closest("[data-market-judge-action]") : null;
    if (marketJudgeAction) {
      loadMarketJudge(marketJudgeAction.dataset.marketJudgeAction === "run");
      return;
    }
    const marketPulseAction = target ? target.closest("[data-market-pulse-action]") : null;
    if (marketPulseAction) {
      loadMarketPulse(marketPulseAction.dataset.marketPulseAction === "run");
      return;
    }
    const binanceAction = target ? target.closest("[data-binance-action]") : null;
    if (binanceAction) {
      loadBinanceBlocks(String(binanceAction.dataset.binanceAction || "refresh"));
      return;
    }
    const cryptoResearchAction = target ? target.closest("[data-crypto-research-action]") : null;
    if (cryptoResearchAction) {
      loadCryptoResearch(String(cryptoResearchAction.dataset.cryptoResearchAction || "refresh"));
      return;
    }
    const cryptoAlphaAction = target ? target.closest("[data-crypto-alpha-action]") : null;
    if (cryptoAlphaAction) {
      loadCryptoAlpha(String(cryptoAlphaAction.dataset.cryptoAlphaAction || "refresh"));
      return;
    }
    const evidencePolicyAction = target ? target.closest("[data-evidence-policy-action]") : null;
    if (evidencePolicyAction) {
      loadEvidencePolicy();
      return;
    }
    const jueWikiScope = target ? target.closest("[data-jue-wiki-scope]") : null;
    if (jueWikiScope) {
      state.jueWikiSelectedPageId = "";
      loadJueWiki(String(jueWikiScope.dataset.jueWikiScope || "kis"));
      return;
    }
    const jueWikiPage = target ? target.closest("[data-jue-wiki-page-id]") : null;
    if (jueWikiPage) {
      state.jueWikiSelectedPageId = String(jueWikiPage.dataset.jueWikiPageId || "");
      renderHelperAgent();
      return;
    }
    const jueWikiAction = target ? target.closest("[data-jue-wiki-action]") : null;
    if (jueWikiAction) {
      const action = String(jueWikiAction.dataset.jueWikiAction || "refresh");
      if (action === "repair") {
        runJueWikiRepair();
      } else {
        loadJueWiki();
      }
      return;
    }
    const settingsCategory = target ? target.closest("[data-settings-category]") : null;
    if (settingsCategory) {
      state.settingsPage.category = String(settingsCategory.dataset.settingsCategory || "all");
      renderHelperAgent();
      return;
    }
    const settingsAction = target ? target.closest("[data-settings-action]") : null;
    if (settingsAction) {
      const action = String(settingsAction.dataset.settingsAction || "");
      if (action === "refresh") {
        loadSettingsCatalog();
        loadJueWorkflowStatus();
        loadCodexNativeStatus();
      } else if (action === "refresh-jue-workflows") {
        loadJueWorkflowStatus();
      } else if (action === "refresh-codex-native") {
        loadCodexNativeStatus(true);
      } else if (action === "reset") {
        state.settingsPage.draft = {};
        state.settingsPage.saveResult = null;
        renderHelperAgent();
      } else if (action === "save") {
        saveSettingsDraft();
      } else if (action === "restart") {
        restartRunnersForSettings();
      }
      return;
    }
    const periodReviewAction = target ? target.closest("[data-period-review]") : null;
    if (periodReviewAction) {
      runMemoryPeriodReview(String(periodReviewAction.dataset.periodReview || "weekly"));
      return;
    }
    const memoryAction = target ? target.closest("[data-memory-action]") : null;
    if (memoryAction) {
      runInvestmentMemoryAction(String(memoryAction.dataset.memoryAction || "refresh"));
      return;
    }
    const etfResearchAction = target ? target.closest("[data-etf-research-action]") : null;
    if (etfResearchAction) {
      const action = String(etfResearchAction.dataset.etfResearchAction || "refresh");
      if (action === "collect") {
        runEtfResearchCollect();
      } else {
        loadEtfResearch();
      }
      return;
    }
    const discoveryAction = target ? target.closest("[data-discovery-action]") : null;
    if (discoveryAction) {
      const action = String(discoveryAction.dataset.discoveryAction || "refresh");
      if (action === "run") {
        runDailyDiscovery();
      } else {
        loadDailyDiscovery();
      }
      return;
    }
    const blockHistoryDateAction = target ? target.closest("[data-block-history-action]") : null;
    if (blockHistoryDateAction) {
      const action = String(blockHistoryDateAction.dataset.blockHistoryAction || "");
      if (action === "prev-date") {
        moveBlockHistoryDate(1);
      } else if (action === "next-date") {
        moveBlockHistoryDate(-1);
      }
      renderHelperAgent();
      return;
    }
    const blockHistoryStatus = target ? target.closest("[data-block-history-status]") : null;
    if (blockHistoryStatus) {
      state.kisBlockHistory.status = String(blockHistoryStatus.dataset.blockHistoryStatus || "inactive");
      state.kisBlockHistory.selectedBlockId = "";
      renderHelperAgent();
      return;
    }
    const blockHistoryHorizon = target ? target.closest("[data-block-history-horizon]") : null;
    if (blockHistoryHorizon) {
      state.kisBlockHistory.horizon = String(blockHistoryHorizon.dataset.blockHistoryHorizon || "all");
      state.kisBlockHistory.selectedBlockId = "";
      renderHelperAgent();
      return;
    }
    const blockHistorySelect = target ? target.closest("[data-block-history-select]") : null;
    if (blockHistorySelect) {
      state.kisBlockHistory.selectedBlockId = String(blockHistorySelect.dataset.blockHistorySelect || "");
      renderHelperAgent();
      return;
    }
    const blockDirectiveSave = target ? target.closest("[data-block-directive-save]") : null;
    if (blockDirectiveSave) {
      const blockId = String(blockDirectiveSave.dataset.blockId || "");
      if (blockId) saveKisBlockDirective(blockId);
      return;
    }
    const blockAction = target ? target.closest("[data-block-action]") : null;
    if (blockAction) {
      const action = String(blockAction.dataset.blockAction || "refresh");
      const blockId = String(blockAction.dataset.blockId || blockAction.dataset.orderId || "");
      if (action === "refresh") {
        loadKisBlocks();
      } else {
        runKisBlockAction(action, blockId);
      }
      return;
    }
    const helperQuestion = target ? target.closest("[data-helper-question]") : null;
    if (!helperQuestion) return;
    state.helperAsk.query = String(helperQuestion.dataset.helperQuestion || "").trim();
    openAskPageWithQuery(state.helperAsk.query);
  });
  const helperModalRoot = qs("helperModalRoot");
  if (helperModalRoot) {
    helperModalRoot.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.dataset?.helperDetailClose !== "true") return;
      state.helperDetailModal = null;
      renderHelperAgent();
    });
  }
  helperContent.addEventListener("submit", async (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.id === "strategyIntelForm") {
      event.preventDefault();
      state.strategyIntel.query = String(qs("strategyIntelQuery")?.value || "").trim();
      await loadStrategyIntel(false);
      return;
    }
    if (target.id === "symbolAnalysisForm") {
      event.preventDefault();
      state.symbolAnalysis.input = String(qs("symbolAnalysisInput")?.value || "").trim();
      await runSymbolAnalysis();
      return;
    }
    if (target.id === "jueWikiSearchForm") {
      event.preventDefault();
      state.jueWikiSearchQuery = String(qs("jue-wiki-search")?.value || "").trim();
      state.jueWikiSelectedPageId = "";
      await loadJueWiki();
      return;
    }
    if (target.id !== "helperAskForm") return;
    event.preventDefault();
    const queryInput = qs("helperAskQuery");
    const symbolInput = qs("helperAskSymbol");
    state.helperAsk.query = String(queryInput?.value || "").trim();
    state.helperAsk.symbol = String(symbolInput?.value || "").replace(/\D/g, "").slice(0, 6);
    await submitHelperAsk();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (state.mobileMenuOpen) {
      setMobileMenuOpen(false);
      qs("mobileNavMoreBtn")?.focus();
      return;
    }
    if (state.helperDetailModal) {
      state.helperDetailModal = null;
      renderHelperAgent();
    }
  });
  window.addEventListener("visibilitychange", () => {
    syncActiveBlockRefresh();
    syncSystemMetricsRefresh();
    if (shouldAutoRefreshActiveBlocks()) {
      refreshActiveBlockPanel();
    }
  });

  syncSystemMetricsRefresh();
  await loadTelegramStatus();
  renderPageMode();
  const prioritizeVisibleHelperTab = hasAdminToken() && state.activePage === "helper";
  const shouldPreloadVisibleHelperTab = prioritizeVisibleHelperTab && !isMemoryTab(state.activeHelperTab);
  const prioritizeKisBlocks = (
    prioritizeVisibleHelperTab
    && state.activeHelperTab === "kis_trader"
    && !state.kisBlockStatus
    && !state.kisBlockLoading
  );
  if (prioritizeKisBlocks) {
    loadKisBlocks({
      includeEtf: false,
      includeDiscovery: false,
      includeJudge: false,
      includeOpsReadiness: false,
    });
  } else if (shouldPreloadVisibleHelperTab) {
    ensureHelperTabData();
  }
  const kisQuickPreload = hasAdminToken() && !prioritizeKisBlocks
    ? loadKisBlocks({
        activeOnly: true,
        includeEtf: false,
        includeDiscovery: false,
        includeJudge: false,
        includeOpsReadiness: false,
        silent: true,
      })
    : null;
  if (!hasAdminToken()) {
    markAuthRequired("운영 토큰을 입력하면 국장/블록/운영 데이터를 불러옵니다. 토큰은 브라우저 세션에만 저장됩니다.");
  } else {
    try {
      await refreshDashboard({ skipKisBlocks: prioritizeKisBlocks || Boolean(kisQuickPreload) });
    } catch (error) {
      if (isAuthError(error)) {
        markAuthRequired(getErrorMessage(error));
      } else {
        setHealth("API offline", false);
      }
    }
  }
  ensureHelperTabData();
  syncActiveBlockRefresh();
  syncSystemMetricsRefresh();
  if (shouldAutoRefreshActiveBlocks()) {
    refreshActiveBlockPanel();
  }
}

init();
