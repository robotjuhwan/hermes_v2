(function () {
  function researchRows(payload) {
    return Array.isArray(payload?.items) ? payload.items : [];
  }

  function universeRows(status) {
    if (Array.isArray(status?.configured_universe) && status.configured_universe.length) {
      return status.configured_universe;
    }
    return Array.isArray(status?.universe) ? status.universe : [];
  }

  function researchStale(isoString, nowMs = Date.now()) {
    if (!isoString) return false;
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return false;
    return nowMs - date.getTime() > 24 * 60 * 60 * 1000;
  }

  function escapeValue(value, options = {}) {
    const escapeFn = typeof options.escapeHTML === "function"
      ? options.escapeHTML
      : (next) => String(next ?? "");
    return escapeFn(String(value ?? ""));
  }

  function numberValue(value, fallback = 0, options = {}) {
    if (typeof options.asNumber === "function") {
      return options.asNumber(value, fallback);
    }
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
  }

  function formatNum(value, digits = 4, options = {}) {
    if (typeof options.fmtNum === "function") {
      return options.fmtNum(value, digits);
    }
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    return num.toLocaleString("ko-KR", { maximumFractionDigits: digits });
  }

  function formatKRW(value, options = {}) {
    if (typeof options.fmtKRW === "function") {
      return options.fmtKRW(value);
    }
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    return Math.round(num).toLocaleString("ko-KR");
  }

  function formatKST(value, withDate, options = {}) {
    if (typeof options.fmtKST === "function") {
      return options.fmtKST(value, withDate);
    }
    return value ? String(value) : "-";
  }

  function nonNegativeInt(value, options = {}) {
    if (typeof options.normalizeNonNegativeInt === "function") {
      return options.normalizeNonNegativeInt(value);
    }
    const num = Number(value);
    return Number.isFinite(num) && num >= 0 ? Math.floor(num) : 0;
  }

  function horizonWeight(value, options = {}) {
    if (typeof options.blockHorizonWeight === "function") {
      return options.blockHorizonWeight(value);
    }
    const num = Number(value);
    if (!Number.isFinite(num)) return "-";
    return `${Math.round(num * 100)}%`;
  }

  function coreAllocation(payload, blocks, options = {}) {
    const horizonFn = typeof options.horizonFn === "function" ? options.horizonFn : () => "";
    const items = Array.isArray(payload?.horizon_allocation?.items)
      ? payload.horizon_allocation.items
      : [];
    const row = items.find((item) => horizonFn(item) === "core_etf");
    const coreBlocks = (Array.isArray(blocks) ? blocks : [])
      .filter((block) => horizonFn(block) === "core_etf");
    if (!row) {
      return {
        actual: null,
        target: null,
        value: null,
        blockCount: coreBlocks.length,
      };
    }
    return {
      actual: row.actual_weight ?? row.weight ?? row.current_weight ?? null,
      target: row.target_weight ?? row.target ?? null,
      value: row.current_value_krw ?? row.value_krw ?? row.open_value_krw ?? null,
      blockCount: coreBlocks.length,
    };
  }

  function snapshotChip(snapshot, options = {}) {
    const status = String(snapshot?.status || "missing");
    if (status === "ok" && researchStale(snapshot?.captured_at)) {
      return '<span class="strategy-data-chip warn">snapshot stale</span>';
    }
    if (status === "ok") return '<span class="strategy-data-chip good">snapshot ok</span>';
    if (status === "error") return '<span class="strategy-data-chip warn">snapshot error</span>';
    if (status === "stale") return '<span class="strategy-data-chip warn">snapshot stale</span>';
    return '<span class="strategy-data-chip neutral">snapshot missing</span>';
  }

  function scoreChips(score, options = {}) {
    const label = String(score?.label || "unknown");
    const chips = [
      `<span class="strategy-data-chip ${label === "unknown" ? "neutral" : "good"}">${escapeValue(label, options)}</span>`,
    ];
    if (!score?.scored_at) {
      chips.push('<span class="strategy-data-chip neutral">score missing</span>');
    } else if (researchStale(score.scored_at)) {
      chips.push('<span class="strategy-data-chip warn">score stale</span>');
    }
    const risks = Array.isArray(score?.risks) ? score.risks : [];
    if (risks.length) {
      chips.push(`<span class="strategy-data-chip warn">risk ${escapeValue(String(risks.length), options)}</span>`);
    }
    return chips.join("");
  }

  function renderCandidateRow(item, options = {}) {
    const snapshot = item?.latest_snapshot || {};
    const score = item?.latest_score || {};
    const symbol = item?.symbol || snapshot.symbol || score.symbol || "-";
    const name = item?.name || snapshot.name || symbol;
    const price = snapshot.price === undefined || snapshot.price === null ? "-" : `${formatKRW(snapshot.price, options)}원`;
    const changePct = snapshot.change_pct === undefined || snapshot.change_pct === null
      ? "-"
      : `${formatNum(snapshot.change_pct, 2, options)}%`;
    const turnover = snapshot.turnover_krw ? `${formatKRW(snapshot.turnover_krw, options)}원` : "-";
    const liquidity = score.liquidity_score === undefined || score.liquidity_score === null
      ? "-"
      : formatNum(score.liquidity_score, 0, options);
    const coreFit = score.core_fit_score === undefined || score.core_fit_score === null
      ? "-"
      : formatNum(score.core_fit_score, 0, options);
    return `
      <div class="etf-candidate-row">
        <div class="etf-candidate-id">
          <strong>${escapeValue(name, options)}</strong>
          <span class="mono">${escapeValue(symbol, options)}</span>
        </div>
        <div class="etf-candidate-metrics">
          <span><b>${escapeValue(price, options)}</b>가격</span>
          <span class="${numberValue(snapshot.change_pct, 0, options) >= 0 ? "gain" : "loss"}"><b>${escapeValue(changePct, options)}</b>등락</span>
          <span><b>${escapeValue(turnover, options)}</b>거래대금</span>
          <span><b>${escapeValue(liquidity, options)}</b>유동성</span>
          <span><b>${escapeValue(coreFit, options)}</b>Core-fit</span>
        </div>
        <div class="etf-candidate-chips">
          ${snapshotChip(snapshot, options)}
          ${scoreChips(score, options)}
        </div>
      </div>
    `;
  }

  function renderCoreBoard(payload, blocks, options = {}) {
    const research = options.research || {};
    const reportRepo = options.reportRepository || {};
    const status = research.status || {};
    const candidates = researchRows(research.candidates);
    const universe = universeRows(status);
    const allocation = coreAllocation(payload, blocks, { horizonFn: options.horizonFn });
    const universeCount = status.configured_universe?.length ?? status.universe_count ?? universe.length;
    const busy = research.loading || research.running ? "disabled" : "";
    const loadingChip = research.loading ? '<span class="strategy-data-chip neutral">loading</span>' : "";
    const runningChip = research.running ? '<span class="strategy-data-chip warn">collecting</span>' : "";
    const errorChip = research.error ? `<span class="strategy-data-chip warn">${escapeValue(research.error, options)}</span>` : "";
    const latestSnapshot = status.latest_snapshot_at ? `snapshot ${formatKST(status.latest_snapshot_at, true, options)}` : "snapshot missing";
    const latestScore = status.latest_score_at ? `score ${formatKST(status.latest_score_at, true, options)}` : "score missing";
    const etfLinkCount = nonNegativeInt(reportRepo.etf_link_count, options);
    const linkedReportCount = nonNegativeInt(reportRepo.linked_report_count, options);
    const unlinkedEtfKeywordCount = nonNegativeInt(reportRepo.unlinked_etf_keyword_report_count, options);
    const lastSymbolLinkUpdated = reportRepo.last_symbol_link_updated_at
      ? `links ${formatKST(reportRepo.last_symbol_link_updated_at, true, options)}`
      : "links missing";
    return `
      <section class="etf-core-board">
        <div class="panel-head compact">
          <div>
            <h3>ETF/Core 리서치</h3>
            <p>코어 ETF 배정과 최신 후보 상태</p>
          </div>
          <div class="etf-core-actions">
            <button class="btn small ghost" type="button" data-etf-research-action="refresh" ${busy}>새로고침</button>
            <button class="btn small warm" type="button" data-etf-research-action="collect" ${busy}>ETF 리서치 갱신</button>
          </div>
        </div>
        <div class="etf-core-summary">
          <span><b>${allocation.actual === null ? "-" : horizonWeight(allocation.actual, options)}</b>Actual</span>
          <span><b>${allocation.target === null ? "-" : horizonWeight(allocation.target, options)}</b>Target</span>
          <span><b>${escapeValue(String(allocation.blockCount), options)}</b>Core blocks</span>
          <span><b>${allocation.value === null ? "-" : `${formatKRW(allocation.value, options)}원`}</b>평가금액</span>
        </div>
        <div class="etf-universe-strip">
          <span class="strategy-data-chip">Universe ${escapeValue(String(universeCount || 0), options)}</span>
          ${universe.slice(0, 8).map((row) => `<span class="strategy-data-chip neutral">${escapeValue(row.name || row.symbol || "-", options)}</span>`).join("")}
          ${universe.length > 8 ? `<span class="strategy-data-chip neutral">+${escapeValue(String(universe.length - 8), options)}</span>` : ""}
          <span class="strategy-data-chip ${status.latest_snapshot_at ? "good" : "neutral"}">${escapeValue(latestSnapshot, options)}</span>
          <span class="strategy-data-chip ${status.latest_score_at ? "good" : "neutral"}">${escapeValue(latestScore, options)}</span>
          <span class="strategy-data-chip ${linkedReportCount ? "good" : "neutral"}">Linked reports ${escapeValue(linkedReportCount === null ? "--" : String(linkedReportCount), options)}</span>
          <span class="strategy-data-chip ${etfLinkCount ? "good" : "neutral"}">ETF links ${escapeValue(etfLinkCount === null ? "--" : String(etfLinkCount), options)}</span>
          <span class="strategy-data-chip ${unlinkedEtfKeywordCount ? "warn" : "neutral"}">Unlinked ETF ${escapeValue(unlinkedEtfKeywordCount === null ? "--" : String(unlinkedEtfKeywordCount), options)}</span>
          <span class="strategy-data-chip ${reportRepo.last_symbol_link_updated_at ? "good" : "neutral"}">${escapeValue(lastSymbolLinkUpdated, options)}</span>
          ${loadingChip}${runningChip}${errorChip}
        </div>
        <div class="etf-candidate-list">
          ${candidates.length ? candidates.slice(0, 6).map((row) => renderCandidateRow(row, options)).join("") : '<div class="notice compact">ETF 후보/스냅샷 데이터가 아직 없습니다.</div>'}
        </div>
      </section>
    `;
  }

  window.HERMES_ETF_TAB = Object.freeze({
    researchRows,
    universeRows,
    researchStale,
    coreAllocation,
    snapshotChip,
    scoreChips,
    renderCandidateRow,
    renderCoreBoard,
  });
})();
