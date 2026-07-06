(function () {
  function defaultEscapeHTML(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function defaultFmtNum(value, maxFractionDigits = 4) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "-";
    return numeric.toLocaleString(undefined, {
      maximumFractionDigits: maxFractionDigits,
    });
  }

  function defaultFmtPercent(value, maxFractionDigits = 1) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "-";
    return `${defaultFmtNum(numeric, maxFractionDigits)}%`;
  }

  function defaultFmtKST(value) {
    return String(value || "-");
  }

  function defaultAsNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function helpers(options = {}) {
    return {
      escapeHTML: typeof options.escapeHTML === "function" ? options.escapeHTML : defaultEscapeHTML,
      fmtNum: typeof options.fmtNum === "function" ? options.fmtNum : defaultFmtNum,
      fmtPercent: typeof options.fmtPercent === "function" ? options.fmtPercent : defaultFmtPercent,
      fmtKST: typeof options.fmtKST === "function" ? options.fmtKST : defaultFmtKST,
      asNumber: typeof options.asNumber === "function" ? options.asNumber : defaultAsNumber,
      renderEvidencePolicyFlow: typeof options.renderEvidencePolicyFlow === "function"
        ? options.renderEvidencePolicyFlow
        : () => "",
    };
  }

  function symbolRowsMap(rows) {
    if (Array.isArray(rows)) {
      return rows.reduce((acc, row) => {
        const symbol = String(row?.symbol || "").trim();
        if (symbol) {
          acc[symbol] = row?.features && typeof row.features === "object"
            ? { symbol, ...row.features }
            : row;
        }
        return acc;
      }, {});
    }
    return rows && typeof rows === "object" ? rows : {};
  }

  function notesMap(context) {
    return symbolRowsMap(context?.symbol_notes || {});
  }

  function featuresMap(context) {
    return symbolRowsMap(
      context?.features
      || context?.symbol_features
      || context?.feature_packets
      || context?.items
      || {},
    );
  }

  function renderTimeframeGrid(feature, options = {}) {
    const { escapeHTML, fmtPercent } = helpers(options);
    const frames = feature?.timeframes && typeof feature.timeframes === "object"
      ? feature.timeframes
      : {};
    const preferred = ["1m", "5m", "15m", "1h", "4h"];
    const entries = preferred
      .filter((key) => frames[key])
      .map((key) => [key, frames[key]]);
    if (!entries.length) return "";
    return `
      <div class="crypto-timeframe-grid" aria-label="timeframe structure">
        ${entries.map(([key, row]) => `
          <span class="crypto-timeframe-cell ${escapeHTML(String(row.trend || ""))}">
            <b>${escapeHTML(key)}</b>
            <em>${escapeHTML(row.trend || "-")}</em>
            <small>${escapeHTML(fmtPercent(row.momentum_pct || 0, 1))}</small>
          </span>
        `).join("")}
      </div>
    `;
  }

  function renderResearchPanel(state, options = {}) {
    const { escapeHTML, fmtNum, fmtPercent } = helpers(options);
    const tabState = state?.cryptoResearch || {};
    const status = tabState.status || {};
    const context = tabState.context || {};
    const candidates = Array.isArray(context.candidates) ? context.candidates : [];
    const notes = notesMap(context);
    const features = featuresMap(context);
    const noteSymbols = Object.keys(notes);
    const featureSymbols = Object.keys(features);
    const regime = context.market_regime || status.market_regime || context.regime || {};
    const regimeLabel = regime.label || regime.regime || status.regime || "-";
    const focusCount = context.focus_symbol_count || context.focus_count || status.llm_top_symbols || status.focus_symbol_count || 0;
    const observedCount = context.observed_symbol_count || context.symbol_count || status.symbol_count || featureSymbols.length;
    const symbols = [...new Set([
      ...candidates.map((row) => String(row?.symbol || "").trim()).filter(Boolean),
      ...noteSymbols,
      ...featureSymbols,
    ])].slice(0, 8);
    const statusText = status.available === false ? "대기" : (status.status || context.status || "-");
    const result = tabState.result || {};
    const candidateCards = candidates.slice(0, 12).map((row) => {
      const symbol = String(row.symbol || "-");
      const note = notes[symbol] || {};
      return `
        <article class="crypto-research-card">
          <div class="crypto-research-card-head">
            <div>
              <span class="section-kicker">${escapeHTML(row.market || "spot")} · ${escapeHTML(row.horizon || "-")}</span>
              <h4>${escapeHTML(symbol)}</h4>
            </div>
            <span class="helper-runtime-chip ok">${escapeHTML(row.stance || note.stance || "-")}</span>
          </div>
          <div class="metric-row">
            <span>score ${escapeHTML(fmtNum(row.score, 0))}</span>
            <span>confidence ${escapeHTML(fmtPercent(Number(row.confidence || 0) * 100, 0))}</span>
          </div>
          <div class="strategy-chip-row compact">
            <span class="strategy-data-chip">entry ${escapeHTML(row.entry_quality || note.entry_quality || "-")}</span>
            <span class="strategy-data-chip">squeeze ${escapeHTML(row.squeeze_risk || note.squeeze_risk || "-")}</span>
            <span class="strategy-data-chip">R/R ${escapeHTML(fmtNum(row.reward_risk || row.rr || 0, 2))}</span>
          </div>
          <p>${escapeHTML(row.reason_md || note.summary_md || "")}</p>
        </article>
      `;
    }).join("");
    const noteCards = symbols.map((symbol) => {
      const note = notes[symbol] || {};
      const feature = features[symbol] || {};
      const summary = note.summary_md || note.summary || feature.summary || "";
      const trend = feature.trend_1m || feature.trend || feature.market_structure || "-";
      const alignment = feature.timeframe_alignment || feature.alignment || "-";
      const entryQuality = feature.entry_quality || note.entry_quality || "-";
      const squeezeRisk = feature.squeeze_risk || note.squeeze_risk || "-";
      const volume = feature.quote_volume_usdt ?? feature.volume_usdt ?? feature.quote_volume ?? null;
      return `
        <article class="crypto-research-note">
          <div class="card-row">
            <strong>${escapeHTML(symbol)}</strong>
            <span class="status-chip">${escapeHTML(alignment || note.stance || trend || "-")}</span>
          </div>
          <p>${escapeHTML(summary || "메모리가 아직 없습니다.")}</p>
          ${renderTimeframeGrid(feature, options)}
          <div class="strategy-chip-row">
            <span class="strategy-data-chip">trend ${escapeHTML(trend)}</span>
            <span class="strategy-data-chip">entry ${escapeHTML(entryQuality)}</span>
            <span class="strategy-data-chip">squeeze ${escapeHTML(squeezeRisk)}</span>
            <span class="strategy-data-chip">volume ${escapeHTML(volume === null ? "-" : fmtNum(volume, 0))}</span>
          </div>
        </article>
      `;
    }).join("");

    return `
      <section class="memory-section crypto-research-panel" data-crypto-research-panel>
        <div class="panel-head compact">
          <div>
            <span class="section-kicker">Crypto Market Research</span>
            <h3>바이낸스 리서치</h3>
          </div>
          <div class="strategy-intel-actions">
            <button class="btn ghost" type="button" data-crypto-research-action="refresh" ${tabState.loading ? "disabled" : ""}>갱신</button>
            <button class="btn" type="button" data-crypto-research-action="collect" ${tabState.running ? "disabled" : ""}>구조 수집</button>
            <button class="btn warm" type="button" data-crypto-research-action="run" ${tabState.running ? "disabled" : ""}>
              ${tabState.running ? "리서치 중..." : "AI 리서치"}
            </button>
          </div>
        </div>
        ${tabState.error ? `<div class="notice">크립토 리서치 조회 실패: ${escapeHTML(tabState.error)}</div>` : ""}
        <div class="block-trader-kpis">
          <article class="mini-card"><p>상태</p><h4>${escapeHTML(statusText)}</h4></article>
          <article class="mini-card"><p>시장 레짐</p><h4>${escapeHTML(regimeLabel)}</h4></article>
          <article class="mini-card"><p>관측/집중</p><h4>${escapeHTML(`${fmtNum(observedCount, 0)} / ${fmtNum(focusCount, 0)}`)}</h4></article>
          <article class="mini-card"><p>스냅샷</p><h4>${escapeHTML(fmtNum(status.snapshot_count, 0))}</h4></article>
          <article class="mini-card"><p>후보</p><h4>${escapeHTML(fmtNum(status.candidate_count ?? candidates.length, 0))}</h4></article>
          <article class="mini-card"><p>노트</p><h4>${escapeHTML(fmtNum(noteSymbols.length, 0))}</h4></article>
          <article class="mini-card"><p>최근 실행</p><h4>${escapeHTML(result.status || status.last_run_status || "-")}</h4></article>
        </div>
        ${regime.summary ? `<div class="crypto-edge-strip">${escapeHTML(regime.summary)}</div>` : ""}
        <div class="crypto-research-grid">
          ${candidateCards || '<div class="notice">아직 생성된 크립토 후보가 없습니다.</div>'}
        </div>
        <div class="crypto-research-notes">
          ${noteCards || '<div class="notice">심볼 노트와 피처를 기다리는 중입니다.</div>'}
        </div>
      </section>
    `;
  }

  function renderAlphaPanel(state, options = {}) {
    const { escapeHTML, fmtNum, fmtPercent } = helpers(options);
    const tabState = state?.cryptoAlpha || {};
    const status = tabState.status || {};
    const context = tabState.context || {};
    const events = Array.isArray(context.events) ? context.events : [];
    const outcomes = Array.isArray(context.similar_outcomes) ? context.similar_outcomes : [];
    const scorecards = Array.isArray(context.scorecards) ? context.scorecards : [];
    const lessons = Array.isArray(context.active_lessons) ? context.active_lessons : [];
    const gaps = Array.isArray(context.data_gaps) ? context.data_gaps : [];
    const result = tabState.result || {};
    const statusText = status.available === false ? "대기" : (status.status || context.status || "-");
    const eventCards = events.slice(0, 8).map((event) => `
      <article class="crypto-alpha-event">
        <div class="card-row">
          <strong>${escapeHTML(event.event_type || "event")}</strong>
          <span class="status-chip">${escapeHTML(fmtNum(Number(event.importance || 0), 2))}</span>
        </div>
        <p>${escapeHTML(event.title || event.summary || "-")}</p>
        <div class="strategy-chip-row compact">
          ${(Array.isArray(event.symbols) ? event.symbols : []).slice(0, 5).map((symbol) => (
            `<span class="strategy-data-chip">${escapeHTML(symbol)}</span>`
          )).join("")}
          <span class="strategy-data-chip">${escapeHTML(event.source_id || "-")}</span>
        </div>
      </article>
    `).join("");
    const outcomeRows = outcomes.slice(0, 6).map((row) => `
      <div class="crypto-alpha-outcome">
        <span>${escapeHTML(row.symbol || "-")}</span>
        <strong>${escapeHTML(fmtPercent(row.return_pct || 0, 2))}</strong>
        <small>${escapeHTML(`${row.event_type || "-"} · R ${fmtNum(row.r_multiple || 0, 2)}`)}</small>
      </div>
    `).join("");
    return `
      <section class="memory-section crypto-alpha-panel">
        <div class="panel-head compact">
          <div>
            <span class="section-kicker">Crypto Alpha DB</span>
            <h3>촉매·결과 라벨</h3>
          </div>
          <div class="strategy-intel-actions">
            <button class="btn ghost" type="button" data-crypto-alpha-action="refresh" ${tabState.loading ? "disabled" : ""}>갱신</button>
            <button class="btn warm" type="button" data-crypto-alpha-action="collect" ${tabState.running ? "disabled" : ""}>
              ${tabState.running ? "수집 중..." : "알파 수집"}
            </button>
            <button class="btn" type="button" data-crypto-alpha-action="outcomes" ${tabState.running ? "disabled" : ""}>결과 라벨</button>
          </div>
        </div>
        ${tabState.error ? `<div class="notice">크립토 알파 조회 실패: ${escapeHTML(tabState.error)}</div>` : ""}
        <div class="block-trader-kpis">
          <article class="mini-card"><p>상태</p><h4>${escapeHTML(statusText)}</h4></article>
          <article class="mini-card"><p>이벤트</p><h4>${escapeHTML(fmtNum(status.events || context.event_count || 0, 0))}</h4></article>
          <article class="mini-card"><p>결과 라벨</p><h4>${escapeHTML(fmtNum(status.outcomes || outcomes.length, 0))}</h4></article>
          <article class="mini-card"><p>패턴</p><h4>${escapeHTML(fmtNum(status.hypotheses || scorecards.length, 0))}</h4></article>
          <article class="mini-card"><p>최근 실행</p><h4>${escapeHTML(result.status || "-")}</h4></article>
        </div>
        <div class="crypto-alpha-layout">
          <div class="crypto-alpha-events">
            ${eventCards || '<div class="notice">아직 압축된 촉매 이벤트가 없습니다.</div>'}
          </div>
          <aside class="crypto-alpha-side">
            <h4>패턴 점수</h4>
            <div class="strategy-chip-row compact">
              ${scorecards.slice(0, 8).map((card) => `<span class="strategy-data-chip">${escapeHTML(card.pattern_key || "")}</span>`).join("") || '<span class="strategy-data-chip">scorecard 대기</span>'}
            </div>
            <h4>비슷한 결과</h4>
            <div class="crypto-alpha-outcomes">
              ${outcomeRows || '<p class="muted">결과 라벨이 쌓이면 여기에 표시됩니다.</p>'}
            </div>
            <h4>활성 교훈 / 빈칸</h4>
            <ul class="crypto-alpha-lessons">
              ${[...lessons.slice(0, 3), ...gaps.slice(0, 3).map((gap) => `gap: ${gap}`)].map((item) => `<li>${escapeHTML(item)}</li>`).join("") || '<li>쌓이는 중</li>'}
            </ul>
          </aside>
        </div>
      </section>
    `;
  }

  function renderQuantBoard(state, options = {}) {
    const { escapeHTML, fmtNum } = helpers(options);
    const trader = state?.binanceTrader || {};
    const rows = Array.isArray(trader.quantSignals) ? trader.quantSignals : [];
    if (trader.quantError) {
      return `<section class="memory-section binance-quant-panel"><div class="notice">퀀트 신호 조회 실패: ${escapeHTML(trader.quantError)}</div></section>`;
    }
    if (!rows.length) {
      return `
        <section class="memory-section binance-quant-panel">
          <div class="panel-head compact">
            <h3>정량 신호 보드</h3>
            <p>아직 저장된 바이낸스 퀀트 신호가 없습니다.</p>
          </div>
        </section>
      `;
    }
    const body = rows.map((item) => {
      const signal = item.signal || {};
      const metrics = signal.metrics || {};
      const bias = String(signal.bias || item.bias || "unknown");
      return `
        <tr>
          <td><strong>${escapeHTML(item.symbol || "-")}</strong><span>${escapeHTML(item.horizon || "-")}</span></td>
          <td><span class="quant-bias ${escapeHTML(bias)}">${escapeHTML(bias)}</span></td>
          <td class="num">${escapeHTML(fmtNum(item.long_score, 1))}</td>
          <td class="num">${escapeHTML(fmtNum(item.short_score, 1))}</td>
          <td class="num">${escapeHTML(fmtNum(item.no_trade_score, 1))}</td>
          <td class="num">${escapeHTML(fmtNum(metrics.atr_pct, 2))}%</td>
          <td class="num">${escapeHTML(fmtNum(metrics.rsi, 1))}</td>
          <td class="num">${escapeHTML(fmtNum(metrics.spread_bps, 2))}</td>
        </tr>
      `;
    }).join("");
    return `
      <section class="memory-section binance-quant-panel">
        <div class="panel-head compact">
          <h3>정량 신호 보드</h3>
          <p>롱·숏·관망 점수와 변동성/체결비용을 쥬 판단 전에 압축합니다.</p>
        </div>
        <div class="quant-table-wrap">
          <table class="quant-table">
            <thead>
              <tr>
                <th>심볼</th>
                <th>Bias</th>
                <th>Long</th>
                <th>Short</th>
                <th>No Trade</th>
                <th>ATR</th>
                <th>RSI</th>
                <th>Spread</th>
              </tr>
            </thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  function renderPatternBoard(state, options = {}) {
    const { escapeHTML, fmtNum, fmtPercent, fmtKST, asNumber } = helpers(options);
    const trader = state?.binanceTrader || {};
    const context = trader.patternContext || {};
    const rows = Array.isArray(context.scorecards) ? context.scorecards : [];
    const qualifiedRows = Array.isArray(context.qualified_scorecards) ? context.qualified_scorecards : [];
    const patterns = Array.isArray(context.patterns) ? context.patterns : [];
    const optimizedSets = Array.isArray(context.optimized_strategy_sets) ? context.optimized_strategy_sets : [];
    const optimization = context.optimization && typeof context.optimization === "object" ? context.optimization : {};
    if (trader.patternError) {
      return `<section class="memory-section binance-pattern-panel"><div class="notice">백테스트 랩 조회 실패: ${escapeHTML(trader.patternError)}</div></section>`;
    }
    if (!rows.length && !patterns.length && !optimizedSets.length) {
      return `
        <section class="memory-section binance-pattern-panel">
          <div class="panel-head compact">
            <h3>백테스트·최적화 랩</h3>
            <p>아직 검증된 패턴 scorecard와 최적화 세트가 없습니다.</p>
          </div>
        </section>
      `;
    }
    const promotionTimes = optimizedSets
      .map((row) => row.promoted_at)
      .filter(Boolean)
      .sort();
    const latestPromotion = promotionTimes.length ? promotionTimes[promotionTimes.length - 1] : "";
    const summaryCards = [
      { label: "최적화 세트", value: fmtNum(optimization.set_count || optimizedSets.length, 0), tone: optimizedSets.length ? "good" : "neutral" },
      { label: "검증 통과", value: fmtNum(qualifiedRows.length, 0), tone: qualifiedRows.length ? "good" : "warn" },
      { label: "원본 스코어카드", value: fmtNum(rows.length, 0), tone: rows.length ? "neutral" : "warn" },
      { label: "패턴 후보", value: fmtNum(patterns.length, 0), tone: patterns.length ? "neutral" : "warn" },
      { label: "최근 승격", value: latestPromotion ? fmtKST(latestPromotion, true) : "--", tone: latestPromotion ? "good" : "neutral" },
    ].map((item) => `
      <article class="backtest-kpi ${escapeHTML(item.tone)}">
        <span>${escapeHTML(item.label)}</span>
        <strong>${escapeHTML(item.value)}</strong>
      </article>
    `).join("");
    const optimizedCards = optimizedSets.slice(0, 8).map((set) => {
      const params = set.parameter_set && typeof set.parameter_set === "object" ? set.parameter_set : {};
      const side = String(set.direction || "").toLowerCase();
      const sideClass = side === "short" ? "short" : side === "long" ? "long" : "neutral";
      const stopPct = asNumber(params.stop_pct, 0) * 100;
      const targetPct = asNumber(params.target_pct, 0) * 100;
      const holdingBars = params.holding_bars ?? "-";
      const sample = [set.sample_start, set.sample_end].filter(Boolean).join(" → ");
      return `
        <article class="optimized-set-card">
          <div class="optimized-set-head">
            <div>
              <strong>${escapeHTML(set.symbol || "-")}</strong>
              <span>${escapeHTML(set.pattern_key || set.family || "-")}</span>
            </div>
            <span class="quant-bias ${escapeHTML(sideClass)}">${escapeHTML(set.direction || "-")}</span>
          </div>
          <div class="optimized-param-row">
            <span><b>${escapeHTML(fmtPercent(stopPct, 2))}</b>손절</span>
            <span><b>${escapeHTML(fmtPercent(targetPct, 2))}</b>목표</span>
            <span><b>${escapeHTML(String(holdingBars))}</b>보유봉</span>
          </div>
          <div class="optimized-set-metrics">
            <span><b>${escapeHTML(fmtNum(set.objective_score, 1))}</b>objective</span>
            <span><b>${escapeHTML(fmtPercent(asNumber(set.win_rate, 0) * 100, 1))}</b>승률</span>
            <span><b>${escapeHTML(fmtNum(set.expectancy_r, 2))}R</b>기대값</span>
            <span><b>${escapeHTML(fmtNum(set.profit_factor, 2))}</b>PF</span>
            <span><b>${escapeHTML(fmtNum(set.trade_count, 0))}</b>거래</span>
          </div>
          <div class="strategy-chip-row compact">
            <span class="strategy-data-chip">${escapeHTML(set.objective || "risk_adjusted_net_r_v1")}</span>
            ${sample ? `<span class="strategy-data-chip neutral">${escapeHTML(sample)}</span>` : ""}
            ${set.promoted_at ? `<span class="strategy-data-chip good">${escapeHTML(fmtKST(set.promoted_at, true))}</span>` : ""}
          </div>
        </article>
      `;
    }).join("");
    const body = rows.slice(0, 12).map((row) => {
      const quality = row.entry_quality || {};
      const passed = Boolean(quality.passed);
      const failed = Array.isArray(quality.failed) ? quality.failed : [];
      return `
        <tr>
          <td>${escapeHTML(row.symbol || "-")}</td>
          <td>${escapeHTML(row.family || "-")}</td>
          <td>${escapeHTML(row.direction || "-")}</td>
          <td class="num">${escapeHTML(fmtNum(row.expectancy_r, 2))}R</td>
          <td class="num">${escapeHTML(fmtPercent((row.win_rate || 0) * 100, 1))}</td>
          <td class="num">${escapeHTML(fmtNum(row.trade_count, 0))}</td>
          <td>${passed ? '<span class="strategy-data-chip good">통과</span>' : `<span class="strategy-data-chip warn">${escapeHTML(failed.slice(0, 2).join(", ") || "점검")}</span>`}</td>
          <td class="num">${escapeHTML(fmtNum(row.score, 1))}</td>
        </tr>
      `;
    }).join("");
    return `
      <section class="memory-section binance-pattern-panel">
        <div class="panel-head compact">
          <div>
            <h3>백테스트·최적화 랩</h3>
            <p>패턴 검증 결과와 쥬가 가격 구조 prior로 참고하는 최적화 세트를 함께 봅니다.</p>
          </div>
          <div class="strategy-chip-row compact">
            <span class="strategy-data-chip">${escapeHTML(fmtNum(patterns.length, 0))} patterns</span>
            <span class="strategy-data-chip good">${escapeHTML(fmtNum(optimizedSets.length, 0))} optimized</span>
          </div>
        </div>
        <div class="backtest-kpi-grid">${summaryCards}</div>
        <div class="optimization-note">
          <strong>활용 방식</strong>
          <span>최적화 세트는 주문 명령이 아니라 손절·목표·보유봉을 잡는 검증된 가격 구조 후보입니다. 쥬는 이 값을 호가·스프레드·펀딩·오더북·라이브 권한과 맞을 때만 블록으로 승격합니다.</span>
        </div>
        <div class="optimized-set-board">
          ${optimizedCards || '<div class="notice">아직 승격된 최적화 세트가 없습니다.</div>'}
        </div>
        <div class="panel-subhead">
          <h4>원본 패턴 스코어카드</h4>
          <span>${escapeHTML(fmtNum(rows.length, 0))} rows · ${escapeHTML(fmtNum(qualifiedRows.length, 0))} qualified</span>
        </div>
        <div class="pattern-table-wrap">
          <table class="pattern-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Pattern</th>
                <th>Side</th>
                <th>Expectancy</th>
                <th>Win</th>
                <th>N</th>
                <th>Quality</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody>${body || '<tr><td colspan="8">scorecard 대기</td></tr>'}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  function renderLabTab(state, options = {}) {
    const {
      renderEvidencePolicyFlow,
    } = helpers(options);
    const cryptoResearch = state?.cryptoResearch || {};
    return `
      <div class="crypto-research-lab-shell">
        <section class="block-trader-hero crypto-research-lab-hero">
          <div>
            <span class="section-kicker">Crypto Research Lab</span>
            <h3>크립토 리서치 랩</h3>
            <p>바이낸스 쥬가 보는 시장 리서치, 알파 이벤트, 정량 신호, 패턴 검증을 블록 화면과 분리해서 봅니다.</p>
          </div>
          <div class="strategy-intel-actions">
            <button class="btn ghost" type="button" data-binance-action="refresh">퀀트·패턴 갱신</button>
            <button class="btn" type="button" data-crypto-research-action="refresh">리서치 갱신</button>
            <button class="btn warm" type="button" data-crypto-research-action="run" ${cryptoResearch.running ? "disabled" : ""}>
              ${cryptoResearch.running ? "AI 리서치 중..." : "AI 리서치"}
            </button>
          </div>
        </section>
        ${renderEvidencePolicyFlow()}
        ${renderResearchPanel(state, options)}
        ${renderAlphaPanel(state, options)}
        ${renderQuantBoard(state, options)}
        ${renderPatternBoard(state, options)}
      </div>
    `;
  }

  window.HERMES_CRYPTO_RESEARCH_TAB = Object.freeze({
    notesMap,
    featuresMap,
    renderTimeframeGrid,
    renderResearchPanel,
    renderAlphaPanel,
    renderQuantBoard,
    renderPatternBoard,
    renderLabTab,
  });
})();
