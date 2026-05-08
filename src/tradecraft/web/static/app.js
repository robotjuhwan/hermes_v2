const API = "/api";
const THEME_KEY = "hermes_theme_ai_research_v1";
const state = {
  dashboard: null,
  activeVenueId: "all",
  activePage: "main",
  view: "dashboard",
  activeHelperTab: "ask",
  kisTraderStatus: null,
  kisTraderError: "",
  kisBlockStatus: null,
  kisBlockError: "",
  investmentMemory: null,
  investmentMemoryError: "",
  investmentMemoryRunning: false,
  reportsStatus: null,
  reportsError: "",
  runtimeStorage: null,
  runtimeStorageError: "",
  rebalanceStatus: null,
  rebalanceError: "",
  healthStatus: null,
  healthError: "",
  theme: "dark",
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
  marketJudge: {
    loading: false,
    running: false,
    error: "",
    result: null,
  },
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
};

function qs(id) {
  return document.getElementById(id);
}

function bindEvent(id, eventName, handler) {
  const node = qs(id);
  if (!node) return;
  node.addEventListener(eventName, handler);
}

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => {
    if (ch === "&") return "&amp;";
    if (ch === "<") return "&lt;";
    if (ch === ">") return "&gt;";
    if (ch === '"') return "&quot;";
    return "&#39;";
  });
}

function fmtKRW(value) {
  return new Intl.NumberFormat("ko-KR", {
    maximumFractionDigits: 0,
  }).format(Number(value || 0));
}

function fmtNum(value, maxFractionDigits = 4) {
  return new Intl.NumberFormat("ko-KR", {
    maximumFractionDigits: maxFractionDigits,
  }).format(Number(value || 0));
}

function fmtMaybeKRW(value) {
  if (value === null || value === undefined) return "-";
  return fmtKRW(value);
}

function asNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function fmtKST(isoString, withDate = false) {
  if (!isoString) return "--";
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "--";
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(date);
  const pick = (type) => parts.find((part) => part.type === type)?.value || "";
  const stamp = `${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}:${pick("second")}`;
  if (withDate) return stamp;
  return `${pick("hour")}:${pick("minute")}:${pick("second")}`;
}

