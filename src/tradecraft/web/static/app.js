const API = "/api";
const THEME_KEY = "hermes_theme";
const state = {
  dashboard: null,
  activeVenueId: "all",
  theme: "light",
  view: "dashboard",
  backtest: {
    status: null,
    scenarios: [],
    selectedSessionIds: [],
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

function getInitialTheme() {
  const saved = window.localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
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
  pill.style.color = ok ? "#0a9c4b" : "#c0372b";
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

function renderDashboard() {
  renderTopMetrics();
  renderVenueTabs();
  renderActiveVenue();
  renderSessions(getActiveSessions(), state.dashboard?.clock_utc);
  renderBacktestSessionOptions();

  const telegramFeed = state.dashboard?.telegram?.last_webhook_message
    ? [{ type: "telegram", message: `Webhook: ${state.dashboard.telegram.last_webhook_message}` }]
    : [];
  renderEvents([...(state.dashboard?.events || []), ...telegramFeed]);
}

async function refreshDashboard() {
  state.dashboard = await getJSON("/dashboard");
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
  line.style.stroke = last >= 0 ? "var(--gain)" : "var(--loss)";
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
  bindEvent("themeToggle", "click", toggleTheme);
  bindEvent("refreshBtn", "click", refreshDashboard);
  bindEvent("viewDashboardBtn", "click", () => setActiveView("dashboard"));
  bindEvent("viewBacktestBtn", "click", () => setActiveView("backtest"));
  bindEvent("venueTabs", "click", (event) => {
    const button = event.target.closest("[data-venue]");
    if (!button || !state.dashboard) return;
    state.activeVenueId = button.dataset.venue;
    renderDashboard();
  });
  bindEvent("btSessionList", "change", () => {
    state.backtest.selectedSessionIds = selectedBacktestSessionIds();
  });
  bindEvent("btRefreshBtn", "click", async () => {
    await refreshBacktestStatus();
    await loadBacktestDataStatus();
  });
  bindEvent("btStartBtn", "click", async () => {
    try {
      await startBacktestFromUI();
    } catch (error) {
      alert(error.message || "backtest start failed");
    }
  });
  bindEvent("btStopBtn", "click", async () => {
    try {
      await stopBacktestFromUI();
    } catch (error) {
      alert(error.message || "backtest stop failed");
    }
  });
  setActiveView("dashboard");

  await checkHealth();
  await loadTelegramStatus();
  await refreshDashboard();
  await loadBacktestScenarios();
  await refreshBacktestStatus();
  await loadBacktestDataStatus();
}

init();
