const API = "/api";
const THEME_KEY = "hermes_theme";
const state = {
  dashboard: null,
  strategyControl: null,
  activeVenueId: "all",
  activePage: "main",
  activeHelperTab: "research",
  kisTraderStatus: null,
  kisTraderError: "",
  reportsStatus: null,
  reportsError: "",
  healthStatus: null,
  healthError: "",
  theme: "light",
  lastRenderedWebhookMessage: "",
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
  if (source.includes("naver_report_db")) return "리포트 DB";
  if (source.includes("report_crawl")) return "직접 수집";
  return truncateWithEllipsis(source, 28);
}

function helperStateChip(value) {
  const text = String(value ?? "").trim() || "-";
  const lower = text.toLowerCase();
  const okTokens = ["ok", "online", "running", "connected", "ready", "up", "enabled", "true"];
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

function renderPageMode() {
  const isHelper = state.activePage === "helper";
  const mainIds = [
    "mainMetricsSection",
    "mainBalanceSection",
    "mainLayoutSection",
    "mainStrategySection",
    "mainTelegramSection",
  ];
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

  const helperNavBtn = qs("helperNavBtn");
  if (helperNavBtn) {
    helperNavBtn.hidden = isHelper;
  }
}

function openHelperPage(tab = "research") {
  state.activePage = "helper";
  state.activeHelperTab = tab;
  renderPageMode();
  renderHelperAgent();
  const section = qs("helperAgentSection");
  if (section) {
    section.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function openMainPage() {
  state.activePage = "main";
  renderPageMode();
  window.scrollTo({ top: 0, behavior: "smooth" });
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
  if (!rows.length) {
    return '<div class="notice">리서치 요약 데이터가 없습니다.</div>';
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
  const overview = `전체 ${rows.length}건 · 누적 학습 ${learningTotalCount === null ? "--" : learningTotalCount}회 · 점수 ${score === null ? "--" : score}/100`;

  const sections = [...grouped.entries()].map(([groupLabel, entries]) => {
    const itemsHtml = entries
      .map((row, index) => {
        const title = truncateWithEllipsis(
          row.title || row.name || row.symbol || row.code || row.topic || `리서치 ${index + 1}`,
          84
        );
        const summaryText = truncateWithEllipsis(
          row.summary || row.thesis || row.note || row.reason || row.description || row.content || "요약 정보 없음",
          220
        );
        const status = String(row.status || "ok").toLowerCase();
        const statusLabel = status === "ok" ? "OK" : truncateWithEllipsis(status.toUpperCase(), 18);
        const statusClass = status === "ok" ? "ok" : "warn";
        const picks = Array.isArray(row.picks)
          ? row.picks
              .map((code) => String(code || "").trim())
              .filter((code) => code)
              .slice(0, 4)
          : [];
        const picksText = picks.length ? `후보: ${picks.join(", ")}` : "후보: 없음";
        const queryText = truncateWithEllipsis(row.query || research?.query || "일반", 34);
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
            </div>
          </li>
        `;
      })
      .join("");

    return `
      <section class="helper-group-section">
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

function renderRuntimeHelperTab() {
  const health = state.healthStatus;
  const healthError = state.healthError;
  const reports = state.reportsStatus;
  const bots = Array.isArray(state.strategyControl?.items) ? state.strategyControl.items : [];

  const systemRows = [
    { label: "API 상태", value: health?.status || (healthError ? "offline" : "-") },
    { label: "Runtime 데이터 상태", value: health?.runtime_status || "-" },
    { label: "Research 데이터 상태", value: health?.research_status || "-" },
    { label: "Naver Reports", value: health?.naver_reports_enabled ? "enabled" : "disabled" },
    { label: "KIS Trader", value: health?.kis_trader_enabled ? "enabled" : "disabled" },
  ];

  const botRows = bots.length
    ? bots.map((row) => ({
        label: row.label || row.bot_id || "bot",
        value: `${row.running ? "RUNNING" : "STOPPED"} · ${row.api_reachable ? "API UP" : "API DOWN"}`,
      }))
    : [{ label: "Freqtrade", value: "no data" }];

  const runnerLabel = (value) => {
    if (value === true) {
      return "running";
    }
    if (value === false) {
      return "stopped";
    }
    return "unknown";
  };
  const runnerRows = [
    { label: "runtime runner 프로세스", value: runnerLabel(health?.runtime_runner_alive) },
    { label: "research runner 프로세스", value: runnerLabel(health?.research_runner_alive) },
    { label: "kis trader runner 프로세스", value: runnerLabel(health?.kis_trader_runner_alive) },
    { label: "reports crawler 프로세스", value: runnerLabel(health?.naver_reports_runner_alive) },
  ];

  const reportTotal = Number(reports?.repository?.total_reports || 0);
  const learningTotalCount = normalizeNonNegativeInt(state.dashboard?.research?.learning_total_count);
  const reportUpdated = reports?.repository?.last_updated_at
    ? fmtKST(reports.repository.last_updated_at, true)
    : "--";
  const ragAvailable = reports?.rag?.available ? "available" : "unavailable";
  const ragCount = Number(reports?.rag?.count || 0);
  const dataRows = [
    { label: "reports db", value: String(reportTotal) },
    { label: "reports updated", value: reportUpdated },
    { label: "rag status", value: ragAvailable },
    { label: "rag chunks", value: String(ragCount) },
    { label: "누적 학습 횟수", value: learningTotalCount === null ? "-" : String(learningTotalCount) },
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
        <h4>Freqtrade 실행</h4>
        <ul class="helper-runtime-list">
          ${renderRows(botRows)}
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

  const validTabs = new Set(["research", "runtime", "kis_trader", "reports"]);
  if (!validTabs.has(state.activeHelperTab)) {
    state.activeHelperTab = "research";
  }

  tabsRoot.querySelectorAll("[data-helper-tab]").forEach((button) => {
    const active = button.dataset.helperTab === state.activeHelperTab;
    button.classList.toggle("active", active);
  });

  let updatedAt = state.dashboard?.clock_utc || "";
  const score = normalizeScore100(state.dashboard?.research?.agent_self_score_100);
  scoreRoot.textContent = score === null ? "현재 역량 --/100" : `현재 역량 ${score}/100`;
  if (state.activeHelperTab === "runtime") {
    contentRoot.innerHTML = renderRuntimeHelperTab();
    updatedAt = pickUpdatedAt(state.strategyControl) || pickUpdatedAt(state.reportsStatus) || updatedAt;
  } else if (state.activeHelperTab === "kis_trader") {
    contentRoot.innerHTML = renderStatusHelperTab(state.kisTraderStatus, state.kisTraderError, "KIS 트레이더");
    updatedAt = pickUpdatedAt(state.kisTraderStatus) || updatedAt;
  } else if (state.activeHelperTab === "reports") {
    contentRoot.innerHTML = renderStatusHelperTab(state.reportsStatus, state.reportsError, "리포트 수집");
    updatedAt = pickUpdatedAt(state.reportsStatus) || updatedAt;
  } else {
    contentRoot.innerHTML = renderResearchHelperTab();
    updatedAt = pickUpdatedAt(state.dashboard?.research) || updatedAt;
  }

  updatedRoot.textContent = updatedAt ? `업데이트 KST ${fmtKST(updatedAt, true)}` : "업데이트 --";
}

function renderStrategyControl() {
  const payload = state.strategyControl;
  const rows = payload?.items || [];
  const updatedAt = payload?.updated_at || "";
  qs("strategyUpdatedAt").textContent = updatedAt
    ? `KST ${fmtKST(updatedAt, true)}`
    : "--";

  if (!rows.length) {
    qs("strategyList").innerHTML = `
      <article class="strategy-row">
        <div class="strategy-footnote">전략 정보가 없습니다.</div>
      </article>
    `;
    return;
  }

  qs("strategyList").innerHTML = rows
    .map((row) => {
      const running = Boolean(row.running);
      const reachable = Boolean(row.api_reachable);
      const pidText = row.pid ? `PID ${row.pid}` : "PID -";
      const apiText = row.api_url || "API URL -";
      const limitCurrency = String(row.bot_id || "") === "kis" ? "KRW" : "USDT";
      const limitLabel = `${limitCurrency} LIMIT`;
      const usdtLimit = Number(row.usdt_limit || 0);
      const usdtLimitText = usdtLimit > 0 ? fmtNum(usdtLimit, 2) : "";
      return `
      <article class="strategy-row">
        <div class="strategy-row-head">
          <h4 class="strategy-title">${escapeHTML(row.label || row.bot_id)}</h4>
          <div class="strategy-actions">
            <button class="btn" type="button" data-strategy-action="start" data-bot-id="${escapeHTML(row.bot_id)}" ${running ? "disabled" : ""}>Start</button>
            <button class="btn warm" type="button" data-strategy-action="stop" data-bot-id="${escapeHTML(row.bot_id)}" ${running ? "" : "disabled"}>Stop</button>
          </div>
        </div>
        <div class="strategy-meta">
          <span class="strategy-pill ${running ? "running" : "stopped"}">${running ? "RUNNING" : "STOPPED"}</span>
          <span class="strategy-pill ${reachable ? "reachable" : "unreachable"}">${reachable ? "API UP" : "API DOWN"}</span>
          <span class="strategy-pill mono">${escapeHTML(pidText)}</span>
          <span class="strategy-pill mono">${escapeHTML(limitLabel)} ${escapeHTML(usdtLimitText || "-")}</span>
        </div>
        <div class="strategy-actions">
          <input class="strategy-limit-input" type="number" min="0.01" step="0.01" data-limit-bot-id="${escapeHTML(row.bot_id)}" value="${escapeHTML(usdtLimitText)}" placeholder="${escapeHTML(limitCurrency)} limit" />
          <button class="btn ghost" type="button" data-strategy-action="set-limit" data-bot-id="${escapeHTML(row.bot_id)}">Set Limit</button>
        </div>
        <div class="strategy-footnote mono">${escapeHTML(apiText)}</div>
      </article>
    `;
    })
    .join("");
}

async function refreshStrategyControl() {
  state.strategyControl = await getJSON("/freqtrade/strategies");
  renderStrategyControl();
  renderHelperAgent();
}

async function runStrategyAction(path) {
  state.strategyControl = await getJSON(path, {
    method: "POST",
    body: JSON.stringify({}),
  });
  renderStrategyControl();
  await refreshDashboard();
}

async function runStrategyActionWithBody(path, body) {
  state.strategyControl = await getJSON(path, {
    method: "POST",
    body: JSON.stringify(body || {}),
  });
  renderStrategyControl();
  await refreshDashboard();
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
  const [dashboardResult, kisResult, reportsResult, healthResult] = await Promise.allSettled([
    getJSON("/dashboard"),
    getJSON("/kis/trader/status"),
    getJSON("/reports/status"),
    getJSON("/health"),
  ]);

  if (dashboardResult.status !== "fulfilled") {
    throw dashboardResult.reason;
  }

  state.dashboard = dashboardResult.value;
  if (kisResult.status === "fulfilled") {
    state.kisTraderStatus = kisResult.value;
    state.kisTraderError = "";
  } else {
    state.kisTraderStatus = null;
    state.kisTraderError = getErrorMessage(kisResult.reason);
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
  qs("helperNavBtn").addEventListener("click", () => {
    openHelperPage("research");
  });
  qs("helperBackBtn").addEventListener("click", openMainPage);
  qs("refreshBtn").addEventListener("click", async () => {
    await Promise.all([refreshDashboard(), refreshStrategyControl()]);
  });
  qs("strategyRefreshBtn").addEventListener("click", refreshStrategyControl);
  qs("strategyStartAllBtn").addEventListener("click", async () => {
    await runStrategyAction("/freqtrade/strategies/start-all");
  });
  qs("strategyStopAllBtn").addEventListener("click", async () => {
    await runStrategyAction("/freqtrade/strategies/stop-all");
  });
  qs("strategyList").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-strategy-action]");
    if (!button) return;
    const action = String(button.dataset.strategyAction || "").trim();
    const botId = String(button.dataset.botId || "").trim();
    if (!action || !botId) return;
    if (action === "set-limit") {
      const input = qs("strategyList").querySelector(
        `[data-limit-bot-id="${botId.replace(/"/g, '\\"')}"]`
      );
      const value = Number(input?.value || 0);
      if (!Number.isFinite(value) || value <= 0) {
        return;
      }
      await runStrategyActionWithBody(
        `/freqtrade/strategies/${encodeURIComponent(botId)}/usdt-limit`,
        { usdt_limit: value }
      );
      return;
    }
    const path =
      action === "start"
        ? `/freqtrade/strategies/${encodeURIComponent(botId)}/start`
        : `/freqtrade/strategies/${encodeURIComponent(botId)}/stop`;
    await runStrategyAction(path);
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
    state.activeHelperTab = String(button.dataset.helperTab || "research");
    renderHelperAgent();
  });

  await loadTelegramStatus();
  renderPageMode();
  await Promise.all([refreshDashboard(), refreshStrategyControl()]);
}

init();
