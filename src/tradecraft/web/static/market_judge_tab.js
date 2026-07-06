(() => {
  function htmlEscape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function helpers(options) {
    return {
      escapeHTML: typeof options?.escapeHTML === "function" ? options.escapeHTML : htmlEscape,
      fmtNum: typeof options?.fmtNum === "function" ? options.fmtNum : (value, digits = 0) => Number(value || 0).toFixed(digits),
      fmtKRW: typeof options?.fmtKRW === "function" ? options.fmtKRW : (value) => String(Math.round(Number(value || 0))),
      fmtKST: typeof options?.fmtKST === "function" ? options.fmtKST : (value) => String(value || "-"),
      asNumber: typeof options?.asNumber === "function" ? options.asNumber : (value, fallback = 0) => {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
      },
    };
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

  function marketPulseLabel(value) {
    const labels = {
      risk_on: "Risk-on",
      risk_off: "Risk-off",
      rotation: "순환매",
      choppy: "혼조",
    };
    const key = String(value || "").trim();
    return labels[key] || key || "대기";
  }

  function marketPulseWeightLabel(value, options = {}) {
    const { fmtNum } = helpers(options);
    const numericValue = Number(value || 0);
    const pct = Math.abs(numericValue) <= 1 ? numericValue * 100 : numericValue;
    return `${fmtNum(pct, 1)}%`;
  }

  function renderMarketPulseScoreComponents(pulse, options = {}) {
    const { escapeHTML, fmtNum } = helpers(options);
    const components = pulse?.score_components && typeof pulse.score_components === "object"
      ? pulse.score_components
      : {};
    const componentKeys = [
      ["index_score", "지수"],
      ["investor_flow_score", "수급"],
      ["program_score", "프로그램"],
      ["sector_score", "섹터"],
      ["fx_risk_score", "환율"],
      ["futures_basis_score", "베이시스"],
      ["block_exposure_score", "블록"],
    ];
    const cards = componentKeys
      .filter(([key]) => components[key] !== undefined && components[key] !== null)
      .map(([key, fallbackLabel]) => {
        const component = components[key];
        const payload = component && typeof component === "object" ? component : { score: component };
        const label = payload.label || fallbackLabel;
        const reason = payload.reason || key;
        return `
          <div class="market-pulse-score-card">
            <span>${escapeHTML(label)}</span>
            <strong>${escapeHTML(fmtNum(payload.score || 0, 1))}</strong>
            <small>${escapeHTML(reason)}</small>
          </div>
        `;
      });
    if (typeof components.total_score === "number") {
      cards.unshift(`
        <div class="market-pulse-score-card total">
          <span>합계</span>
          <strong>${escapeHTML(fmtNum(components.total_score, 1))}</strong>
          <small>${escapeHTML(pulse?.score_method_version || "score")}</small>
        </div>
      `);
    } else if (pulse?.score_method_version) {
      cards.unshift(`
        <div class="market-pulse-score-card total">
          <span>스코어</span>
          <strong>${escapeHTML(fmtNum(pulse?.score || 0, 1))}</strong>
          <small>${escapeHTML(pulse.score_method_version)}</small>
        </div>
      `);
    }
    if (!cards.length) {
      return "";
    }
    return `<div class="market-pulse-score-grid">${cards.join("")}</div>`;
  }

  function renderMarketPulseWeightChips(weights, prefix, options = {}) {
    const { escapeHTML } = helpers(options);
    if (!weights || typeof weights !== "object") return "";
    return Object.entries(weights)
      .filter(([, value]) => Number.isFinite(Number(value)))
      .sort((a, b) => Math.abs(Number(b[1])) - Math.abs(Number(a[1])))
      .slice(0, 4)
      .map(([name, value]) => `<span class="strategy-data-chip neutral">${escapeHTML(prefix)} ${escapeHTML(name)} ${escapeHTML(marketPulseWeightLabel(value, options))}</span>`)
      .join("");
  }

  function renderMarketPulseBlockExposure(pulse, options = {}) {
    const { escapeHTML, fmtNum } = helpers(options);
    const blockExposure = pulse?.block_exposure && typeof pulse.block_exposure === "object"
      ? pulse.block_exposure
      : {};
    const blockCount = Number(blockExposure.block_count || 0);
    const sectorWeights = renderMarketPulseWeightChips(blockExposure.sector_weights, "섹터", options);
    const marketWeights = renderMarketPulseWeightChips(blockExposure.market_weights, "시장", options);
    const concentrationFlags = Array.isArray(blockExposure.concentration_flags)
      ? blockExposure.concentration_flags
      : [];
    const pressureFlags = Array.isArray(blockExposure.pressure_flags)
      ? blockExposure.pressure_flags
      : [];
    const flags = [...concentrationFlags, ...pressureFlags];

    if (!blockCount && !sectorWeights && !marketWeights && !flags.length) {
      return '<div class="market-pulse-block-strip"><span class="strategy-data-chip good">블록 노출 없음</span></div>';
    }

    return `
      <div class="market-pulse-block-strip">
        <span class="strategy-data-chip">블록 ${escapeHTML(fmtNum(blockCount, 0))}건</span>
        ${sectorWeights}
        ${marketWeights}
        ${flags.map((item) => `<span class="strategy-data-chip warn">${escapeHTML(item)}</span>`).join("")}
        ${!blockCount ? '<span class="strategy-data-chip good">블록 노출 없음</span>' : ""}
      </div>
    `;
  }

  function renderMarketPulseSummary(viewState, options = {}) {
    const { escapeHTML, fmtNum, fmtKST } = helpers(options);
    const marketPulse = viewState?.marketPulse || {};
    const pulse = marketPulse.result;
    const error = marketPulse.error;
    const loading = marketPulse.loading || marketPulse.running;
    const disabled = marketPulse.running ? "disabled" : "";
    if (!pulse && !error && !loading) {
      return "";
    }
    if (error) {
      return `<section class="market-pulse-panel"><div class="notice">시장 펄스 실패: ${escapeHTML(error)}</div></section>`;
    }
    const indices = Array.isArray(pulse?.indices) ? pulse.indices.slice(0, 4) : [];
    const flows = Array.isArray(pulse?.investor_flows) ? pulse.investor_flows.slice(0, 3) : [];
    const programs = Array.isArray(pulse?.program_trading) ? pulse.program_trading.slice(0, 2) : [];
    const futures = pulse?.futures && typeof pulse.futures === "object" ? pulse.futures : {};
    const fx = pulse?.fx && typeof pulse.fx === "object" ? pulse.fx : {};
    const sectors = Array.isArray(pulse?.sectors?.items) ? pulse.sectors.items.slice(0, 4) : [];
    const gaps = Array.isArray(pulse?.data_gaps) ? pulse.data_gaps.slice(0, 4) : [];
    return `
      <section class="market-pulse-panel">
        <div class="market-pulse-head">
          <div>
            <span class="eyebrow">MARKET PULSE</span>
            <h4>${escapeHTML(marketPulseLabel(pulse?.regime))} · ${escapeHTML(fmtNum(pulse?.score || 0, 1))}</h4>
            <p>${escapeHTML(fmtKST(pulse?.captured_at, true))} · ${escapeHTML(pulse?.status || "missing")}</p>
          </div>
          <div class="strategy-intel-actions compact">
            <button class="btn" type="button" data-market-pulse-action="refresh">펄스 보기</button>
            <button class="btn" type="button" data-market-pulse-action="run" ${disabled}>${marketPulse.running ? "수집 중" : "펄스 수집"}</button>
          </div>
        </div>
        <div class="market-pulse-grid">
          ${indices.map((row) => {
            const pct = Number(row?.change_pct || 0);
            return `
              <div class="market-pulse-index">
                <span>${escapeHTML(row?.name || row?.code || "-")}</span>
                <strong>${escapeHTML(fmtNum(row?.value || 0, 2))}</strong>
                <small class="${pct >= 0 ? "up" : "down"}">${pct >= 0 ? "+" : ""}${escapeHTML(fmtNum(pct, 2))}%</small>
              </div>
            `;
          }).join("")}
        </div>
        <div class="market-pulse-flow-grid">
          ${flows.length ? flows.map((row) => {
            const foreign = Number(row?.foreign_net_buy_100m_krw || 0);
            const institution = Number(row?.institution_net_buy_100m_krw || 0);
            const sum = Number(row?.foreign_institution_sum_100m_krw || 0);
            return `
              <div class="market-pulse-flow">
                <span>${escapeHTML(row?.name || row?.market || "-")} · ${escapeHTML(row?.as_of || "-")}</span>
                <strong class="${sum >= 0 ? "up" : "down"}">외+기관 ${sum >= 0 ? "+" : ""}${escapeHTML(fmtNum(sum, 0))}억</strong>
                <small>외국인 ${foreign >= 0 ? "+" : ""}${escapeHTML(fmtNum(foreign, 0))} · 기관 ${institution >= 0 ? "+" : ""}${escapeHTML(fmtNum(institution, 0))}</small>
              </div>
            `;
          }).join("") : '<span class="strategy-data-chip warn">투자자 수급 대기</span>'}
        </div>
        <div class="market-pulse-pressure-grid">
          ${programs.length ? programs.map((row) => {
            const total = Number(row?.total_net_buy_100m_krw || 0);
            const nonArb = Number(row?.non_arbitrage_net_buy_100m_krw || 0);
            return `
              <div class="market-pulse-pressure">
                <span>프로그램 ${escapeHTML(row?.name || row?.market || "-")} · ${escapeHTML(row?.as_of || "-")}</span>
                <strong class="${total >= 0 ? "up" : "down"}">순매수 ${total >= 0 ? "+" : ""}${escapeHTML(fmtNum(total, 0))}억</strong>
                <small>비차익 ${nonArb >= 0 ? "+" : ""}${escapeHTML(fmtNum(nonArb, 0))}</small>
              </div>
            `;
          }).join("") : '<span class="strategy-data-chip warn">프로그램 대기</span>'}
          <div class="market-pulse-pressure">
            <span>선물 베이시스</span>
            <strong class="${Number(futures?.basis || 0) >= 0 ? "up" : "down"}">${escapeHTML(futures?.status === "ok" ? fmtNum(futures.basis, 2) : "-")}</strong>
            <small>${escapeHTML(futures?.basis_signal || futures?.status || "missing")} · 선물 ${escapeHTML(futures?.status === "ok" ? `${fmtNum(futures.futures_change_pct || 0, 2)}%` : "-")}</small>
          </div>
          <div class="market-pulse-pressure">
            <span>USD/KRW</span>
            <strong class="${Number(fx?.change || 0) <= 0 ? "up" : "down"}">${escapeHTML(fx?.status === "ok" ? fmtNum(fx.value || 0, 2) : "-")}</strong>
            <small>${Number(fx?.change || 0) >= 0 ? "+" : ""}${escapeHTML(fx?.status === "ok" ? fmtNum(fx.change || 0, 2) : "-")} · ${escapeHTML(fx?.as_of || fx?.status || "missing")}</small>
          </div>
        </div>
        ${renderMarketPulseScoreComponents(pulse, options)}
        ${renderMarketPulseBlockExposure(pulse, options)}
        <div class="strategy-data-warning-strip">
          ${sectors.length ? sectors.map((row) => `<span class="strategy-data-chip">${escapeHTML(row.name)} ${escapeHTML(fmtNum(row.avg_strength || 0, 0))}</span>`).join("") : '<span class="strategy-data-chip warn">섹터 신호 대기</span>'}
          ${gaps.map((item) => `<span class="strategy-data-chip warn">${escapeHTML(item)}</span>`).join("")}
        </div>
      </section>
    `;
  }

  function renderAccountCashLine(account, options = {}) {
    const { fmtKRW, asNumber } = helpers(options);
    const cashLike = asNumber(account?.cash_krw, 0);
    const orderable = asNumber(account?.orderable_cash_krw, cashLike);
    const settled = asNumber(account?.settled_cash_krw, cashLike);
    const receivable = asNumber(account?.receivable_cash_krw, 0);
    const positionValue = asNumber(account?.position_value_krw, 0);
    const positionCount = account?.position_count || 0;
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

  function renderMarketJudgeHeader(payload, options = {}) {
    const { escapeHTML, fmtKST } = helpers(options);
    const run = payload?.run && typeof payload.run === "object" ? payload.run : {};
    const clock = payload?.clock && typeof payload.clock === "object"
      ? payload.clock
      : run.source_snapshot?.clock || {};
    const account = payload?.account && typeof payload.account === "object" ? payload.account : {};
    const status = payload?.status || run.status || "missing";
    const session = clock.session || run.market_session || "-";
    const accountLine = account.status
      ? renderAccountCashLine(account, options)
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

  function renderMarketQuoteLine(quote, options = {}) {
    const { fmtKRW, fmtNum } = helpers(options);
    if (!quote || typeof quote !== "object") return "시세 없음";
    const price = quote.price ? `${fmtKRW(quote.price)}원` : "-";
    const changePct = Number(quote.change_pct || 0);
    return `${price} · ${changePct >= 0 ? "+" : ""}${fmtNum(changePct, 2)}% · ${quote.source || "-"} · ${quote.status || "-"}`;
  }

  function renderMarketPositionLine(position, options = {}) {
    const { fmtKRW, fmtNum } = helpers(options);
    if (!position || typeof position !== "object" || !position.symbol) return "신규/미보유 후보";
    const pnlPct = Number(position.unrealized_pnl_pct || 0);
    const weight = Number(position.position_weight || 0) * 100;
    return `보유 ${fmtKRW(position.value_krw)}원 · 손익 ${fmtKRW(position.unrealized_pnl_krw)}원 / ${pnlPct >= 0 ? "+" : ""}${fmtNum(pnlPct, 2)}% · 비중 ${fmtNum(weight, 1)}%`;
  }

  function renderMarketJudgmentCard(row, options = {}) {
    const { escapeHTML, fmtNum } = helpers(options);
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
            <p>${escapeHTML(renderMarketQuoteLine(quote, options))}</p>
          </div>
          <div class="market-judge-action">
            <strong>${escapeHTML(marketActionLabel(row?.account_action))}</strong>
            <span>${escapeHTML(row?.stance || "-")} · ${escapeHTML(row?.horizon || "-")}</span>
          </div>
        </div>
        <div class="market-judge-meta">
          <span>confidence ${escapeHTML(fmtNum(Number(row?.confidence || 0), 2))}</span>
          <span>${escapeHTML(renderMarketPositionLine(position, options))}</span>
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

  function renderTab(viewState, options = {}) {
    const { escapeHTML } = helpers(options);
    const marketJudge = viewState?.marketJudge || {};
    const payload = marketJudge.result;
    const loadingHtml = marketJudge.loading || marketJudge.running
      ? '<div class="notice">장중 판단 데이터를 불러오는 중입니다.</div>'
      : "";
    const errorHtml = marketJudge.error
      ? `<div class="notice">장중 판단 실패: ${escapeHTML(marketJudge.error)}</div>`
      : "";
    const disabled = marketJudge.running ? "disabled" : "";
    if (!payload) {
      return `
        <div class="market-judge-shell">
          <div class="strategy-intel-actions">
            <button class="btn primary" type="button" data-market-judge-action="refresh">최근 판단 보기</button>
            <button class="btn" type="button" data-market-judge-action="run" ${disabled}>gpt-5.5 장중 판단</button>
          </div>
          ${renderMarketPulseSummary(viewState, options)}
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
          <button class="btn" type="button" data-market-judge-action="run" ${disabled}>${marketJudge.running ? "판단 중" : "gpt-5.5 장중 판단"}</button>
        </div>
        ${renderMarketPulseSummary(viewState, options)}
        ${loadingHtml}
        ${errorHtml}
        ${renderMarketJudgeHeader(payload, options)}
        <section class="market-judge-board">
          ${judgments.length ? judgments.map((row) => renderMarketJudgmentCard(row, options)).join("") : '<div class="notice">표시할 판단 종목이 없습니다.</div>'}
        </section>
        <p class="strategy-footnote">${escapeHTML(payload.disclaimer || "실거래 판단용입니다. 주문은 HERMES 안전 게이트와 블록 규칙을 통과한 경우에만 실행됩니다.")}</p>
      </div>
    `;
  }

  window.HERMES_MARKET_JUDGE_TAB = {
    marketActionLabel,
    marketJudgeTone,
    marketPulseLabel,
    marketPulseWeightLabel,
    renderMarketPulseSummary,
    renderMarketJudgeHeader,
    renderMarketJudgmentCard,
    renderTab,
  };
})();
