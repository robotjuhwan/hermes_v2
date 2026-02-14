const API = "/api";
const THEME_KEY = "hermes_theme";
const state = {
  dashboard: null,
  activeVenueId: "all",
  theme: "light",
};

function qs(id) {
  return document.getElementById(id);
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

async function init() {
  applyTheme(getInitialTheme());
  qs("themeToggle").addEventListener("click", toggleTheme);
  qs("refreshBtn").addEventListener("click", refreshDashboard);
  qs("venueTabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-venue]");
    if (!button || !state.dashboard) return;
    state.activeVenueId = button.dataset.venue;
    renderDashboard();
  });

  await checkHealth();
  await loadTelegramStatus();
  await refreshDashboard();
}

init();