function fmtDurationSec(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)));
  if (seconds < 60) return `${seconds}초`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  if (hours < 24) {
    return restMinutes ? `${hours}시간 ${restMinutes}분` : `${hours}시간`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days}일 ${restHours}시간` : `${days}일`;
}

function fmtBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const digits = unitIndex >= 3 ? 1 : 0;
  return `${size.toFixed(digits)} ${units[unitIndex]}`;
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
    return deriveAllVenue(venues);
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
  const venues = state.dashboard?.venues || [];
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

function truncateWithEllipsis(value, maxChars = 180) {
  const compact = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!compact) return "-";
  const limit = Math.max(Number(maxChars) || 0, 8);
  if (compact.length <= limit) return compact;
  return `${compact.slice(0, limit - 3)}...`;
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
  const okTokens = ["ok", "online", "running", "covered", "connected", "ready", "up", "enabled", "true"];
  const warnTokens = [
    "warn",
    "stale",
    "offline",
    "error",
    "failed",
    "down",
    "missing",
    "invalid",
    "disabled",
    "false",
  ];
  const isWarn = warnTokens.some((token) => lower === token || lower.includes(token));
  const isOk = okTokens.some((token) => lower === token || lower.includes(token));
  return {
    text,
    cls: isOk && !isWarn ? "ok" : "warn",
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
  const llmBridge = intelligence.llm_bridge || {};
  const health = state.healthStatus || {};

  const score = normalizeScore100(research?.agent_self_score_100);
  const learningTotalCount = normalizeNonNegativeInt(research?.learning_total_count);
  const reportTotal = normalizeNonNegativeInt(repo.total_reports);
  const factsTotal = normalizeNonNegativeInt(facts.total_facts);
  const symbolTotal = normalizeNonNegativeInt(repo.total_symbols);
  const ragCount = normalizeNonNegativeInt(rag.count);
  const ragAvailable = boolFromStatus(rag.available, false);
  const llmFactsEnabled = boolFromStatus(
    llmFacts.enabled,
    boolFromStatus(health.naver_reports_llm_facts_enabled, false)
  );
  const llmFactsActive = boolFromStatus(
    llmFacts.active,
    boolFromStatus(health.naver_reports_llm_facts_active, false)
  );
  const llmBridgeMode = String(llmBridge.mode || health.llm_bridge_mode || "none");
  const llmBridgeReady = boolFromStatus(
    llmBridge.ready,
    boolFromStatus(health.llm_bridge_ready, false)
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
        "RAG",
        ragAvailable ? "ready" : "off",
        `chunks ${ragCount === null ? "--" : ragCount}`,
        ragTone
      )}
      ${renderResearchMetricTile(
        "LLM facts",
        llmValue,
        `bridge ${llmBridgeReady ? llmBridgeMode : "none"}`,
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
  const isHelper = state.activePage === "helper";
  const researchTabs = new Set(["ask", "strategy_intel", "research", "market_judge"]);
  const systemTabs = new Set(["runtime", "reports", "rebalance"]);
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
    mainNavBtn.classList.toggle("active", !isHelper);
  }
  const helperNavBtn = qs("helperNavBtn");
  if (helperNavBtn) {
    helperNavBtn.classList.toggle("active", isHelper && researchTabs.has(state.activeHelperTab));
  }
  document.querySelectorAll("[data-nav-helper-tab]").forEach((button) => {
    const targetTab = String(button.dataset.navHelperTab || "");
    const active =
      isHelper
      && (
        targetTab === state.activeHelperTab
        || (targetTab === "runtime" && systemTabs.has(state.activeHelperTab))
      );
    button.classList.toggle("active", active);
  });
}

function openHelperPage(tab = "ask") {
  state.activePage = "helper";
  state.activeHelperTab = tab;
  renderPageMode();
  renderHelperAgent();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openMainPage() {
  state.activePage = "main";
  state.helperDetailModal = null;
  const modalRoot = qs("helperModalRoot");
  if (modalRoot) {
    modalRoot.innerHTML = "";
  }
  renderPageMode();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function ensureHelperTabData(tab = state.activeHelperTab) {
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
  if (tab === "kis_trader" && !state.kisBlockStatus) {
    loadKisBlocks();
  }
  if (tab === "memory" && !state.investmentMemory) {
    loadInvestmentMemory();
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
  const model = String(result.model || "gpt-5.5");

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
    : "<li>수집 리포트와 RAG 문단 기반 정보 제공이며 매매 추천이 아닙니다.</li>";

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

function strategySuitability(row) {
  return row?.suitability && typeof row.suitability === "object" ? row.suitability : {};
}

function strategyHorizonLabel(key) {
  if (key === "short_term") return "단기";
  if (key === "mid_term") return "중기";
  if (key === "long_term") return "장기";
  return "균형";
}

function strategyHorizonPayload(row, key) {
  const suitability = strategySuitability(row);
  const payload = suitability[key] && typeof suitability[key] === "object" ? suitability[key] : {};
  const score = Math.max(0, Math.min(100, Number(payload.score ?? row?.score ?? 0)));
  return {
    score,
    grade: String(payload.grade || "-"),
    drivers: Array.isArray(payload.drivers) ? payload.drivers : [],
    risks: Array.isArray(payload.risks) ? payload.risks : [],
  };
}

function renderStrategySuitabilityBars(row) {
  const rows = ["short_term", "mid_term", "long_term"].map((key) => {
    const payload = strategyHorizonPayload(row, key);
    return `
      <div class="strategy-horizon-row">
        <span>${escapeHTML(strategyHorizonLabel(key))}</span>
        <b>${escapeHTML(`${payload.grade} / ${Math.round(payload.score)}`)}</b>
        <div class="strategy-horizon-track"><i style="width:${escapeHTML(String(payload.score))}%"></i></div>
      </div>
    `;
  });
  return `<div class="strategy-horizon-grid">${rows.join("")}</div>`;
}

function renderStrategySuitabilityDetail(row) {
  const coverage = row?.data_coverage && typeof row.data_coverage === "object" ? row.data_coverage : {};
  const rows = ["short_term", "mid_term", "long_term"].map((key) => {
    const payload = strategyHorizonPayload(row, key);
    const drivers = payload.drivers.slice(0, 3);
    const risks = payload.risks.slice(0, 2);
    return `
      <div class="strategy-horizon-detail">
        <div class="helper-row-head">
          <strong>${escapeHTML(strategyHorizonLabel(key))}</strong>
          <span class="helper-row-status muted">${escapeHTML(`${payload.grade} / ${Math.round(payload.score)}`)}</span>
        </div>
        <ul>
          ${(drivers.length ? drivers : ["근거 보강 필요"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}
        </ul>
        <small>${escapeHTML((risks.length ? risks : ["리스크 추가 점검"]).join(" · "))}</small>
      </div>
    `;
  });
  const missing = Array.isArray(coverage.missing) ? coverage.missing : [];
  return `
    <div class="strategy-detail-section strategy-suitability-detail">
      <strong>기간별 적합도</strong>
      <div class="strategy-horizon-detail-grid">${rows.join("")}</div>
      <p class="strategy-coverage-note">
        ${escapeHTML(`자료 커버리지 ${coverage.coverage_score ?? "-"} · 소스 ${coverage.source_count ?? "-"}개${missing.length ? ` · 미수집 ${missing.join(", ")}` : ""}`)}
      </p>
    </div>
  `;
}

function strategyDataWarnings(row) {
  const warnings = Array.isArray(row?.data_warnings)
    ? row.data_warnings.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  if (!warnings.length) {
    const coverage = row?.data_coverage && typeof row.data_coverage === "object" ? row.data_coverage : {};
    const missing = Array.isArray(coverage.missing) ? coverage.missing : [];
    return missing.slice(0, 4).map((item) => `미수집 ${item}`);
  }
  return [...new Set(warnings)].slice(0, 6);
}

function renderStrategyDataWarnings(row) {
  const warnings = strategyDataWarnings(row);
  const identity = row?.identity_status && typeof row.identity_status === "object" ? row.identity_status : {};
  const identityOk = String(identity.status || "") === "ok";
  const chips = [
    ...(identity.label ? [{ label: identity.label, tone: identityOk ? "good" : "warn" }] : []),
    ...warnings.map((label) => ({
      label,
      tone: /검증|미수집|없음|1개/.test(label) ? "warn" : "neutral",
    })),
  ];
  if (!chips.length) {
    return "";
  }
  return `
    <div class="strategy-data-warning-strip">
      ${chips
        .slice(0, 7)
        .map((item) => `<span class="strategy-data-chip ${escapeHTML(item.tone)}">${escapeHTML(item.label)}</span>`)
        .join("")}
    </div>
  `;
}

function renderStrategyDataHealth(result) {
  const candidates = Array.isArray(result?.candidates) ? result.candidates : [];
  const sources = Array.isArray(result?.sources) ? result.sources : [];
  const warningCount = candidates.reduce((sum, row) => sum + (strategyDataWarnings(row).length ? 1 : 0), 0);
  const valuationMissing = candidates.filter((row) => {
    const valuation = row?.valuation && typeof row.valuation === "object" ? row.valuation : {};
    return String(valuation.status || "").toLowerCase() !== "ok";
  }).length;
  const identitySuspect = candidates.filter((row) => {
    const identity = row?.identity_status && typeof row.identity_status === "object" ? row.identity_status : {};
    return identity.status && identity.status !== "ok";
  }).length;
  const activeSources = sources.filter((row) => ["ok", "active", "updated"].includes(String(row.status || "").toLowerCase())).length;
  return `
    <div class="strategy-data-health">
      <span>점수 ${escapeHTML(result?.score_method_version || "v2")}</span>
      <span>활성 소스 ${escapeHTML(String(activeSources))}/${escapeHTML(String(sources.length))}</span>
      <span>자료주의 ${escapeHTML(String(warningCount))}</span>
      <span>밸류 미수집 ${escapeHTML(String(valuationMissing))}</span>
      <span>종목명 검증 ${escapeHTML(String(identitySuspect))}</span>
    </div>
  `;
}

function renderStrategyCollectResult(result, errorMessage) {
  if (errorMessage) {
    return `<div class="strategy-collect-panel bad">시그널 수집 실패: ${escapeHTML(errorMessage)}</div>`;
  }
  if (!result) {
    return "";
  }
  const sources = Array.isArray(result.sources) ? result.sources : [];
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const status = String(result.status || "-");
  const sourceRows = sources
    .map((row) => {
      const warnings = Array.isArray(row.warnings) ? row.warnings : [];
      const cache = row.cache ? ` · cache ${row.cache}` : "";
      const unresolved = row.unresolved ? ` · unresolved ${row.unresolved}` : "";
      return `
        <li>
          <span>${escapeHTML(row.label || row.source_id || "source")}</span>
          <strong class="helper-runtime-chip ${escapeHTML(sourceTone(row.status))}">
            ${escapeHTML(`${row.status || "-"} · loaded ${row.loaded || 0} · inserted ${row.inserted || 0} · skipped ${row.skipped || 0}${cache}${unresolved}`)}
          </strong>
          ${warnings.length ? `<small>${escapeHTML(warnings.join(" / "))}</small>` : ""}
        </li>
      `;
    })
    .join("");
  const errorRows = errors
    .slice(0, 5)
    .map((row) => `<li>${escapeHTML(`${row.source_id || "source"}: ${row.detail || row.message || row}`)}</li>`)
    .join("");
  return `
    <div class="strategy-collect-panel ${errors.length ? "warn" : ""}">
      <div class="helper-row-head">
        <strong>시그널 수집 ${escapeHTML(status)}</strong>
        <span class="helper-row-status ${escapeHTML(sourceTone(status))}">inserted ${escapeHTML(String(result.inserted || 0))}</span>
      </div>
      ${sourceRows ? `<ul class="helper-runtime-list strategy-collect-list">${sourceRows}</ul>` : ""}
      ${errorRows ? `<ul class="helper-plain-list strategy-collect-errors">${errorRows}</ul>` : ""}
    </div>
  `;
}

function renderStrategyFundamentalsCollectResult(result, errorMessage) {
  if (errorMessage) {
    return `<div class="strategy-collect-panel bad">밸류 갱신 실패: ${escapeHTML(errorMessage)}</div>`;
  }
  if (!result) {
    return "";
  }
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const items = Array.isArray(result.items) ? result.items.slice(0, 8) : [];
  const status = String(result.status || "-");
  const itemRows = items
    .map((row) => {
      const latest = row.latest && typeof row.latest === "object" ? row.latest : {};
      const label = latest.score?.label ? ` · ${strategyValuationLabel(latest.score.label)}` : "";
      return `
        <li>
          <span>${escapeHTML(row.symbol || "-")}</span>
          <strong class="helper-runtime-chip ${escapeHTML(sourceTone(row.status))}">
            ${escapeHTML(`${row.status || "-"}${label}`)}
          </strong>
        </li>
      `;
    })
    .join("");
  const errorRows = errors
    .slice(0, 5)
    .map((row) => `<li>${escapeHTML(`${row.symbol || "symbol"}: ${row.error || row.message || row}`)}</li>`)
    .join("");
  return `
    <div class="strategy-collect-panel ${errors.length ? "warn" : ""}">
      <div class="helper-row-head">
        <strong>밸류 갱신 ${escapeHTML(status)}</strong>
        <span class="helper-row-status ${escapeHTML(sourceTone(status))}">
          ${escapeHTML(`collected ${result.collected || 0} · skipped ${result.skipped || 0}`)}
        </span>
      </div>
      ${itemRows ? `<ul class="helper-runtime-list strategy-collect-list">${itemRows}</ul>` : ""}
      ${errorRows ? `<ul class="helper-plain-list strategy-collect-errors">${errorRows}</ul>` : ""}
    </div>
  `;
}

function renderStrategyIntelSources(sources) {
  const rows = Array.isArray(sources) ? sources : [];
  if (!rows.length) {
    return '<div class="notice">전략 소스 상태가 없습니다.</div>';
  }
  return `
    <div class="strategy-intel-source-grid">
      ${rows
        .map((row) => {
          const tone = sourceTone(row.status);
          const count = row.count === undefined || row.count === null ? "-" : String(row.count);
          return `
            <article class="strategy-intel-source">
              <div class="helper-row-head">
                <h4>${escapeHTML(row.label || row.source_id || "source")}</h4>
                <span class="helper-row-status ${escapeHTML(tone)}">${escapeHTML(row.status || "unknown")}</span>
              </div>
              <p class="helper-row-summary">${escapeHTML(row.role || row.caution || "대기 중")}</p>
              <div class="helper-row-meta">
                <span>${escapeHTML(`signals ${count}`)}</span>
                <span>${escapeHTML(row.source_id || "")}</span>
              </div>
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderStrategyScoreComponents(row) {
  const components = row.score_components || {};
  const parts = [
    ["report", "리포트", components.report],
    ["research", "리서치", components.research],
    ["whale", "고래", components.whale],
    ["after_close", "종가수급", components.after_close],
    ["valuation", "밸류", components.valuation],
    ["recency", "최신성", components.recency],
    ["evidence", "근거", components.evidence],
    ["fit", "적합도", components.fit],
    ["risk", "리스크", components.risk_penalty],
  ];
  return `
    <div class="strategy-score-grid">
      ${parts
        .map(([key, label, value]) => {
          const score = Math.max(0, Math.min(100, Number(value || 0)));
          return `
            <div class="strategy-score-component ${key === "risk" ? "risk" : ""}">
              <span>${escapeHTML(label)}</span>
              <div class="strategy-score-track"><i style="width:${escapeHTML(String(score))}%"></i></div>
              <b>${escapeHTML(String(Math.round(score)))}</b>
            </div>
          `;
        })
        .join("")}
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
  const warnings = strategyDataWarnings(row);
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
      ${renderStrategySuitabilityDetail(row)}
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
  const balanced = strategyHorizonPayload(row, "balanced");
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
        ${renderStrategyDataWarnings(row)}
        ${renderStrategyValuationChips(row)}
        ${renderStrategySuitabilityBars(row)}
        ${renderStrategyScoreComponents(row)}
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
    renderStrategyCollectResult(state.strategyIntel.collectResult, state.strategyIntel.collectError),
    renderStrategyFundamentalsCollectResult(
      state.strategyIntel.valuationCollectResult,
      state.strategyIntel.valuationCollectError,
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
            <button class="btn" type="button" data-strategy-intel-action="llm" ${disabled}>gpt-5.5 브리핑</button>
            <button class="btn" type="button" data-strategy-intel-action="collect" ${collectDisabled}>시그널 수집</button>
            <button class="btn" type="button" data-strategy-intel-action="valuation_collect" ${valuationCollectDisabled}>밸류 갱신</button>
          </div>
          <div class="helper-chip-row">${quick}</div>
        </form>
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
          <button class="btn" type="button" data-strategy-intel-action="llm" ${disabled}>gpt-5.5 브리핑</button>
          <button class="btn" type="button" data-strategy-intel-action="collect" ${collectDisabled}>${state.strategyIntel.collectLoading ? "수집 중" : "시그널 수집"}</button>
          <button class="btn" type="button" data-strategy-intel-action="valuation_collect" ${valuationCollectDisabled}>${state.strategyIntel.valuationCollectLoading ? "갱신 중" : "밸류 갱신"}</button>
        </div>
        <div class="helper-chip-row">${quick}</div>
      </form>
      ${collectHtml}
      ${loadingHtml}
      ${errorHtml}
      <section class="strategy-intel-hero">
        <div>
          <span class="eyebrow">STRATEGY INTELLIGENCE</span>
          <h4>${escapeHTML(regime.label || "mixed")}</h4>
          <p>${escapeHTML(regime.stance || "시장 판단 대기")}</p>
          ${renderStrategyDataHealth(result)}
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
          <span class="pill mono">${escapeHTML(result.model || "gpt-5.5")}</span>
        </div>
        <pre class="helper-answer-text">${escapeHTML(brief)}</pre>
      </section>
      <section class="strategy-candidate-board">
        ${candidates.length ? candidates.map(renderStrategyCandidate).join("") : '<div class="notice">후보가 부족합니다.</div>'}
      </section>
      <div class="strategy-intel-grid">
        <article class="helper-card helper-card-wide">
          <h4>소스 상태</h4>
          ${renderStrategyIntelSources(result.sources)}
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

function marketActionLabel(value) {
  const labels = {
    hold: "유지",
    watch_add: "추가 관심",
    avoid_add: "추가 보류",
    trim_watch: "비중 점검",
    risk_check: "리스크 관리",
    new_watch: "신규 관심",
  };
  const key = String(value || "").trim();
  return labels[key] || key || "-";
}

function marketJudgeTone(row) {
  const action = String(row?.account_action || "");
  const stance = String(row?.stance || "");
  if (action === "risk_check" || stance === "risk_check") return "bad";
  if (action === "trim_watch") return "warn";
  if (action === "new_watch" || stance === "watch") return "good";
  return "neutral";
}

function renderMarketJudgeHeader(payload) {
  const run = payload?.run && typeof payload.run === "object" ? payload.run : {};
  const clock = payload?.clock && typeof payload.clock === "object"
    ? payload.clock
    : run.source_snapshot?.clock || {};
  const account = payload?.account && typeof payload.account === "object" ? payload.account : {};
  const status = payload?.status || run.status || "missing";
  const session = clock.session || run.market_session || "-";
  const accountLine = account.status
    ? `${fmtKRW(account.cash_krw)}원 현금 · ${fmtKRW(account.position_value_krw)}원 보유 · ${account.position_count || 0}종목`
    : "국장1 계좌 스냅샷 대기";
  return `
    <section class="market-judge-hero">
      <div>
        <span class="eyebrow">INTRADAY JUDGE</span>
        <h4>${escapeHTML(session)}</h4>
        <p>${escapeHTML(accountLine)}</p>
      </div>
      <div class="market-judge-kpis">
        <span><strong>${escapeHTML(status)}</strong>상태</span>
        <span><strong>${escapeHTML(run.mode || "-")}</strong>모드</span>
        <span><strong>${escapeHTML(run.model || "gpt-5.5")}</strong>모델</span>
        <span><strong>${escapeHTML(fmtKST(clock.now || run.run_at, true))}</strong>KST</span>
      </div>
    </section>
  `;
}

function renderMarketQuoteLine(quote) {
  if (!quote || typeof quote !== "object") return "시세 없음";
  const price = quote.price ? `${fmtKRW(quote.price)}원` : "-";
  const changePct = Number(quote.change_pct || 0);
  return `${price} · ${changePct >= 0 ? "+" : ""}${fmtNum(changePct, 2)}% · ${quote.source || "-"} · ${quote.status || "-"}`;
}

function renderMarketPositionLine(position) {
  if (!position || typeof position !== "object" || !position.symbol) return "신규/미보유 후보";
  const pnlPct = Number(position.unrealized_pnl_pct || 0);
  const weight = Number(position.position_weight || 0) * 100;
  return `보유 ${fmtKRW(position.value_krw)}원 · 손익 ${fmtKRW(position.unrealized_pnl_krw)}원 / ${pnlPct >= 0 ? "+" : ""}${fmtNum(pnlPct, 2)}% · 비중 ${fmtNum(weight, 1)}%`;
}

function renderMarketJudgmentCard(row) {
  const tone = marketJudgeTone(row);
  const quote = row?.quote && typeof row.quote === "object" ? row.quote : {};
  const position = row?.position && typeof row.position === "object" ? row.position : {};
  const strategy = row?.strategy && typeof row.strategy === "object" ? row.strategy : {};
  const reasons = Array.isArray(row?.reasons) ? row.reasons.slice(0, 3) : [];
  const risks = Array.isArray(row?.risks) ? row.risks.slice(0, 3) : [];
  const triggers = Array.isArray(row?.triggers) ? row.triggers.slice(0, 3) : [];
  const gaps = Array.isArray(row?.data_gaps) ? row.data_gaps.slice(0, 4) : [];
  return `
    <article class="market-judge-card ${escapeHTML(tone)}">
      <div class="market-judge-card-head">
        <div>
          <h4>${escapeHTML(row?.name || row?.symbol || "-")} <span>${escapeHTML(row?.symbol || "-")}</span></h4>
          <p>${escapeHTML(renderMarketQuoteLine(quote))}</p>
        </div>
        <div class="market-judge-action">
          <strong>${escapeHTML(marketActionLabel(row?.account_action))}</strong>
          <span>${escapeHTML(row?.stance || "-")} · ${escapeHTML(row?.horizon || "-")}</span>
        </div>
      </div>
      <div class="market-judge-meta">
        <span>confidence ${escapeHTML(fmtNum(Number(row?.confidence || 0), 2))}</span>
        <span>${escapeHTML(renderMarketPositionLine(position))}</span>
        <span>전략 ${escapeHTML(String(strategy.score ?? "-"))}</span>
      </div>
      <div class="market-judge-columns">
        <div>
          <strong>근거</strong>
          <ul>${(reasons.length ? reasons : ["근거 보강 필요"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
        </div>
        <div>
          <strong>반론</strong>
          <ul>${(risks.length ? risks : ["리스크 추가 점검"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
        </div>
        <div>
          <strong>확인</strong>
          <ul>${(triggers.length ? triggers : ["거래대금/섹터 수급 확인"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
        </div>
      </div>
      ${
        gaps.length
          ? `<div class="strategy-data-warning-strip">${gaps.map((item) => `<span class="strategy-data-chip warn">${escapeHTML(item)} 미확인</span>`).join("")}</div>`
          : ""
      }
    </article>
  `;
}

function renderMarketJudgeTab() {
  const payload = state.marketJudge.result;
  const loadingHtml = state.marketJudge.loading || state.marketJudge.running
    ? '<div class="notice">장중 판단 데이터를 불러오는 중입니다.</div>'
    : "";
  const errorHtml = state.marketJudge.error
    ? `<div class="notice">장중 판단 실패: ${escapeHTML(state.marketJudge.error)}</div>`
    : "";
  const disabled = state.marketJudge.running ? "disabled" : "";
  if (!payload) {
    return `
      <div class="market-judge-shell">
        <div class="strategy-intel-actions">
          <button class="btn primary" type="button" data-market-judge-action="refresh">최근 판단 보기</button>
          <button class="btn" type="button" data-market-judge-action="run" ${disabled}>gpt-5.5 장중 판단</button>
        </div>
        ${loadingHtml}
        ${errorHtml || '<div class="notice">아직 저장된 장중 판단이 없습니다. “gpt-5.5 장중 판단”으로 새 스냅샷을 만들 수 있습니다.</div>'}
      </div>
    `;
  }
  const judgments = Array.isArray(payload.judgments) ? payload.judgments : [];
  return `
    <div class="market-judge-shell">
      <div class="strategy-intel-actions">
        <button class="btn primary" type="button" data-market-judge-action="refresh">최근 판단 보기</button>
        <button class="btn" type="button" data-market-judge-action="run" ${disabled}>${state.marketJudge.running ? "판단 중" : "gpt-5.5 장중 판단"}</button>
      </div>
      ${loadingHtml}
      ${errorHtml}
      ${renderMarketJudgeHeader(payload)}
      <section class="market-judge-board">
        ${judgments.length ? judgments.map(renderMarketJudgmentCard).join("") : '<div class="notice">표시할 판단 종목이 없습니다.</div>'}
      </section>
      <p class="strategy-footnote">${escapeHTML(payload.disclaimer || "정보 제공용이며 매매 추천이 아닙니다.")}</p>
    </div>
  `;
}

function renderMemoryPolicyStrip(memory) {
  const policies = Array.isArray(memory?.active_policies) ? memory.active_policies : [];
  if (!policies.length) {
    return '<div class="notice">아직 활성화된 메모리 운용 원칙이 없습니다. 장전/마감 루틴이 쌓이면 이 영역이 채워집니다.</div>';
  }
  return `
    <div class="memory-policy-strip">
      ${policies.slice(0, 6).map((row) => `
        <span class="strategy-data-chip">
          ${escapeHTML(row.policy_id || row.action || "policy")}
          <small>${escapeHTML(truncateWithEllipsis(row.reason || row.action || "", 58))}</small>
        </span>
      `).join("")}
    </div>
  `;
}

function renderMemoryJournalCard(row) {
  const message = String(row?.message_md || "").trim();
  return `
    <article class="memory-journal-card">
      <div class="block-card-head">
        <div>
          <h4>${escapeHTML(row?.title || row?.slot_label || row?.slot || "메모리")}</h4>
          <p>${escapeHTML(row?.trading_day || "-")} · ${escapeHTML(row?.slot_label || row?.slot || "-")}</p>
        </div>
        <span class="block-status">${row?.sent_telegram ? "Telegram" : "저널"}</span>
      </div>
      <p class="helper-text">${escapeHTML(truncateWithEllipsis(message || "아직 내용이 없습니다.", 420))}</p>
      ${
        message.length > 420
          ? `<button class="btn tiny ghost" type="button" data-helper-detail-id="${escapeHTML(registerHelperDetail({
              title: row?.title || "메모리 저널",
              body: message,
              meta: [`${row?.trading_day || ""} · ${row?.slot || ""}`],
            }))}">전문 보기</button>`
          : ""
      }
    </article>
  `;
}

function renderInvestmentMemoryTab() {
  const memory = state.investmentMemory;
  const errorHtml = state.investmentMemoryError
    ? `<div class="notice">메모리 조회 실패: ${escapeHTML(state.investmentMemoryError)}</div>`
    : "";
  if (!memory) {
    return `${errorHtml || '<div class="notice">투자 메모리를 불러오는 중입니다.</div>'}`;
  }
  const journals = Array.isArray(memory.journals) ? memory.journals : [];
  const latest = Array.isArray(memory.latest_journals) ? memory.latest_journals : [];
  const status = memory.context_pack || {};
  const busy = state.investmentMemoryRunning ? "disabled" : "";
  return `
    <div class="memory-shell">
      <div class="strategy-intel-actions">
        <button class="btn primary" type="button" data-memory-action="refresh" ${busy}>새로고침</button>
        <button class="btn" type="button" data-memory-action="pre_open" ${busy}>장전 마음가짐</button>
        <button class="btn" type="button" data-memory-action="midday" ${busy}>장중 점검</button>
        <button class="btn" type="button" data-memory-action="post_close" ${busy}>마감 리뷰</button>
        <button class="btn warm" type="button" data-memory-action="block_reflection" ${busy}>블록 반성</button>
      </div>
      ${errorHtml}
      <section class="memory-hero">
        <div>
          <span class="section-kicker">Growing Agent Memory</span>
          <h4>메모리 기반 투자 파트너</h4>
          <p>장전 마음가짐, 장중 점검, 마감 리뷰, 블록 반성을 Markdown/DB로 축적하고 블록 트레이딩 판단 입력에 연결합니다.</p>
        </div>
        <div class="block-trader-kpis">
          <span><strong>${escapeHTML(memory.trading_day || memory.today || "-")}</strong>기준일</span>
          <span><strong>${escapeHTML(memory.active_policies?.length ?? 0)}</strong>활성 원칙</span>
          <span><strong>${escapeHTML(journals.length)}</strong>오늘 저널</span>
          <span><strong>${escapeHTML(status.status || "ok")}</strong>컨텍스트</span>
        </div>
      </section>
      <section class="memory-section">
        <div class="panel-head compact">
          <h3>오늘 적용 중인 운용 원칙</h3>
        </div>
        ${renderMemoryPolicyStrip(memory)}
      </section>
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
      </section>
      <p class="strategy-footnote">메모리는 LLM 매니저의 판단 보조 자료입니다. kill switch, 현금/보유수량 제한, 중복주문 방지는 항상 우선합니다.</p>
    </div>
  `;
}

function blockStatusLabel(value) {
  const labels = {
    proposed: "제안",
    entry_pending: "진입 대기",
    open: "운용 중",
    exit_pending: "청산 대기",
    closed: "종료",
    paused: "일시정지",
    error: "오류",
  };
  const key = String(value || "").trim();
  return labels[key] || key || "-";
}

function blockTone(value) {
  const status = String(value || "");
  if (status === "open") return "good";
  if (status === "entry_pending" || status === "exit_pending" || status === "paused") return "warn";
  if (status === "error") return "bad";
  return "neutral";
}

function renderKisBlockHero(payload) {
  const summary = payload?.summary || payload || {};
  const clock = summary.clock || {};
  const account = payload?.account || {};
  const kill = summary.kill_switch || {};
  const mode = summary.execution_mode || "-";
  const killLabel = kill.enabled ? "KILL ON" : "정상";
  return `
    <section class="block-trader-hero">
      <div>
        <span class="eyebrow">KIS BLOCK TRADER</span>
        <h4>${escapeHTML(mode)} · ${escapeHTML(clock.session || "-")}</h4>
        <p>${escapeHTML(`현금 ${fmtKRW(account.cash_krw)}원 · 보유 ${fmtKRW(account.position_value_krw)}원 · ${account.position_count || 0}종목`)}</p>
      </div>
      <div class="block-trader-kpis">
        <span><strong>${escapeHTML(summary.open_block_count ?? 0)}</strong>활성 블록</span>
        <span><strong>${escapeHTML(summary.block_count ?? 0)}</strong>전체 블록</span>
        <span><strong>${escapeHTML(summary.llm_ready ? "ready" : "off")}</strong>LLM</span>
        <span><strong>${escapeHTML(killLabel)}</strong>킬스위치</span>
      </div>
    </section>
  `;
}

function renderBlockCard(block) {
  const tone = blockTone(block.status);
  const pnl = asNumber(block.unrealized_pnl_krw, 0);
  const quote = block.quote || {};
  const target = asNumber(block.target_price, 0);
  const stop = asNumber(block.stop_price, 0);
  return `
    <article class="block-card ${escapeHTML(tone)}">
      <div class="block-card-head">
        <div>
          <h4>${escapeHTML(block.name || block.symbol || "-")} <span>${escapeHTML(block.symbol || "-")}</span></h4>
          <p class="mono">${escapeHTML(block.block_id || "-")}</p>
        </div>
        <span class="block-status">${escapeHTML(blockStatusLabel(block.status))}</span>
      </div>
      <div class="block-price-grid">
        <span><b>${escapeHTML(fmtKRW(block.qty_open || block.qty_initial))}</b>주</span>
        <span><b>${escapeHTML(fmtMaybeKRW(block.entry_price))}</b>진입</span>
        <span><b>${escapeHTML(fmtMaybeKRW(block.current_price || quote.price))}</b>현재</span>
        <span><b>${escapeHTML(fmtMaybeKRW(target))}</b>목표</span>
        <span><b>${escapeHTML(fmtMaybeKRW(stop))}</b>손절</span>
        <span class="${pnl >= 0 ? "gain" : "loss"}"><b>${escapeHTML(fmtKRW(pnl))}</b>PnL</span>
      </div>
      <p class="helper-text">${escapeHTML(block.thesis || block.llm_reason || "블록 운용 근거 대기")}</p>
      <div class="block-card-actions">
        <span class="strategy-data-chip">${escapeHTML(block.next_rule_action || "watch")}</span>
        ${block.created_by === "existing_position" ? '<span class="strategy-data-chip">기존 보유</span>' : ""}
        ${
          block.status === "open"
            ? `<button class="btn small" type="button" data-block-action="close" data-block-id="${escapeHTML(block.block_id)}">청산 요청</button>
               <button class="btn small ghost" type="button" data-block-action="pause" data-block-id="${escapeHTML(block.block_id)}">정지</button>`
            : ""
        }
        ${
          block.status === "paused"
            ? `<button class="btn small" type="button" data-block-action="resume" data-block-id="${escapeHTML(block.block_id)}">재개</button>`
            : ""
        }
      </div>
    </article>
  `;
}

function renderBlockAllocation(payload) {
  const rows = Array.isArray(payload?.allocation?.items) ? payload.allocation.items : [];
  return `
    <article class="helper-card helper-card-wide">
      <h4>계좌/블록 배정</h4>
      <div class="table-wrap compact">
        <table>
          <thead>
            <tr><th>종목</th><th>잔고</th><th>블록</th><th>미배정</th><th>초과</th></tr>
          </thead>
          <tbody>
            ${
              rows.length
                ? rows.map((row) => `
                  <tr>
                    <td>${escapeHTML(`${row.name || row.symbol} (${row.symbol})`)}</td>
                    <td>${escapeHTML(fmtKRW(row.account_qty))}</td>
                    <td>${escapeHTML(fmtKRW(row.block_qty))}</td>
                    <td>${escapeHTML(fmtKRW(row.unallocated_qty))}</td>
                    <td class="${asNumber(row.overallocated_qty, 0) > 0 ? "loss" : ""}">${escapeHTML(fmtKRW(row.overallocated_qty))}</td>
                  </tr>
                `).join("")
                : `<tr><td colspan="5">배정 데이터가 없습니다.</td></tr>`
            }
          </tbody>
        </table>
      </div>
    </article>
  `;
}

function renderBlockEventFeed(payload) {
  const events = Array.isArray(payload?.events) ? payload.events.slice(0, 8) : [];
  const orders = Array.isArray(payload?.orders) ? payload.orders.slice(0, 8) : [];
  const pendingStatuses = new Set(["sent", "partially_filled", "cancel_requested"]);
  return `
    <article class="helper-card">
      <h4>주문/이벤트</h4>
      <ul class="helper-plain-list">
        ${
          [...orders.map((row) => {
            const fillText = row.filled_qty || row.remaining_qty
              ? ` · 체결 ${fmtKRW(row.filled_qty)} / 잔여 ${fmtKRW(row.remaining_qty)}`
              : "";
            const cancelButton = pendingStatuses.has(String(row.status || ""))
              ? ` <button class="btn tiny ghost" type="button" data-block-action="cancel-order" data-order-id="${escapeHTML(row.id)}">미체결 취소</button>`
              : "";
            return `<span>${escapeHTML(`${row.side} ${row.symbol} ${row.qty}주 @ ${fmtKRW(row.limit_price)} · ${row.status}${fillText}`)}</span>${cancelButton}`;
          }),
            ...events.map((row) => escapeHTML(`${row.event_type} · ${row.message}`))]
            .slice(0, 10)
            .map((item) => `<li>${item}</li>`)
            .join("") || "<li>이벤트 없음</li>"
        }
      </ul>
    </article>
  `;
}

function renderBlockManagerRun(payload) {
  const run = payload?.latest_manager_run || {};
  const actions = run.actions || {};
  const count = ["create_blocks", "update_blocks", "close_blocks", "pause_blocks"]
    .reduce((acc, key) => acc + (Array.isArray(actions[key]) ? actions[key].length : 0), 0);
  return `
    <article class="helper-card">
      <h4>LLM 매니저</h4>
      <p class="helper-text">최근 실행 ${escapeHTML(run.run_at ? fmtKST(run.run_at, true) : "--")} · ${escapeHTML(run.status || "missing")} · 액션 ${escapeHTML(count)}</p>
      <pre class="helper-json mono">${escapeHTML(stringifySafe(actions, true))}</pre>
    </article>
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
  const blocks = Array.isArray(payload.blocks) ? payload.blocks : [];
  const killEnabled = Boolean(payload.summary?.kill_switch?.enabled);
  return `
    <div class="block-trader-shell">
      <div class="strategy-intel-actions">
        <button class="btn primary" type="button" data-block-action="refresh">새로고침</button>
        <button class="btn warm" type="button" data-block-action="adopt">쥬 기존 보유분 블록화</button>
        <button class="btn" type="button" data-block-action="manager">LLM 매니저 1회</button>
        <button class="btn" type="button" data-block-action="tick">룰엔진 tick</button>
        <button class="btn ${killEnabled ? "" : "danger"}" type="button" data-block-action="${killEnabled ? "kill-release" : "kill"}">${killEnabled ? "킬스위치 해제" : "킬스위치"}</button>
      </div>
      ${errorHtml}
      <section class="memory-section">
        <div class="panel-head compact">
          <h3>오늘 적용 중인 메모리 정책</h3>
        </div>
        ${renderMemoryPolicyStrip(state.investmentMemory)}
      </section>
      ${renderKisBlockHero(payload)}
      <section class="block-board">
        ${blocks.length ? blocks.map(renderBlockCard).join("") : '<div class="notice">아직 등록된 블록이 없습니다. LLM 매니저 실행 후 블록 제안을 확인하세요.</div>'}
      </section>
      <div class="helper-grid">
        ${renderBlockAllocation(payload)}
        ${renderBlockEventFeed(payload)}
        ${renderBlockManagerRun(payload)}
      </div>
      <p class="strategy-footnote">블록 트레이딩은 독립 블록 단위 관리 도구입니다. 실주문은 별도 실행 플래그가 켜져야 동작합니다.</p>
    </div>
  `;
}

function renderRuntimeHelperTab() {
  const health = state.healthStatus;
  const healthError = state.healthError;
  const reports = state.reportsStatus;
  const runtime = state.dashboard?.runtime || {};
  const storage = state.runtimeStorage || {};
  const cleanupCandidates = storage.cleanup_candidates || {};
  const unrefPdfs = cleanupCandidates.unreferenced_report_pdfs || {};
  const venues = Array.isArray(state.dashboard?.venues) ? state.dashboard.venues : [];

  const systemRows = [
    { label: "API 상태", value: health?.status || (healthError ? "offline" : "-") },
    { label: "Runtime 데이터 상태", value: health?.runtime_status || "-" },
    { label: "Runtime 역할", value: runtime.role_label || health?.runtime_role || "-" },
    {
      label: "Runtime 실행",
      value: `${runtime.execution_mode || health?.runtime_execution_mode || "-"} · orders ${
        runtime.executes_orders || health?.runtime_executes_orders ? "on" : "off"
      }`,
    },
    { label: "Research 데이터 상태", value: health?.research_status || "-" },
    { label: "Naver Reports", value: health?.naver_reports_enabled ? "enabled" : "disabled" },
    { label: "Block Trader", value: `${health?.kis_block_trader_execution_mode || "-"} · ${health?.kis_block_trader_enabled ? "enabled" : "disabled"}` },
  ];

  const venueRows = venues.length
    ? venues.map((row) => {
        const assetCount = Array.isArray(row.assets) ? row.assets.length : 0;
        return {
          label: row.label || row.id || "venue",
          value: `connected · ${assetCount}개 · ${fmtKRW(Number(row.total_krw || 0))} KRW`,
        };
      })
    : [{ label: "거래소 자산", value: "missing" }];

  const runnerProcesses = health?.runner_processes || {};
  const runnerLabel = (key, fallback) => {
    const row = runnerProcesses[key] || {};
    const status = String(row.status || "").trim().toLowerCase();
    const pid = Number(row.pid || 0);
    const pidFilePid = Number(row.pid_file_pid || 0);
    if (status === "covered") {
      return `covered · ${row.covered_by_label || row.covered_by || "supervisor"}`;
    }
    if (status === "running" || row.direct_alive === true || row.alive === true) {
      return pid > 0 ? `running · pid ${pid}` : "running";
    }
    if (row.pid_file_status === "stale") {
      return pidFilePid > 0 ? `stale pid · ${pidFilePid}` : "stale pid";
    }
    if (row.pid_file_status === "mismatch") {
      return pidFilePid > 0 ? `pid mismatch · ${pidFilePid}` : "pid mismatch";
    }
    if (fallback === true) {
      return "running";
    }
    if (fallback === false) {
      return "stopped";
    }
    return status || "unknown";
  };
  const runnerRows = [
    { label: "control API", value: runnerLabel("control", health?.status === "ok") },
    { label: "runtime runner", value: runnerLabel("runtime", health?.runtime_runner_alive) },
    { label: "intelligence 통합 runner", value: runnerLabel("intelligence", health?.intelligence_runner_alive) },
    { label: "research 전용 runner", value: runnerLabel("research", health?.research_runner_alive) },
    { label: "reports 전용 crawler", value: runnerLabel("naver_reports", health?.naver_reports_runner_alive) },
    { label: "전략 시그널 runner", value: runnerLabel("strategy_insights", health?.strategy_insight_runner_alive) },
    { label: "Block trader runner", value: runnerLabel("kis_block_trader", health?.kis_block_trader_runner_alive) },
  ];

  const reportTotal = Number(reports?.repository?.total_reports || 0);
  const learningTotalCount = normalizeNonNegativeInt(state.dashboard?.research?.learning_total_count);
  const llmFacts = reports?.intelligence?.llm_facts || {};
  const llmBridge = reports?.intelligence?.llm_bridge || {};
  const fundamentals = reports?.fundamentals || {};
  const reportUpdated = reports?.repository?.last_updated_at
    ? fmtKST(reports.repository.last_updated_at, true)
    : "--";
  const fundamentalsUpdated = fundamentals.latest_crawled_at
    ? fmtKST(fundamentals.latest_crawled_at, true)
    : "--";
  const reportQuality = reports?.repository?.quality || {};
  const ragAvailable = reports?.rag?.available ? "available" : "unavailable";
  const ragCount = Number(reports?.rag?.count || 0);
  const llmFactsLabel = llmFacts.active ? "active" : llmFacts.enabled ? "waiting" : "off";
  const dataRows = [
    { label: "reports db", value: String(reportTotal) },
    { label: "reports updated", value: reportUpdated },
    { label: "fundamentals db", value: String(fundamentals.total_snapshots || 0) },
    { label: "fundamentals symbols", value: String(fundamentals.total_symbols || 0) },
    { label: "fundamentals ok/stale", value: `${String(fundamentals.ok_symbol_count || 0)} / ${String(fundamentals.stale_symbol_count || 0)}` },
    { label: "fundamentals updated", value: fundamentalsUpdated },
    { label: "fundamentals errors", value: String(fundamentals.error_count || 0) },
    { label: "report identity suspect", value: String(reportQuality.identity_suspect_count || 0) },
    { label: "symbol drift", value: String(reportQuality.symbol_directory_drift_count || 0) },
    { label: "rag status", value: ragAvailable },
    { label: "rag chunks", value: String(ragCount) },
    { label: "llm facts", value: `${llmFactsLabel} · ${llmBridge.mode || "none"}` },
    { label: "누적 학습 횟수", value: learningTotalCount === null ? "-" : String(learningTotalCount) },
    { label: "runtime cycle", value: runtime.cycle === undefined ? "-" : String(runtime.cycle) },
    { label: "runtime sessions", value: runtime.sessions === undefined ? "-" : String(runtime.sessions) },
    { label: ".runtime size", value: storage.total_bytes === undefined ? "-" : fmtBytes(storage.total_bytes) },
    {
      label: "unref PDFs",
      value:
        unrefPdfs.count === undefined
          ? "-"
          : `${unrefPdfs.count}개 · ${fmtBytes(unrefPdfs.bytes)}`,
    },
  ];

  const renderRows = (rows) =>
    rows
      .map((row) => {
        const chip = helperStateChip(row.value);
        return `
          <li>
            <span>${escapeHTML(row.label)}</span>
            <strong class="helper-runtime-chip ${chip.cls}">${escapeHTML(chip.text)}</strong>
          </li>
        `;
      })
      .join("");

  return `
    <div class="helper-grid helper-runtime-grid">
      <article class="helper-card">
        <h4>시스템 상태</h4>
        <ul class="helper-runtime-list">
          ${renderRows(systemRows)}
        </ul>
      </article>
      <article class="helper-card">
        <h4>거래소 자산 연동</h4>
        <ul class="helper-runtime-list">
          ${renderRows(venueRows)}
        </ul>
      </article>
      <article class="helper-card">
        <h4>러너 상태</h4>
        <ul class="helper-runtime-list">
          ${renderRows(runnerRows)}
        </ul>
      </article>
      <article class="helper-card">
        <h4>리포트/RAG</h4>
        <ul class="helper-runtime-list">
          ${renderRows(dataRows)}
        </ul>
      </article>
    </div>
  `;
}

function renderRebalanceHelperTab(payload, errorMessage) {
  if (errorMessage) {
    return `<div class="notice">리밸런싱 상태 조회 실패: ${escapeHTML(errorMessage)}</div>`;
  }
  if (!payload || typeof payload !== "object") {
    return '<div class="notice">리밸런싱 상태를 불러오는 중입니다.</div>';
  }

  const target = payload.target || {};
  const current = payload.current || {};
  const execution = payload.execution || {};
  const strategyConfig = payload.strategy_config || {};
  const strategyShowConfig = strategyConfig.show_config || {};
  const strategyOverride = strategyConfig.override || {};
  const targetRows = Array.isArray(target.rows) ? target.rows : [];
  const currentRows = Array.isArray(current.rows) ? current.rows : [];
  const currentRowsForTable = (() => {
    const rows = [...currentRows];
    const cashIndex = rows.findIndex((row) => String(row?.ticker || "").toUpperCase() === "KRW");
    if (cashIndex > 0) {
      const [cashRow] = rows.splice(cashIndex, 1);
      rows.unshift(cashRow);
    }
    return rows;
  })();
  const openPairs = Array.isArray(execution.open_pairs) ? execution.open_pairs : [];
  const codeNameMap = new Map();
  [...targetRows, ...currentRows].forEach((row) => {
    const ticker = String(row?.ticker || "").trim();
    const name = String(row?.name || "").trim();
    if (/^\d{6}$/.test(ticker) && name && name !== ticker) {
      codeNameMap.set(ticker, name);
    }
  });
  const targetInvested = asNumber(target.target_invested_ratio, 0);
  const actualInvested = asNumber(execution.actual_invested_ratio, 0);
  const investedGap = actualInvested - targetInvested;

  const formatSymbolLabel = (row) => {
    const ticker = String(row?.ticker || "-").trim();
    const name = String(row?.name || "").trim();
    if (/^\d{6}$/.test(ticker)) {
      const resolvedName = name && name !== ticker ? name : codeNameMap.get(ticker) || "미상종목";
      return `${resolvedName} (${ticker})`;
    }
    if (ticker.toUpperCase() === "KRW") {
      return "현금 (KRW)";
    }
    if (name && name !== ticker) {
      return `${name} (${ticker})`;
    }
    return ticker;
  };

  const openPairLabels = openPairs.map((pair) => {
    const text = String(pair || "").trim();
    const ticker = text.split("/")[0].trim();
    if (/^\d{6}$/.test(ticker)) {
      return `${codeNameMap.get(ticker) || "미상종목"} (${ticker})`;
    }
    if (ticker.toUpperCase() === "KRW") {
      return "현금 (KRW)";
    }
    return text;
  });

  const headRows = [
    { label: "타깃 업데이트", value: target.updated_at ? fmtKST(target.updated_at, true) : "--" },
    { label: "현금 비중(목표)", value: `${fmtNum(asNumber(target.target_cash_weight, 0) * 100, 1)}%` },
    { label: "투자 비중(목표)", value: `${fmtNum(targetInvested * 100, 1)}%` },
    { label: "타깃 종목 수", value: String(targetRows.length) },
  ];

  const execRows = [
    { label: "오픈 트레이드", value: String(asNumber(execution.open_trade_count, 0)) },
    { label: "투자 비중(실제)", value: `${fmtNum(actualInvested * 100, 1)}%` },
    { label: "목표 대비 편차", value: `${fmtNum(investedGap * 100, 1)}%p` },
    { label: "오픈 스테이크", value: `${fmtKRW(asNumber(execution.open_stake_total_krw, 0))} KRW` },
    { label: "기준 총자산", value: `${fmtKRW(asNumber(execution.total_value_krw, 0))} KRW` },
  ];

  const strategyRows = [
    { label: "API 연결", value: strategyConfig.api_connected ? "connected" : "disconnected" },
    { label: "봇 상태", value: String(strategyShowConfig.state || "-") },
    { label: "전략", value: String(strategyShowConfig.strategy || "-") },
    { label: "타임프레임", value: String(strategyShowConfig.timeframe || "-") },
    { label: "거래 모드", value: String(strategyShowConfig.trading_mode || "-") },
    { label: "최대 오픈 트레이드", value: String(strategyShowConfig.max_open_trades ?? "-") },
    {
      label: "스테이크",
      value: `${String(strategyShowConfig.stake_amount || "-")} ${String(strategyShowConfig.stake_currency || "")}`.trim(),
    },
    {
      label: "강제진입 허용",
      value: strategyShowConfig.force_entry_enable ? "enabled" : "disabled",
    },
    {
      label: "리밸런싱 타깃 시각",
      value: strategyOverride.target_weights_updated_at
        ? fmtKST(strategyOverride.target_weights_updated_at, true)
        : "--",
    },
    {
      label: "타깃 종목 수",
      value: String(strategyOverride.pair_whitelist_count ?? "-"),
    },
    {
      label: "목표 현금 비중",
      value: `${fmtNum(asNumber(strategyOverride.target_cash_weight, 0) * 100, 1)}%`,
    },
  ];

  const targetTable = targetRows.length
    ? `
      <div class="target-table-wrap">
        <table class="target-table">
          <thead>
            <tr>
              <th>종목</th>
              <th>목표 비중</th>
            </tr>
          </thead>
          <tbody>
            ${targetRows
              .slice(0, 12)
              .map(
                (row) => `
                  <tr>
                    <td>${escapeHTML(formatSymbolLabel(row))}</td>
                    <td>${escapeHTML(fmtNum(asNumber(row.weight, 0) * 100, 2))}%</td>
                  </tr>
                `
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `
    : '<div class="notice">리밸런싱 타깃 데이터가 없습니다.</div>';

  const currentTable = currentRowsForTable.length
    ? `
      <div class="target-table-wrap">
        <table class="target-table">
          <thead>
            <tr>
              <th>종목</th>
              <th>현재 비중</th>
            </tr>
          </thead>
          <tbody>
            ${currentRowsForTable
              .slice(0, 12)
              .map(
                (row) => `
                  <tr>
                    <td>${escapeHTML(formatSymbolLabel(row))}</td>
                    <td>${escapeHTML(fmtNum(asNumber(row.weight, 0) * 100, 2))}%</td>
                  </tr>
                `
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `
    : '<div class="notice">현재 비중 데이터가 없습니다.</div>';

  const renderRows = (rows) =>
    rows
      .map((row) => {
        const chip = helperStateChip(row.value);
        return `
          <li>
            <span>${escapeHTML(row.label)}</span>
            <strong class="helper-runtime-chip ${chip.cls}">${escapeHTML(row.value)}</strong>
          </li>
        `;
      })
      .join("");

  return `
    <div class="helper-grid helper-runtime-grid">
      <article class="helper-card">
        <h4>리밸런싱 타깃</h4>
        <ul class="helper-runtime-list">
          ${renderRows(headRows)}
        </ul>
      </article>
      <article class="helper-card">
        <h4>실행 상태</h4>
        <ul class="helper-runtime-list">
          ${renderRows(execRows)}
        </ul>
      </article>
      <article class="helper-card">
        <h4>자동매매 전략 설정</h4>
        <ul class="helper-runtime-list">
          ${renderRows(strategyRows)}
        </ul>
      </article>
      <article class="helper-card helper-card-wide">
        <h4>목표 비중 테이블</h4>
        ${targetTable}
      </article>
      <article class="helper-card helper-card-wide">
        <h4>현재 비중 테이블</h4>
        ${currentTable}
      </article>
      <article class="helper-card helper-card-wide">
        <h4>현재 오픈 포지션 종목</h4>
        <p class="helper-text mono">${escapeHTML(openPairLabels.length ? openPairLabels.join(", ") : "-")}</p>
      </article>
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

function renderHelperAgent() {
  const tabsRoot = qs("helperTabs");
  const contentRoot = qs("helperContent");
  const updatedRoot = qs("helperUpdatedAt");
  const scoreRoot = qs("helperScorePill");
  if (!tabsRoot || !contentRoot || !updatedRoot || !scoreRoot) return;

  const validTabs = new Set(["research", "strategy_intel", "memory", "market_judge", "ask", "runtime", "rebalance", "kis_trader", "reports"]);
  if (!validTabs.has(state.activeHelperTab)) {
    state.activeHelperTab = "ask";
  }
  state.helperDetailRegistry = {};
  state.helperDetailSeq = 0;

  tabsRoot.querySelectorAll("[data-helper-tab]").forEach((button) => {
    const active = button.dataset.helperTab === state.activeHelperTab;
    button.classList.toggle("active", active);
  });

  let updatedAt = state.dashboard?.clock_utc || "";
  let contentHtml = "";
  const score = normalizeScore100(state.dashboard?.research?.agent_self_score_100);
  scoreRoot.textContent = score === null ? "현재 역량 --/100" : `현재 역량 ${score}/100`;
  if (state.activeHelperTab === "runtime") {
    contentHtml = renderRuntimeHelperTab();
    updatedAt = pickUpdatedAt(state.healthStatus) || pickUpdatedAt(state.reportsStatus) || updatedAt;
  } else if (state.activeHelperTab === "strategy_intel") {
    contentHtml = renderStrategyIntelTab();
    updatedAt = pickUpdatedAt(state.strategyIntel.result) || updatedAt;
  } else if (state.activeHelperTab === "memory") {
    contentHtml = renderInvestmentMemoryTab();
    updatedAt = pickUpdatedAt(state.investmentMemory) || updatedAt;
  } else if (state.activeHelperTab === "market_judge") {
    contentHtml = renderMarketJudgeTab();
    updatedAt = pickUpdatedAt(state.marketJudge.result?.run) || pickUpdatedAt(state.marketJudge.result) || updatedAt;
  } else if (state.activeHelperTab === "ask") {
    contentHtml = renderAskHelperTab();
    updatedAt = pickUpdatedAt(state.helperAsk.result) || updatedAt;
  } else if (state.activeHelperTab === "rebalance") {
    contentHtml = renderRebalanceHelperTab(state.rebalanceStatus, state.rebalanceError);
    updatedAt = pickUpdatedAt(state.rebalanceStatus) || updatedAt;
  } else if (state.activeHelperTab === "kis_trader") {
    contentHtml = renderKisBlockTradingTab();
    updatedAt = pickUpdatedAt(state.kisBlockStatus) || updatedAt;
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
    memory: "메모리",
    market_judge: "장중 판단",
    ask: "AI 질문",
    runtime: "운영/데이터",
    rebalance: "리밸런싱",
    kis_trader: "블록 트레이딩",
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
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "request failed");
  }
  return data;
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
  } catch (error) {
    state.marketJudge.error = getErrorMessage(error);
  } finally {
    state.marketJudge.loading = false;
    state.marketJudge.running = false;
    renderHelperAgent();
  }
}

async function loadKisBlocks() {
  state.kisBlockError = "";
  renderHelperAgent();
  try {
    state.kisBlockStatus = await getJSON("/kis/blocks");
  } catch (error) {
    state.kisBlockError = getErrorMessage(error);
  } finally {
    renderHelperAgent();
  }
}

async function loadInvestmentMemory() {
  state.investmentMemoryError = "";
  renderHelperAgent();
  try {
    state.investmentMemory = await getJSON("/memory/today");
  } catch (error) {
    state.investmentMemory = null;
    state.investmentMemoryError = getErrorMessage(error);
  } finally {
    renderHelperAgent();
  }
}

async function runInvestmentMemoryAction(action) {
  state.investmentMemoryError = "";
  state.investmentMemoryRunning = action !== "refresh";
  renderHelperAgent();
  try {
    if (action === "refresh") {
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
  renderTopMetrics();
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

async function refreshDashboard() {
  const [
    dashboardResult,
    kisBlockResult,
    reportsResult,
    healthResult,
    rebalanceResult,
    storageResult,
    memoryResult,
  ] = await Promise.allSettled([
    getJSON("/dashboard"),
    getJSON("/kis/blocks"),
    getJSON("/reports/status"),
    getJSON("/health"),
    getJSON("/rebalance/kis-status"),
    getJSON("/runtime/storage"),
    getJSON("/memory/today"),
  ]);

  if (dashboardResult.status !== "fulfilled") {
    throw dashboardResult.reason;
  }

  state.dashboard = dashboardResult.value;
  if (kisBlockResult.status === "fulfilled") {
    state.kisBlockStatus = kisBlockResult.value;
    state.kisBlockError = "";
  } else {
    state.kisBlockStatus = null;
    state.kisBlockError = getErrorMessage(kisBlockResult.reason);
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

  if (rebalanceResult.status === "fulfilled") {
    state.rebalanceStatus = rebalanceResult.value;
    state.rebalanceError = "";
  } else {
    state.rebalanceStatus = null;
    state.rebalanceError = getErrorMessage(rebalanceResult.reason);
  }

  if (storageResult.status === "fulfilled") {
    state.runtimeStorage = storageResult.value;
    state.runtimeStorageError = "";
  } else {
    state.runtimeStorage = null;
    state.runtimeStorageError = getErrorMessage(storageResult.reason);
  }

  if (memoryResult.status === "fulfilled") {
    state.investmentMemory = memoryResult.value;
    state.investmentMemoryError = "";
  } else {
    state.investmentMemory = null;
    state.investmentMemoryError = getErrorMessage(memoryResult.reason);
  }

  renderDashboard();
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

function selectedBacktestSessionIds() {
  const checks = [...document.querySelectorAll(".bt-session-check:checked")];
  return checks.map((item) => String(item.value || "").trim()).filter(Boolean);
}

function renderBacktestCurve(curve) {
  const line = qs("btCurveLine");
  if (!line) return;
  const rows = Array.isArray(curve) ? curve : [];
  if (rows.length < 2) {
    line.setAttribute("points", "");
    return;
  }

  const width = 1000;
  const height = 260;
  const values = rows.map((row) => asNumber(row.net_pnl_krw, 0));
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const span = max - min;
  const points = rows
    .map((row, idx) => {
      const x = (idx / (rows.length - 1)) * width;
      const y = height - ((asNumber(row.net_pnl_krw, 0) - min) / span) * (height - 12) - 6;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  line.setAttribute("points", points);

  const last = asNumber(values[values.length - 1], 0);
  line.style.stroke = last >= 0 ? "var(--status-ok)" : "var(--status-bad)";
}

function renderBacktestStatus(payload) {
  state.backtest.status = payload || {};
  const job = state.backtest.status.job || {};
  const progress = state.backtest.status.progress || {};
  const aggregate = progress.aggregate || {};
  const curve = progress.equity_curve || [];

  const statusEl = qs("btStatusText");
  if (statusEl) {
    statusEl.textContent = job.status || "idle";
  }
  const total = asNumber(progress.total_cycles, 0);
  const done = asNumber(progress.cycle, 0);
  const pct = asNumber(progress.progress_pct, 0);
  const progressText = qs("btProgressText");
  if (progressText) {
    progressText.textContent = `${done} / ${total} (${fmtNum(pct, 2)}%)`;
  }
  const bar = qs("btProgressBar");
  if (bar) {
    bar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  }

  const net = asNumber(aggregate.net_pnl_krw, 0);
  const realized = asNumber(aggregate.realized_pnl_krw, 0);
  const unrealized = asNumber(aggregate.unrealized_pnl_krw, 0);
  const fees = asNumber(aggregate.fees_krw, 0);

  qs("btNetPnl").textContent = `${fmtKRW(net)} KRW`;
  qs("btRealized").textContent = `${fmtKRW(realized)} KRW`;
  qs("btUnrealized").textContent = `${fmtKRW(unrealized)} KRW`;
  qs("btFees").textContent = `${fmtKRW(fees)} KRW`;
  qs("btNetPnl").className = net >= 0 ? "gain" : "loss";

  renderBacktestCurve(curve);

  const rows = progress.sessions || [];
  qs("btSessionBody").innerHTML = rows
    .map((row) => {
      const netPnl = asNumber(row.net_pnl_krw, 0);
      return `
      <tr>
        <td>${escapeHTML(row.session_id || "-")}</td>
        <td>${escapeHTML(row.symbol || "-")}</td>
        <td>${escapeHTML(row.signals ?? 0)}</td>
        <td>${escapeHTML(row.fills ?? 0)}</td>
        <td>${escapeHTML(row.trades ?? 0)}</td>
        <td class="${netPnl >= 0 ? "gain" : "loss"}">${escapeHTML(fmtKRW(netPnl))}</td>
      </tr>
    `;
    })
    .join("");

  const scenario = job.scenario || "-";
  const source = job.session_source || "-";
  const updated = progress.updated_at ? fmtKST(progress.updated_at, true) : "--";
  qs("btMeta").textContent = `scenario=${scenario} | session_source=${source} | updated=${updated}`;
}

async function refreshBacktestStatus() {
  const payload = await getJSON("/backtest/status");
  renderBacktestStatus(payload);

  const status = String(payload?.job?.status || "");
  if (status === "running") {
    if (!state.backtest.pollTimer) {
      state.backtest.pollTimer = setInterval(async () => {
        try {
          const next = await getJSON("/backtest/status");
          renderBacktestStatus(next);
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

function renderBacktestScenarios(rows) {
  const select = qs("btScenario");
  if (!select) return;
  const items = Array.isArray(rows) ? rows : [];
  state.backtest.scenarios = items;
  select.innerHTML = items
    .map((row) => {
      const key = String(row.key || "");
      const label = String(row.label || key || "-");
      const desc = String(row.description || "");
      return `<option value="${escapeHTML(key)}">${escapeHTML(`${label} - ${desc}`)}</option>`;
    })
    .join("");
  if (!items.length) {
    select.innerHTML = `<option value="baseline">baseline</option>`;
  }
}

async function loadBacktestScenarios() {
  const payload = await getJSON("/backtest/scenarios");
  renderBacktestScenarios(payload.scenarios || []);
}

async function loadBacktestDataStatus() {
  const payload = await getJSON("/backtest/data-status");
  state.backtest.dataStatus = payload;
  const statusText = `data cache: ${payload.symbol_count || 0} symbols`;
  qs("btDataStatus").textContent = statusText;
}

async function startBacktestFromUI() {
  const payload = {
    scenario: qs("btScenario").value || "baseline",
    cycles: asNumber(qs("btCycles").value, 720),
    step_sec: asNumber(qs("btStepSec").value, 60),
    speed: asNumber(qs("btSpeed").value, 120),
    fee_rate: asNumber(qs("btFeeRate").value, 0.0005),
    slippage_bps: asNumber(qs("btSlippage").value, 1),
    session_ids: selectedBacktestSessionIds(),
  };
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
  applyTheme(getInitialTheme());
  qs("themeToggle").addEventListener("click", toggleTheme);
  const mainNavBtn = qs("mainNavBtn");
  if (mainNavBtn) {
    mainNavBtn.addEventListener("click", openMainPage);
  }
  qs("helperNavBtn").addEventListener("click", () => {
    openHelperPage("ask");
  });
  document.querySelectorAll("[data-nav-helper-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      openHelperPage(String(button.dataset.navHelperTab || "ask"));
      ensureHelperTabData();
    });
  });
  qs("helperBackBtn").addEventListener("click", openMainPage);
  qs("refreshBtn").addEventListener("click", async () => {
    await refreshDashboard();
  });
  qs("venueTabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-venue]");
    if (!button || !state.dashboard) return;
    state.activeVenueId = button.dataset.venue;
    renderDashboard();
  });
  qs("helperTabs").addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const button = target ? target.closest("[data-helper-tab]") : null;
    if (!button) return;
    state.activeHelperTab = String(button.dataset.helperTab || "ask");
    state.helperDetailModal = null;
    renderHelperAgent();
    ensureHelperTabData();
  });
  qs("helperContent").addEventListener("input", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.id === "helperAskQuery") {
      state.helperAsk.query = target.value;
    } else if (target.id === "helperAskSymbol") {
      state.helperAsk.symbol = target.value.replace(/\D/g, "").slice(0, 6);
      target.value = state.helperAsk.symbol;
    } else if (target.id === "strategyIntelQuery") {
      state.strategyIntel.query = target.value;
    }
  });
  qs("helperContent").addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
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
    const marketJudgeAction = target ? target.closest("[data-market-judge-action]") : null;
    if (marketJudgeAction) {
      loadMarketJudge(marketJudgeAction.dataset.marketJudgeAction === "run");
      return;
    }
    const memoryAction = target ? target.closest("[data-memory-action]") : null;
    if (memoryAction) {
      runInvestmentMemoryAction(String(memoryAction.dataset.memoryAction || "refresh"));
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
    state.activeHelperTab = "ask";
    renderHelperAgent();
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
  qs("helperContent").addEventListener("submit", async (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.id === "strategyIntelForm") {
      event.preventDefault();
      state.strategyIntel.query = String(qs("strategyIntelQuery")?.value || "").trim();
      await loadStrategyIntel(false);
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
    if (event.key !== "Escape" || !state.helperDetailModal) return;
    state.helperDetailModal = null;
    renderHelperAgent();
  });

  await loadTelegramStatus();
  renderPageMode();
  await refreshDashboard();
}

init();
