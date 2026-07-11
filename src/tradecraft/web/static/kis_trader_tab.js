(function () {
  const activeBlockStatuses = new Set(["entry_pending", "open", "exit_pending"]);
  const blockHorizons = [
    { key: "short", label: "단기", description: "가격·수급 반응 블록" },
    { key: "mid", label: "중기", description: "리포트·실적 thesis 검증" },
    { key: "long", label: "장기", description: "퀄리티·밸류 축적" },
    { key: "core_etf", label: "ETF/Core", description: "시장 노출·코어 자산" },
  ];

  function toNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function blockStatusLabel(value) {
    const labels = {
      proposed: "매수 대기",
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
    if (status === "proposed" || status === "entry_pending" || status === "exit_pending" || status === "paused") return "warn";
    if (status === "error") return "bad";
    return "neutral";
  }

  function timelineDateValue(block) {
    return block?.closed_at || block?.opened_at || block?.created_at || block?.updated_at || "";
  }

  function dateKeyKST(isoString) {
    if (!isoString) return "";
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return "";
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const pick = (type) => parts.find((part) => part.type === type)?.value || "";
    return `${pick("year")}-${pick("month")}-${pick("day")}`;
  }

  function timelineDate(block) {
    return dateKeyKST(timelineDateValue(block));
  }

  function historyDates(blocks) {
    return [...new Set((Array.isArray(blocks) ? blocks : []).map(timelineDate).filter(Boolean))]
      .sort()
      .reverse();
  }

  function exitOrder(payload, block) {
    const blockId = String(block?.block_id || "");
    const orders = Array.isArray(payload?.orders) ? payload.orders : [];
    return orders
      .filter((row) => (
        String(row?.block_id || "") === blockId
        && String(row?.side || "").toLowerCase() === "sell"
      ))
      .sort((a, b) => String(b?.created_at || "").localeCompare(String(a?.created_at || "")))[0] || null;
  }

  function historyPnl(payload, block, numberParser = toNumber) {
    const parse = typeof numberParser === "function" ? numberParser : toNumber;
    const qty = parse(block?.qty_initial || block?.qty_open, 0);
    const entry = parse(block?.entry_price, 0);
    const status = String(block?.status || "");
    const order = exitOrder(payload, block);
    const exitPrice = parse(
      order?.avg_fill_price
        || order?.limit_price
        || block?.current_price
        || block?.quote?.price,
      0,
    );
    const activePnl = parse(block?.unrealized_pnl_krw, 0);
    const pnl = status === "closed" && entry > 0 && exitPrice > 0
      ? (exitPrice - entry) * qty
      : activePnl;
    const pct = entry > 0 && qty > 0 ? (pnl / (entry * qty)) * 100 : 0;
    return {
      pnl,
      pct,
      exitPrice,
      exitOrder: order,
    };
  }

  function statusMatches(block, status) {
    const value = String(block?.status || "");
    if (status === "all") return true;
    if (status === "active") return activeBlockStatuses.has(value);
    if (status === "inactive") return !activeBlockStatuses.has(value);
    return value === status;
  }

  function filteredHistoryBlocks(payload, filters = {}, options = {}) {
    const blocks = Array.isArray(payload?.blocks) ? payload.blocks : [];
    const selectedDate = String(filters.date || "");
    const query = String(filters.query || "").trim().toLowerCase();
    const status = String(filters.status || "inactive");
    const horizon = String(filters.horizon || "all");
    const horizonFn = typeof options.horizonFn === "function" ? options.horizonFn : () => "";
    return blocks
      .filter((block) => !selectedDate || timelineDate(block) === selectedDate)
      .filter((block) => statusMatches(block, status))
      .filter((block) => horizon === "all" || horizonFn(block) === horizon)
      .filter((block) => {
        if (!query) return true;
        return [
          block?.symbol,
          block?.name,
          block?.block_id,
          block?.thesis,
          block?.llm_reason,
        ].some((value) => String(value || "").toLowerCase().includes(query));
      })
      .sort((a, b) => String(timelineDateValue(b)).localeCompare(String(timelineDateValue(a))));
  }

  function daySummary(payload, blocks, numberParser = toNumber) {
    const summary = {
      total: blocks.length,
      active: 0,
      closed: 0,
      error: 0,
      pnl: 0,
    };
    for (const block of blocks) {
      const status = String(block?.status || "");
      if (activeBlockStatuses.has(status)) summary.active += 1;
      if (status === "closed") summary.closed += 1;
      if (status === "error") summary.error += 1;
      summary.pnl += historyPnl(payload, block, numberParser).pnl;
    }
    return summary;
  }

  function normalizeBlockHorizon(value) {
    const raw = String(value || "").trim().toLowerCase();
    if (["short", "short_term", "단기", "intraday", "swing"].includes(raw)) return "short";
    if (["mid", "mid_term", "medium", "중기"].includes(raw)) return "mid";
    if (["long", "long_term", "장기"].includes(raw)) return "long";
    if (["core", "core_etf", "etf", "core/etf", "etf_core"].includes(raw)) return "core_etf";
    if (["cash", "현금"].includes(raw)) return "cash";
    return raw || "short";
  }

  function blockHorizonLabel(value) {
    const key = normalizeBlockHorizon(value);
    if (key === "cash") return "현금";
    return blockHorizons.find((item) => item.key === key)?.label || key || "-";
  }

  function blockHorizonDescription(value) {
    const key = normalizeBlockHorizon(value);
    if (key === "cash") return "대기 현금·기회 준비";
    return blockHorizons.find((item) => item.key === key)?.description || "쥬 판단 대기";
  }

  function blockHorizonClass(value) {
    const key = normalizeBlockHorizon(value);
    return ["short", "mid", "long", "core_etf", "cash"].includes(key) ? key : "short";
  }

  function blockHorizonForBlock(block) {
    return normalizeBlockHorizon(
      block?.horizon
        || block?.block_horizon
        || block?.time_horizon
        || block?.strategy_horizon
        || block?.intent?.horizon
        || block?.metadata?.horizon
        || "short",
    );
  }

  function blockHorizonWeight(
    value,
    numberParser = toNumber,
    numberFormatter = (numeric) => String(numeric),
  ) {
    const parse = typeof numberParser === "function" ? numberParser : toNumber;
    const format = typeof numberFormatter === "function"
      ? numberFormatter
      : (numeric) => String(numeric);
    const numeric = parse(value, 0);
    const pct = Math.abs(numeric) <= 1 ? numeric * 100 : numeric;
    return `${format(pct, Math.abs(pct) < 10 && pct !== 0 ? 1 : 0)}%`;
  }

  function blockDirectiveContext(block) {
    const metadata = block?.metadata && typeof block.metadata === "object"
      ? block.metadata
      : {};
    const directives = Array.isArray(metadata.user_directives)
      ? metadata.user_directives
      : [];
    const latestDirective = metadata.user_directive_latest
      && typeof metadata.user_directive_latest === "object"
      ? metadata.user_directive_latest
      : directives[0] || {};
    const allocationReason = block?.allocation_reason
      || metadata.allocation_reason
      || block?.horizon_reason
      || block?.horizon_note
      || "";
    return {
      metadata,
      directives,
      latestDirective,
      preferredHorizon: metadata.user_preferred_horizon
        || latestDirective.preferred_horizon
        || "",
      allocationReason,
    };
  }

  function escapeText(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\"", "&quot;")
      .replaceAll("'", "&#39;");
  }

  function renderBlockHero(payload, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const accountLine = typeof options.renderAccountCashLine === "function"
      ? options.renderAccountCashLine
      : () => "";
    const summary = payload?.summary || payload || {};
    const clock = summary.clock || {};
    const account = payload?.account || {};
    const kill = summary.kill_switch || {};
    const mode = summary.execution_mode || "-";
    const killLabel = kill.enabled ? "KILL ON" : "정상";
    const ops = options.opsReadiness && typeof options.opsReadiness === "object"
      ? options.opsReadiness
      : {};
    return `
      <section class="block-trader-hero">
        <div>
          <span class="eyebrow">KIS BLOCK TRADER</span>
          <h4>${escape(mode)} · ${escape(clock.session || "-")}</h4>
          <p>${escape(accountLine(account))}</p>
        </div>
        <div class="block-trader-kpis">
          <span><strong>${escape(summary.open_block_count ?? 0)}</strong>활성 블록</span>
          <span><strong>${escape(summary.waiting_entry_block_count ?? 0)}</strong>매수 대기</span>
          <span><strong>${escape(summary.block_count ?? 0)}</strong>전체 블록</span>
          <span><strong>${escape(summary.llm_ready ? "ready" : "off")}</strong>LLM</span>
          <span><strong>${escape(killLabel)}</strong>킬스위치</span>
          <span><strong>${escape(ops.live_trading_enabled ? "LIVE" : "PAPER")}</strong>주문</span>
          <span><strong>${escape(ops.status || "-")}</strong>Readiness</span>
        </div>
      </section>
    `;
  }

  function renderHorizonAllocation(payload, blocks, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const parse = typeof options.asNumber === "function" ? options.asNumber : toNumber;
    const formatNumber = typeof options.fmtNum === "function"
      ? options.fmtNum
      : (numeric) => String(numeric);
    const formatKRW = typeof options.fmtKRW === "function"
      ? options.fmtKRW
      : (numeric) => String(numeric);
    const rows = Array.isArray(blocks) ? blocks : [];
    const items = Array.isArray(payload?.horizon_allocation?.items)
      ? payload.horizon_allocation.items
      : [];
    if (!items.length) {
      const counts = rows.reduce((acc, block) => {
        const key = blockHorizonForBlock(block);
        acc[key] = (acc[key] || 0) + 1;
        return acc;
      }, {});
      if (!rows.length) return "";
      return `
        <section class="block-horizon-allocation">
          <div class="panel-head compact">
            <h3>Horizon 밸런스</h3>
            <p>배정 비중 데이터 대기 · 현재 블록 수 기준</p>
          </div>
          <div class="block-horizon-allocation-grid">
            ${blockHorizons.map((item) => `
              <div class="horizon-allocation-card ${escape(item.key)}">
                <span>${escape(item.label)}</span>
                <strong>${escape(counts[item.key] || 0)}개</strong>
                <small>${escape(item.description)}</small>
              </div>
            `).join("")}
          </div>
        </section>
      `;
    }
    return `
      <section class="block-horizon-allocation">
        <div class="panel-head compact">
          <h3>Horizon 밸런스</h3>
          <p>쥬가 관리하는 현금·단기·중기·장기·ETF/Core 배정</p>
        </div>
        <div class="block-horizon-allocation-grid">
          ${items.map((row) => {
            const horizon = normalizeBlockHorizon(row.horizon || row.key || row.type);
            const horizonClass = blockHorizonClass(horizon);
            const actual = row.actual_weight ?? row.weight ?? row.current_weight ?? 0;
            const target = row.target_weight ?? row.target ?? null;
            const value = row.current_value_krw ?? row.value_krw ?? row.open_value_krw ?? row.cash_krw ?? null;
            return `
              <div class="horizon-allocation-card ${escape(horizonClass)}">
                <span>${escape(row.label || blockHorizonLabel(horizon))}</span>
                <strong>${escape(blockHorizonWeight(actual, parse, formatNumber))}</strong>
                <small>${target === null ? "목표 비중 대기" : `목표 ${blockHorizonWeight(target, parse, formatNumber)}`}${value === null ? "" : ` · ${formatKRW(value)}원`}</small>
              </div>
            `;
          }).join("")}
        </div>
      </section>
    `;
  }

  function renderBlockCard(block, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const parse = typeof options.asNumber === "function" ? options.asNumber : toNumber;
    const formatKRW = typeof options.fmtKRW === "function"
      ? options.fmtKRW
      : (numeric) => String(numeric);
    const formatMaybeKRW = typeof options.fmtMaybeKRW === "function"
      ? options.fmtMaybeKRW
      : formatKRW;
    const formatPercent = typeof options.fmtPercent === "function"
      ? options.fmtPercent
      : (numeric) => `${String(numeric)}%`;
    const renderBlockValidationChips = typeof options.renderBlockValidationChips === "function"
      ? options.renderBlockValidationChips
      : () => "";
    const renderValidationPassportChips = typeof options.renderValidationPassportChips === "function"
      ? options.renderValidationPassportChips
      : () => "";
    const renderBlockCostFeasibilityChips = typeof options.renderBlockCostFeasibilityChips === "function"
      ? options.renderBlockCostFeasibilityChips
      : () => "";
    const renderBlockPolicyEffectChips = typeof options.renderBlockPolicyEffectChips === "function"
      ? options.renderBlockPolicyEffectChips
      : () => "";
    const row = block || {};
    const tone = blockTone(row.status);
    const horizon = blockHorizonForBlock(row);
    const horizonClass = blockHorizonClass(horizon);
    const pnl = parse(row.unrealized_pnl_krw, 0);
    const quote = row.quote || {};
    const target = parse(row.target_price, 0);
    const stop = parse(row.stop_price, 0);
    const reflection = row.reflection_status || {};
    const policyImpacts = Array.isArray(row.policy_impacts) ? row.policy_impacts : [];
    const directiveContext = blockDirectiveContext(row);
    const metadata = directiveContext.metadata || {};
    const performance = row.performance && typeof row.performance === "object" ? row.performance : {};
    const givebackPct = parse(performance.giveback_pct, 0);
    const mfePct = parse(performance.mfe_pct, 0);
    const currentPnlPct = parse(performance.current_pnl_pct, 0);
    const decisionClass = metadata.decision_class || "";
    const stopPolicy = metadata.stop_policy || "";
    const maxLoss = parse(metadata.max_loss_krw, 0);
    const mindChange = metadata.what_would_change_my_mind || "";
    const allocationReason = directiveContext.allocationReason || "";
    const latestDirective = directiveContext.latestDirective || {};
    const preferredHorizon = directiveContext.preferredHorizon || "";
    const isWaitingEntry = String(row.status || "") === "proposed";
    const triggerPrice = parse(metadata.entry_trigger_price || row.entry_price, 0);
    const triggerOperator = String(metadata.entry_trigger_operator || "lte");
    const triggerLabel = triggerOperator === "gte" ? "이상" : "이하";
    const triggerStatus = metadata.entry_trigger_status || "";
    const ruleMode = isWaitingEntry
      ? "매수 조건 감시"
      : row.rule_exit_mode || (horizon === "short" ? "목표/손절 룰 감시" : "30분 매니저 검토");
    const fillProvenance = String(
      metadata.fill_provenance
      || metadata.fill_source
      || row.fill_provenance
      || row.fill_source
      || row.execution_source
      || ""
    ).toLowerCase();
    const provenanceChips = [
      row.created_by === "existing_position"
        ? '<span class="strategy-data-chip warn">기존 보유 채택 · 쥬 진입 성과 제외</span>'
        : "",
      fillProvenance.includes("exchange") || fillProvenance === "live_fill"
        ? '<span class="strategy-data-chip good">거래소 체결</span>'
        : fillProvenance.includes("paper")
          ? '<span class="strategy-data-chip neutral">Paper 체결</span>'
          : "",
      ["failed", "failed_entry", "rejected", "error"].includes(String(row.status || "").toLowerCase())
        ? '<span class="strategy-data-chip bad">진입 실패 · 체결 없음</span>'
        : "",
    ].join("");
    return `
    <article class="block-card ${escape(tone)} horizon-${escape(horizonClass)}" data-kis-block-id="${escape(row.block_id || "")}">
      <div class="block-card-head">
        <div>
          <h4>${escape(row.name || row.symbol || "-")} <span>${escape(row.symbol || "-")}</span></h4>
          <p class="mono">${escape(row.block_id || "-")}</p>
        </div>
        <div class="block-status-stack">
          <span class="block-status">${escape(blockStatusLabel(row.status))}</span>
          <span class="strategy-data-chip horizon-chip ${escape(horizonClass)}">${escape(blockHorizonLabel(horizon))}</span>
        </div>
      </div>
      <div class="block-price-grid">
        <span><b>${escape(formatKRW(row.qty_open || row.qty_initial))}</b>주</span>
        <span><b>${escape(formatMaybeKRW(row.entry_price))}</b>진입</span>
        <span><b>${escape(formatMaybeKRW(row.current_price || quote.price))}</b>현재</span>
        <span><b>${escape(formatMaybeKRW(target))}</b>목표</span>
        <span><b>${escape(formatMaybeKRW(stop))}</b>손절</span>
        <span class="${pnl >= 0 ? "gain" : "loss"}"><b>${escape(formatKRW(pnl))}</b>PnL</span>
      </div>
      ${
        Object.keys(performance).length
          ? `<div class="block-performance-strip">
              <span>MFE <b>${escape(formatPercent(mfePct, 1))}</b></span>
              <span>반납 <b>${escape(formatPercent(givebackPct, 1))}</b></span>
              <span class="${currentPnlPct >= 0 ? "gain" : "loss"}">현재 <b>${escape(formatPercent(currentPnlPct, 1))}</b></span>
            </div>`
          : ""
      }
      <p class="helper-text">${escape(row.thesis || row.llm_reason || "블록 운용 근거 대기")}</p>
      ${allocationReason ? `<p class="helper-text block-allocation-reason">${escape(allocationReason)}</p>` : ""}
      <div class="block-card-actions">
        <span class="strategy-data-chip neutral">${escape(ruleMode)}</span>
        <span class="strategy-data-chip">${escape(row.next_rule_action || "watch")}</span>
        ${isWaitingEntry && triggerPrice > 0 ? `<span class="block-chip entry-watch">매수 ${escape(formatMaybeKRW(triggerPrice))} ${escape(triggerLabel)}</span>` : ""}
        ${triggerStatus ? `<span class="strategy-data-chip neutral">${escape(triggerStatus)}</span>` : ""}
        ${decisionClass ? `<span class="block-chip decision">${escape(decisionClass)}</span>` : ""}
        ${stopPolicy ? `<span class="block-chip stop-policy">${escape(stopPolicy)}</span>` : ""}
        ${maxLoss > 0 ? `<span class="block-chip risk">최대손실 ${escape(formatKRW(maxLoss))}</span>` : ""}
        ${provenanceChips}
        ${renderBlockValidationChips(metadata)}
        ${renderValidationPassportChips(metadata)}
        ${renderBlockCostFeasibilityChips(metadata)}
        ${renderBlockPolicyEffectChips(metadata)}
        <span class="strategy-data-chip ${reflection.status === "reflected" ? "good" : "neutral"}">${escape(reflection.status === "reflected" ? "반성 완료" : reflection.status === "pending" ? "반성 대기" : "반성 미대상")}</span>
        ${policyImpacts.slice(0, 2).map((item) => `<span class="strategy-data-chip warn">${escape(item.rule_id || item.policy_id || "policy")}</span>`).join("")}
        ${preferredHorizon ? `<span class="strategy-data-chip good">사용자 ${escape(blockHorizonLabel(preferredHorizon))}</span>` : ""}
        ${
          row.status === "open"
            ? `<button class="btn small" type="button" data-block-action="close" data-block-id="${escape(row.block_id)}">청산 요청</button>
               <button class="btn small ghost" type="button" data-block-action="pause" data-block-id="${escape(row.block_id)}">정지</button>`
            : ""
        }
        ${
          row.status === "proposed"
            ? `<button class="btn small" type="button" data-block-action="close" data-block-id="${escape(row.block_id)}">대기 취소</button>
               <button class="btn small ghost" type="button" data-block-action="pause" data-block-id="${escape(row.block_id)}">정지</button>`
            : ""
        }
        ${
          row.status === "paused"
            ? `<button class="btn small" type="button" data-block-action="resume" data-block-id="${escape(row.block_id)}">재개</button>`
            : ""
        }
      </div>
      ${mindChange ? `<div class="block-decision-note"><span>판단 변경 조건</span><strong>${escape(mindChange)}</strong></div>` : ""}
      <div class="block-directive-panel">
        ${
          latestDirective.message
            ? `<p class="block-directive-latest">최근 의견: ${escape(latestDirective.message)}</p>`
            : `<p class="block-directive-latest">쥬에게 이 블록의 의도와 기간을 바로 남길 수 있습니다.</p>`
        }
        <div class="block-directive-controls">
          <select data-block-directive-horizon aria-label="블록 선호 기간">
            <option value="">기간 유지</option>
            <option value="short" ${preferredHorizon === "short" ? "selected" : ""}>단기</option>
            <option value="mid" ${preferredHorizon === "mid" ? "selected" : ""}>중기</option>
            <option value="long" ${preferredHorizon === "long" ? "selected" : ""}>장기</option>
            <option value="core_etf" ${preferredHorizon === "core_etf" ? "selected" : ""}>ETF/Core</option>
          </select>
          <textarea data-block-directive-message rows="2" placeholder="예: 오늘 산 주식들은 단기보다는 중기로 다뤄줘."></textarea>
          <button class="btn small" type="button" data-block-directive-save data-block-id="${escape(row.block_id)}">쥬에게 전달</button>
        </div>
      </div>
    </article>
  `;
  }

  function renderHorizonBlockGroups(blocks, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const horizonFn = typeof options.horizonFn === "function" ? options.horizonFn : blockHorizonForBlock;
    const renderCard = typeof options.renderBlockCard === "function"
      ? options.renderBlockCard
      : (block) => renderBlockCard(block, options);
    const rows = Array.isArray(blocks) ? blocks : [];
    if (!rows.length) {
      return '<div class="notice">아직 등록된 블록이 없습니다. LLM 매니저 실행 후 블록 제안을 확인하세요.</div>';
    }
    const grouped = rows.reduce((acc, block) => {
      const key = horizonFn(block);
      if (!acc[key]) acc[key] = [];
      acc[key].push(block);
      return acc;
    }, {});
    return `
      <section class="block-horizon-board">
        ${blockHorizons.map((item) => {
          const horizonRows = grouped[item.key] || [];
          return `
            <div class="block-horizon-column ${escape(item.key)}">
              <div class="block-horizon-column-head">
                <div>
                  <h4>${escape(item.label)}</h4>
                  <p>${escape(item.description)}</p>
                </div>
                <span>${escape(horizonRows.length)}개</span>
              </div>
              <div class="block-horizon-list">
                ${horizonRows.length ? horizonRows.map(renderCard).join("") : '<div class="notice compact">블록 없음</div>'}
              </div>
            </div>
          `;
        }).join("")}
      </section>
    `;
  }

  function renderBlockHistoryRow(payload, block, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const parse = typeof options.asNumber === "function" ? options.asNumber : toNumber;
    const formatKRW = typeof options.fmtKRW === "function"
      ? options.fmtKRW
      : (numeric) => String(numeric);
    const formatMaybeKRW = typeof options.fmtMaybeKRW === "function"
      ? options.fmtMaybeKRW
      : formatKRW;
    const formatNumber = typeof options.fmtNum === "function"
      ? options.fmtNum
      : (numeric) => String(numeric);
    const formatKST = typeof options.fmtKST === "function"
      ? options.fmtKST
      : (value) => String(value || "");
    const truncate = typeof options.truncateWithEllipsis === "function"
      ? options.truncateWithEllipsis
      : (value) => String(value || "");
    const horizon = blockHorizonForBlock(block);
    const horizonClass = blockHorizonClass(horizon);
    const timeline = timelineDateValue(block);
    const pnl = historyPnl(payload, block, parse);
    const selected = String(options.selectedBlockId || "") === String(block?.block_id || "");
    const tone = blockTone(block?.status);
    const eventCount = Array.isArray(payload?.events)
      ? payload.events.filter((row) => String(row?.block_id || "") === String(block?.block_id || "")).length
      : 0;
    return `
      <button class="block-history-row ${escape(tone)} ${selected ? "active" : ""}" type="button" data-block-history-select="${escape(block?.block_id || "")}">
        <div class="block-history-id">
          <strong>${escape(block?.name || block?.symbol || "-")}</strong>
          <span class="mono">${escape(block?.symbol || "-")} · ${escape(block?.block_id || "-")}</span>
        </div>
        <div class="block-history-meta">
          <span class="strategy-data-chip ${escape(tone)}">${escape(blockStatusLabel(block?.status))}</span>
          <span class="strategy-data-chip horizon-chip ${escape(horizonClass)}">${escape(blockHorizonLabel(horizon))}</span>
          ${block?.created_by === "existing_position" ? '<span class="strategy-data-chip neutral">기존 보유</span>' : ""}
          ${eventCount ? `<span class="strategy-data-chip neutral">event ${escape(String(eventCount))}</span>` : ""}
        </div>
        <div class="block-history-numbers">
          <span><b>${escape(formatKRW(block?.qty_initial || block?.qty_open))}</b>주</span>
          <span><b>${escape(formatMaybeKRW(block?.entry_price))}</b>진입</span>
          <span><b>${escape(pnl.exitPrice ? formatKRW(pnl.exitPrice) : formatMaybeKRW(block?.current_price || block?.quote?.price))}</b>${String(block?.status || "") === "closed" ? "청산/현재" : "현재"}</span>
          <span class="${pnl.pnl >= 0 ? "gain" : "loss"}"><b>${pnl.pnl >= 0 ? "+" : ""}${escape(formatKRW(pnl.pnl))}</b>${pnl.pct >= 0 ? "+" : ""}${escape(formatNumber(pnl.pct, 2))}%</span>
        </div>
        <p>${escape(truncate(block?.thesis || block?.llm_reason || block?.risk_note || "블록 근거 대기", 140))}</p>
        <small>${escape(timeline ? formatKST(timeline, true) : "--")}</small>
      </button>
    `;
  }

  function renderBlockHistoryDetail(payload, block, options = {}) {
    if (!block) return "";
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const parse = typeof options.asNumber === "function" ? options.asNumber : toNumber;
    const formatKRW = typeof options.fmtKRW === "function"
      ? options.fmtKRW
      : (numeric) => String(numeric);
    const formatMaybeKRW = typeof options.fmtMaybeKRW === "function"
      ? options.fmtMaybeKRW
      : formatKRW;
    const formatKST = typeof options.fmtKST === "function"
      ? options.fmtKST
      : (value) => String(value || "");
    const renderBlockValidationChips = typeof options.renderBlockValidationChips === "function"
      ? options.renderBlockValidationChips
      : () => "";
    const renderValidationPassportChips = typeof options.renderValidationPassportChips === "function"
      ? options.renderValidationPassportChips
      : () => "";
    const renderBlockCostFeasibilityChips = typeof options.renderBlockCostFeasibilityChips === "function"
      ? options.renderBlockCostFeasibilityChips
      : () => "";
    const renderBlockPolicyEffectChips = typeof options.renderBlockPolicyEffectChips === "function"
      ? options.renderBlockPolicyEffectChips
      : () => "";
    const pnl = historyPnl(payload, block, parse);
    const orders = Array.isArray(payload?.orders)
      ? payload.orders.filter((row) => String(row?.block_id || "") === String(block?.block_id || "")).slice(0, 6)
      : [];
    const events = Array.isArray(payload?.events)
      ? payload.events.filter((row) => String(row?.block_id || "") === String(block?.block_id || "")).slice(0, 6)
      : [];
    const reflection = block.reflection_status || {};
    const metadata = block.metadata && typeof block.metadata === "object" ? block.metadata : {};
    const decisionClass = metadata.decision_class || "";
    const stopPolicy = metadata.stop_policy || "";
    const maxLoss = parse(metadata.max_loss_krw, 0);
    const mindChange = metadata.what_would_change_my_mind || "";
    return `
      <aside class="block-history-detail">
        <div class="helper-row-head">
          <div>
            <span class="eyebrow">BLOCK DETAIL</span>
            <h4>${escape(block.name || block.symbol || "-")} <span>${escape(block.symbol || "-")}</span></h4>
          </div>
          <span class="helper-row-status ${escape(blockTone(block.status))}">${escape(blockStatusLabel(block.status))}</span>
        </div>
        <div class="block-history-detail-grid">
          <span><b>${escape(formatMaybeKRW(block.entry_price))}</b>진입가</span>
          <span><b>${escape(formatMaybeKRW(block.target_price))}</b>목표가</span>
          <span><b>${escape(formatMaybeKRW(block.stop_price))}</b>손절가</span>
          <span class="${pnl.pnl >= 0 ? "gain" : "loss"}"><b>${pnl.pnl >= 0 ? "+" : ""}${escape(formatKRW(pnl.pnl))}</b>손익 추정</span>
        </div>
        <div class="block-history-time-grid">
          <span>생성 ${escape(block.created_at ? formatKST(block.created_at, true) : "--")}</span>
          <span>오픈 ${escape(block.opened_at ? formatKST(block.opened_at, true) : "--")}</span>
          <span>종료 ${escape(block.closed_at ? formatKST(block.closed_at, true) : "--")}</span>
          <span>반성 ${escape(reflection.status || "not_due")}</span>
        </div>
        <div class="block-card-actions compact">
          ${decisionClass ? `<span class="block-chip decision">${escape(decisionClass)}</span>` : ""}
          ${stopPolicy ? `<span class="block-chip stop-policy">${escape(stopPolicy)}</span>` : ""}
          ${maxLoss > 0 ? `<span class="block-chip risk">최대손실 ${escape(formatKRW(maxLoss))}</span>` : ""}
          ${renderBlockValidationChips(metadata)}
          ${renderValidationPassportChips(metadata)}
          ${renderBlockCostFeasibilityChips(metadata)}
          ${renderBlockPolicyEffectChips(metadata)}
        </div>
        ${mindChange ? `<div class="block-decision-note"><span>판단 변경 조건</span><strong>${escape(mindChange)}</strong></div>` : ""}
        <p class="helper-text">${escape(block.thesis || block.llm_reason || "블록 thesis 없음")}</p>
        ${block.risk_note ? `<p class="helper-text block-allocation-reason">${escape(block.risk_note)}</p>` : ""}
        <div class="block-history-subgrid">
          <div>
            <strong>주문</strong>
            <ul class="helper-plain-list">
              ${orders.length ? orders.map((row) => `
                <li>${escape(`${row.side} ${row.qty}주 @ ${formatKRW(row.avg_fill_price || row.limit_price)} · ${row.status} · ${row.reason || "-"}`)}</li>
              `).join("") : "<li>주문 기록 없음</li>"}
            </ul>
          </div>
          <div>
            <strong>이벤트</strong>
            <ul class="helper-plain-list">
              ${events.length ? events.map((row) => `
                <li>${escape(`${formatKST(row.created_at, true)} · ${row.event_type} · ${row.message}`)}</li>
              `).join("") : "<li>이벤트 기록 없음</li>"}
            </ul>
          </div>
        </div>
      </aside>
    `;
  }

  function renderBlockHistoryBoard(payload, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const formatKRW = typeof options.fmtKRW === "function"
      ? options.fmtKRW
      : (numeric) => String(numeric);
    const historyState = options.historyState && typeof options.historyState === "object"
      ? options.historyState
      : {};
    const blocks = Array.isArray(payload?.blocks) ? payload.blocks : [];
    if (!blocks.length) {
      return "";
    }
    const dates = historyDates(blocks);
    let selectedDate = "";
    if (!dates.length) {
      historyState.date = "";
    } else if (!historyState.date || !dates.includes(historyState.date)) {
      historyState.date = dates[0];
    }
    selectedDate = historyState.date || "";
    const dayBlocks = blocks.filter((block) => !selectedDate || timelineDate(block) === selectedDate);
    const rows = filteredHistoryBlocks(
      payload,
      {
        date: selectedDate,
        query: historyState.query,
        status: historyState.status || "inactive",
        horizon: historyState.horizon || "all",
      },
      { horizonFn: blockHorizonForBlock },
    );
    if (!historyState.selectedBlockId && rows.length) {
      historyState.selectedBlockId = String(rows[0].block_id || "");
    }
    if (
      historyState.selectedBlockId
      && !rows.some((row) => String(row.block_id || "") === historyState.selectedBlockId)
    ) {
      historyState.selectedBlockId = rows.length ? String(rows[0].block_id || "") : "";
    }
    const selectedBlock = rows.find((row) => String(row.block_id || "") === historyState.selectedBlockId);
    const summary = daySummary(payload, dayBlocks, options.asNumber);
    const selectedIndex = Math.max(dates.indexOf(selectedDate), 0);
    const prevDisabled = selectedIndex >= dates.length - 1 ? "disabled" : "";
    const nextDisabled = selectedIndex <= 0 ? "disabled" : "";
    const statusOptions = [
      ["inactive", "종료/비활성"],
      ["closed", "종료"],
      ["error", "오류"],
      ["active", "활성 포함"],
      ["all", "전체"],
    ];
    const horizonOptions = [["all", "전체"], ...blockHorizons.map((item) => [item.key, item.label])];
    return `
      <section class="block-history-board">
        <div class="panel-head compact">
          <div>
            <h3>블록 히스토리</h3>
            <p>날짜별 블록 원장 · 종료/오류/반성 흐름 확인</p>
          </div>
          <div class="block-history-date-controls">
            <button class="btn small ghost" type="button" data-block-history-action="prev-date" ${prevDisabled}>이전일</button>
            <input id="blockHistoryDate" type="date" value="${escape(selectedDate)}" />
            <button class="btn small ghost" type="button" data-block-history-action="next-date" ${nextDisabled}>다음일</button>
          </div>
        </div>
        <div class="block-history-kpis">
          <span><strong>${escape(String(summary.total))}</strong>당일 블록</span>
          <span><strong>${escape(String(summary.closed))}</strong>종료</span>
          <span><strong>${escape(String(summary.active))}</strong>활성</span>
          <span><strong>${escape(String(summary.error))}</strong>오류</span>
          <span class="${summary.pnl >= 0 ? "gain" : "loss"}"><strong>${summary.pnl >= 0 ? "+" : ""}${escape(formatKRW(summary.pnl))}</strong>손익 추정</span>
        </div>
        <div class="block-history-filters">
          <input id="blockHistoryQuery" type="search" placeholder="종목명, 코드, 블록ID 검색" value="${escape(historyState.query || "")}" />
          <div class="helper-chip-row">
            ${statusOptions.map(([key, label]) => `
              <button class="strategy-data-chip ${(historyState.status || "inactive") === key ? "good" : "neutral"}" type="button" data-block-history-status="${escape(key)}">${escape(label)}</button>
            `).join("")}
          </div>
          <div class="helper-chip-row">
            ${horizonOptions.map(([key, label]) => `
              <button class="strategy-data-chip ${(historyState.horizon || "all") === key ? "good" : "neutral"}" type="button" data-block-history-horizon="${escape(key)}">${escape(label)}</button>
            `).join("")}
          </div>
        </div>
        <div class="block-history-layout">
          <div class="block-history-list">
            ${rows.length ? rows.map((block) => renderBlockHistoryRow(payload, block, { ...options, selectedBlockId: historyState.selectedBlockId })).join("") : '<div class="notice compact">선택한 조건의 블록 기록이 없습니다.</div>'}
          </div>
          ${renderBlockHistoryDetail(payload, selectedBlock, options)}
        </div>
      </section>
    `;
  }

  function renderBlockAllocation(payload, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const parse = typeof options.asNumber === "function" ? options.asNumber : toNumber;
    const formatKRW = typeof options.fmtKRW === "function"
      ? options.fmtKRW
      : (numeric) => String(numeric);
    const allRows = Array.isArray(payload?.allocation?.items) ? payload.allocation.items : [];
    const rows = allRows.filter((row) => (
      parse(row.account_qty, 0) > 0
      || parse(row.block_qty, 0) > 0
      || parse(row.unallocated_qty, 0) > 0
      || parse(row.overallocated_qty, 0) > 0
    ));
    const hiddenZeroRows = Math.max(allRows.length - rows.length, 0);
    return `
    <article class="helper-card helper-card-wide">
      <div class="panel-head compact">
        <div>
          <h4>계좌/블록 배정</h4>
          ${hiddenZeroRows > 0 ? `<p class="helper-text">0수량 후보 ${escape(String(hiddenZeroRows))}개 숨김</p>` : ""}
        </div>
      </div>
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
                    <td>${escape(`${row.name || row.symbol} (${row.symbol})`)}</td>
                    <td>${escape(formatKRW(row.account_qty))}</td>
                    <td>${escape(formatKRW(row.block_qty))}</td>
                    <td>${escape(formatKRW(row.unallocated_qty))}</td>
                    <td class="${parse(row.overallocated_qty, 0) > 0 ? "loss" : ""}">${escape(formatKRW(row.overallocated_qty))}</td>
                  </tr>
                `).join("")
                : `<tr><td colspan="5">${allRows.length ? "실제 잔고/블록 배정이 있는 종목이 없습니다." : "배정 데이터가 없습니다."}</td></tr>`
            }
          </tbody>
        </table>
      </div>
    </article>
  `;
  }

  function renderBlockEventFeed(payload, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const formatKRW = typeof options.fmtKRW === "function"
      ? options.fmtKRW
      : (numeric) => String(numeric);
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
              ? ` · 체결 ${formatKRW(row.filled_qty)} / 잔여 ${formatKRW(row.remaining_qty)}`
              : "";
            const cancelButton = pendingStatuses.has(String(row.status || ""))
              ? ` <button class="btn tiny ghost" type="button" data-block-action="cancel-order" data-order-id="${escape(row.id)}">미체결 취소</button>`
              : "";
            return `<span>${escape(`${row.side} ${row.symbol} ${row.qty}주 @ ${formatKRW(row.limit_price)} · ${row.status}${fillText}`)}</span>${cancelButton}`;
          }),
            ...events.map((row) => escape(`${row.event_type} · ${row.message}`))]
            .slice(0, 10)
            .map((item) => `<li>${item}</li>`)
            .join("") || "<li>이벤트 없음</li>"
        }
      </ul>
    </article>
  `;
  }

  function renderBlockManagerRun(payload, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const formatKST = typeof options.fmtKST === "function"
      ? options.fmtKST
      : (value) => String(value || "");
    const stringify = typeof options.stringifySafe === "function"
      ? options.stringifySafe
      : (value) => JSON.stringify(value, null, 2);
    const run = payload?.latest_manager_run || {};
    const actions = run.actions || {};
    const count = ["adopt_existing_blocks", "create_blocks", "update_blocks", "close_blocks", "pause_blocks"]
      .reduce((acc, key) => acc + (Array.isArray(actions[key]) ? actions[key].length : 0), 0);
    return `
    <article class="helper-card">
      <h4>LLM 매니저</h4>
      <p class="helper-text">최근 실행 ${escape(run.run_at ? formatKST(run.run_at, true) : "--")} · ${escape(run.status || "missing")} · 액션 ${escape(count)}</p>
      <pre class="helper-json mono">${escape(stringify(actions, true))}</pre>
    </article>
  `;
  }

  function renderKisHoldDecisionDetailText({ latest, hold, summary }, options = {}) {
    const formatNumber = typeof options.fmtNum === "function"
      ? options.fmtNum
      : (numeric) => String(numeric);
    const formatKST = typeof options.fmtKST === "function"
      ? options.fmtKST
      : (value) => String(value || "");
    const horizonNotes = hold?.horizon_notes && typeof hold.horizon_notes === "object"
      ? hold.horizon_notes
      : {};
    const missed = Array.isArray(hold?.missed_upside_reviews) ? hold.missed_upside_reviews : [];
    const section = (title, rows, formatter) => {
      if (!Array.isArray(rows) || !rows.length) return `${title} (0)\n- 없음`;
      return `${title} (${rows.length})\n${rows.map((row, index) => `${index + 1}. ${formatter(row)}`).join("\n")}`;
    };
    const triggerLine = (row) => {
      if (!row || typeof row !== "object") return String(row || "-");
      return [
        row.symbol || "-",
        row.horizon || "-",
        row.price ? `@ ${formatNumber(row.price, 0)}` : "",
        row.condition || "조건 감시",
        row.reason || "",
      ].filter(Boolean).join(" · ");
    };
    const missedLine = (row) => {
      if (!row || typeof row !== "object") return String(row || "-");
      return [
        `${row.name || row.symbol || "-"}(${row.symbol || "-"})`,
        `exit ${row.exit_price ? formatNumber(row.exit_price, 0) : "-"}`,
        `now ${row.current_price ? formatNumber(row.current_price, 0) : "-"}`,
        `+${formatNumber(Number(row.upside_after_exit_pct || 0), 2)}%`,
        row.lesson || "",
      ].filter(Boolean).join(" · ");
    };
    const horizonLine = (key) => section(`${key} 노트`, horizonNotes[key] || [], (row) => String(row || "-"));
    return [
      `판단 시각: ${latest?.run_at ? formatKST(latest.run_at, true) : "-"}`,
      `모델: ${latest?.model || "-"}`,
      `상태: ${latest?.status || "-"}`,
      `액션 수: ${formatNumber(Number(hold?.action_count || 0), 0)}`,
      "",
      "요약",
      summary || "-",
      "",
      section("관망 이유", hold?.reasons || [], (row) => String(row || "-")),
      "",
      section("watch symbols", hold?.watch_symbols || [], (row) => String(row || "-")),
      "",
      section("long watch symbols", hold?.long_watch_symbols || [], (row) => String(row || "-")),
      "",
      section("다음 트리거", hold?.next_triggers || [], triggerLine),
      "",
      horizonLine("short"),
      "",
      horizonLine("mid"),
      "",
      horizonLine("long"),
      "",
      horizonLine("core_etf"),
      "",
      horizonLine("cash"),
      "",
      section("missed upside review", missed, missedLine),
      "",
      section("데이터 공백", hold?.data_gaps || [], (row) => String(row || "-")),
      "",
      section("리스크 노트", hold?.risk_notes || [], (row) => String(row || "-")),
    ].join("\n");
  }

  function renderKisHoldDecision(payload, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const formatNumber = typeof options.fmtNum === "function"
      ? options.fmtNum
      : (numeric) => String(numeric);
    const formatKST = typeof options.fmtKST === "function"
      ? options.fmtKST
      : (value) => String(value || "");
    const registerDetail = typeof options.registerHelperDetail === "function"
      ? options.registerHelperDetail
      : () => "";
    const latest = payload?.latest_manager_run || {};
    const response = latest.response && typeof latest.response === "object" ? latest.response : {};
    const hold = latest.hold_decision && typeof latest.hold_decision === "object"
      ? latest.hold_decision
      : (response.hold_decision && typeof response.hold_decision === "object" ? response.hold_decision : {});
    const summary = hold.summary || "아직 저장된 KIS 관망 판단이 없습니다.";
    const watchSymbols = Array.isArray(hold.watch_symbols) ? hold.watch_symbols : [];
    const longWatchSymbols = Array.isArray(hold.long_watch_symbols) ? hold.long_watch_symbols : [];
    const triggers = Array.isArray(hold.next_triggers) ? hold.next_triggers : [];
    const reasons = Array.isArray(hold.reasons) ? hold.reasons : [];
    const missed = Array.isArray(hold.missed_upside_reviews) ? hold.missed_upside_reviews : [];
    const horizonNotes = hold.horizon_notes && typeof hold.horizon_notes === "object" ? hold.horizon_notes : {};
    const longNotes = Array.isArray(horizonNotes.long) ? horizonNotes.long : [];
    const primaryWatchSymbols = longWatchSymbols.length ? longWatchSymbols : watchSymbols;
    const visibleWatchSymbols = primaryWatchSymbols.slice(0, 8);
    const hiddenWatchCount = Math.max(primaryWatchSymbols.length - visibleWatchSymbols.length, 0);
    const detailId = registerDetail({
      title: "KIS 쥬 관망 노트 전체보기",
      subtitle: "KIS Manager Hold Decision",
      body: renderKisHoldDecisionDetailText({ latest, hold, summary }, options),
      meta: [
        latest.run_at ? formatKST(latest.run_at, true) : "판단 대기",
        `watch ${watchSymbols.length}`,
        `long ${longWatchSymbols.length}`,
        `missed ${missed.length}`,
      ],
    });
    const triggerRows = triggers.slice(0, 4).map((row) => `
      <div>
        <span>${escape(`${row.horizon || "watch"} · ${row.symbol || "-"}`)}</span>
        <strong>${escape(row.price ? `${formatNumber(row.price, 0)} · ${row.condition || "조건 감시"}` : row.condition || "조건 감시")}</strong>
        ${row.reason ? `<small class="helper-text">${escape(row.reason)}</small>` : ""}
      </div>
    `).join("");
    return `
      <section class="memory-section binance-edge-panel">
        <div class="panel-head compact">
          <div>
            <h3>KIS 쥬 관망 노트</h3>
            <p>${escape(latest.run_at ? `${formatKST(latest.run_at)} 판단 · 액션 ${formatNumber(Number(hold.action_count || 0), 0)}개` : "최근 매니저 판단 대기")}</p>
          </div>
          <button class="btn tiny ghost" type="button" data-helper-detail-id="${escape(detailId)}">전체보기</button>
        </div>
        <p class="helper-text">${escape(summary)}</p>
        <div class="strategy-chip-row">
          <span class="strategy-data-chip neutral">watch ${escape(String(watchSymbols.length))}</span>
          <span class="strategy-data-chip good">long ${escape(String(longWatchSymbols.length))}</span>
          <span class="strategy-data-chip warn">missed-upside ${escape(String(missed.length))}</span>
          <span class="strategy-data-chip neutral">trigger ${escape(String(triggers.length))}</span>
        </div>
        <div class="strategy-chip-row">
          ${visibleWatchSymbols.map((symbol) => `<span class="strategy-data-chip ${longWatchSymbols.length ? "good" : ""}">${escape(symbol)}</span>`).join("")
            || '<span class="strategy-data-chip">watch 대기</span>'}
          ${hiddenWatchCount ? `<span class="strategy-data-chip neutral">+${escape(String(hiddenWatchCount))}</span>` : ""}
        </div>
        ${longNotes.length ? `
          <div class="helper-card">
            <h4>장기축적 노트</h4>
            <p class="helper-text">${escape(longNotes.slice(0, 3).join(" · "))}</p>
          </div>
        ` : ""}
        ${reasons.length ? `
          <div class="helper-card">
            <h4>관망 이유</h4>
            <p class="helper-text">${escape(reasons.slice(0, 4).join(" · "))}</p>
          </div>
        ` : ""}
        ${triggerRows ? `<div class="binance-edge-grid">${triggerRows}</div>` : ""}
        ${missed.length ? `
          <div class="helper-card">
            <h4>놓친 상승 반성</h4>
            <p class="helper-text">${escape(missed.slice(0, 3).map((row) => `${row.name || row.symbol || "-"} +${formatNumber(Number(row.upside_after_exit_pct || 0), 2)}%`).join(" · "))}</p>
          </div>
        ` : ""}
      </section>
    `;
  }

  function renderKisCreativeHypothesesDetailText({ latest, hypotheses }, options = {}) {
    const formatNumber = typeof options.fmtNum === "function"
      ? options.fmtNum
      : (numeric) => String(numeric);
    const formatKST = typeof options.fmtKST === "function"
      ? options.fmtKST
      : (value) => String(value || "");
    const rows = Array.isArray(hypotheses) ? hypotheses : [];
    if (!rows.length) {
      return [
        `판단 시각: ${latest?.run_at ? formatKST(latest.run_at, true) : "-"}`,
        "저장된 창의적 가설이 아직 없습니다.",
      ].join("\n");
    }
    const blockLine = (block) => {
      if (!block || typeof block !== "object" || !Object.keys(block).length) return "제안 블록 없음";
      return [
        block.symbol || "-",
        block.horizon || "-",
        block.entry_style || "-",
        block.entry_trigger_price ? `trigger ${formatNumber(block.entry_trigger_price, 0)}` : "",
        block.entry_trigger_operator || "",
        block.target_price ? `target ${formatNumber(block.target_price, 0)}` : "",
        block.stop_price ? `stop ${formatNumber(block.stop_price, 0)}` : "",
        block.reason || "",
      ].filter(Boolean).join(" · ");
    };
    return [
      `판단 시각: ${latest?.run_at ? formatKST(latest.run_at, true) : "-"}`,
      `모델: ${latest?.model || "-"}`,
      `가설 수: ${rows.length}`,
      "",
      ...rows.map((row, index) => [
        `${index + 1}. ${row.title || row.summary || row.hypothesis_type || "가설"}`,
        `유형/결정: ${row.hypothesis_type || "-"} · ${row.decision || "-"}`,
        `심볼/섹터/기간: ${(row.symbols || []).join(", ") || "-"} · ${row.sector || "-"} · ${row.horizon || "-"}`,
        `확신: ${formatNumber(Number(row.confidence || 0) * 100, 0)}%`,
        `요약: ${row.summary || "-"}`,
        `근거: ${(row.evidence || []).join(" / ") || "-"}`,
        `리스크: ${(row.risks || []).join(" / ") || "-"}`,
        `무효화: ${row.invalidation || "-"}`,
        `제안 블록: ${blockLine(row.proposed_block)}`,
        `다음 점검: ${row.next_check || "-"}`,
      ].join("\n")).join("\n\n"),
    ].join("\n");
  }

  function renderKisCreativeHypotheses(payload, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const formatNumber = typeof options.fmtNum === "function"
      ? options.fmtNum
      : (numeric) => String(numeric);
    const formatKST = typeof options.fmtKST === "function"
      ? options.fmtKST
      : (value) => String(value || "");
    const registerDetail = typeof options.registerHelperDetail === "function"
      ? options.registerHelperDetail
      : () => "";
    const latest = payload?.latest_manager_run || {};
    const response = latest.response && typeof latest.response === "object" ? latest.response : {};
    const hypotheses = Array.isArray(latest.creative_hypotheses)
      ? latest.creative_hypotheses
      : (Array.isArray(response.creative_hypotheses) ? response.creative_hypotheses : []);
    const detailId = registerDetail({
      title: "KIS 쥬 창의적 가설 전체보기",
      subtitle: "Creative Hypothesis Loop",
      body: renderKisCreativeHypothesesDetailText({ latest, hypotheses }, options),
      meta: [
        latest.run_at ? formatKST(latest.run_at, true) : "판단 대기",
        `hypotheses ${hypotheses.length}`,
      ],
    });
    const typeCounts = hypotheses.reduce((acc, row) => {
      const key = row?.hypothesis_type || "unknown";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
    const cards = hypotheses.slice(0, 4).map((row) => {
      const block = row?.proposed_block && typeof row.proposed_block === "object" ? row.proposed_block : {};
      const symbols = Array.isArray(row?.symbols) ? row.symbols : [];
      return `
        <article class="helper-card">
          <h4>${escape(row?.title || row?.hypothesis_type || "가설")}</h4>
          <p class="helper-text">${escape(row?.summary || "요약 대기")}</p>
          <div class="strategy-chip-row">
            <span class="strategy-data-chip">${escape(row?.hypothesis_type || "-")}</span>
            <span class="strategy-data-chip ${row?.decision === "create_wait_block" ? "good" : row?.decision === "reject" ? "warn" : "neutral"}">${escape(row?.decision || "watch")}</span>
            <span class="strategy-data-chip">${escape(row?.horizon || "-")}</span>
            <span class="strategy-data-chip">확신 ${escape(formatNumber(Number(row?.confidence || 0) * 100, 0))}%</span>
          </div>
          <p class="helper-text">${escape(symbols.length ? symbols.join(" · ") : row?.sector || "심볼 검토 대기")}</p>
          ${Object.keys(block).length ? `
            <p class="helper-text">제안 블록: ${escape([
              block.symbol || "-",
              block.entry_style || "-",
              block.entry_trigger_price ? formatNumber(block.entry_trigger_price, 0) : "",
              block.target_price ? `목표 ${formatNumber(block.target_price, 0)}` : "",
              block.stop_price ? `방어 ${formatNumber(block.stop_price, 0)}` : "",
            ].filter(Boolean).join(" · "))}</p>
          ` : ""}
        </article>
      `;
    }).join("");
    return `
      <section class="memory-section binance-edge-panel">
        <div class="panel-head compact">
          <div>
            <h3>KIS 쥬 창의적 가설</h3>
            <p>${escape(latest.run_at ? `${formatKST(latest.run_at)} 생성 · ${formatNumber(hypotheses.length, 0)}개` : "최근 가설 대기")}</p>
          </div>
          <button class="btn tiny ghost" type="button" data-helper-detail-id="${escape(detailId)}">가설 전체보기</button>
        </div>
        <div class="strategy-chip-row">
          ${Object.entries(typeCounts).slice(0, 6).map(([key, value]) => `<span class="strategy-data-chip neutral">${escape(key)} ${escape(String(value))}</span>`).join("")
            || '<span class="strategy-data-chip">가설 대기</span>'}
        </div>
        ${cards ? `<div class="helper-grid">${cards}</div>` : '<p class="helper-text">다음 KIS 매니저 실행부터 창의적 가설이 축적됩니다.</p>'}
      </section>
    `;
  }

  function dailyDiscoveryItems(payload) {
    if (!payload || typeof payload !== "object") return [];
    if (Array.isArray(payload.items)) return payload.items;
    if (Array.isArray(payload.results)) return payload.results;
    if (Array.isArray(payload.run?.results)) return payload.run.results;
    return [];
  }

  function dailyDiscoverySummaryValue(
    payload,
    key,
    fallback = 0,
    nonNegativeInt = (value) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
    },
  ) {
    const summary = payload?.summary && typeof payload.summary === "object"
      ? payload.summary
      : {};
    const value = nonNegativeInt(payload?.[key] ?? summary[key]);
    return value === null ? nonNegativeInt(fallback) || 0 : value;
  }

  function renderDailyDiscoveryCard(row, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const formatNumber = typeof options.fmtNum === "function"
      ? options.fmtNum
      : (value) => String(value);
    const analysis = row?.analysis && typeof row.analysis === "object" ? row.analysis : {};
    const stance = String(analysis.stance || row?.stance || "-");
    const score = row?.score ?? analysis.score ?? analysis.confidence ?? null;
    const summary = analysis.summary || row?.summary || row?.error_message || "분석 요약 대기";
    const status = String(row?.status || "");
    const tone = status === "error"
      ? "bad"
      : stance === "block_candidate" || stance === "buy" || stance === "bullish"
        ? "good"
        : "neutral";
    return `
      <article class="daily-discovery-card ${status === "error" ? "error" : ""}">
        <div class="daily-discovery-card-head">
          <strong>${escape(row?.name || row?.symbol || "-")} <span>${escape(row?.symbol || "")}</span></strong>
          <span class="strategy-data-chip ${escape(tone)}">${escape(stance)}</span>
        </div>
        <div class="daily-discovery-card-meta">
          <span class="strategy-data-chip neutral">${escape(row?.market || "-")}</span>
          <span class="strategy-data-chip neutral">score ${escape(score === null || score === undefined ? "-" : formatNumber(score, 1))}</span>
          ${status ? `<span class="strategy-data-chip ${status === "error" ? "warn" : "good"}">${escape(status)}</span>` : ""}
        </div>
        <p>${escape(summary)}</p>
      </article>
    `;
  }

  function renderDailyDiscoveryPanel(discoveryState, options = {}) {
    const escape = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeText;
    const formatKST = typeof options.fmtKST === "function"
      ? options.fmtKST
      : (value) => String(value || "");
    const nonNegativeInt = typeof options.normalizeNonNegativeInt === "function"
      ? options.normalizeNonNegativeInt
      : (value) => {
        const parsed = Number(value);
        return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
      };
    const payload = discoveryState?.payload || {};
    const items = dailyDiscoveryItems(payload);
    const blockCandidates = Array.isArray(payload.block_candidates)
      ? payload.block_candidates
      : items.filter((row) => {
        const analysis = row?.analysis && typeof row.analysis === "object" ? row.analysis : {};
        return analysis.stance === "block_candidate";
      });
    const selectedCount = dailyDiscoverySummaryValue(payload, "selected_count", items.length, nonNegativeInt);
    const analyzedCount = dailyDiscoverySummaryValue(
      payload,
      "analyzed_count",
      items.filter((row) => row?.status !== "error").length,
      nonNegativeInt,
    );
    const candidateCount = dailyDiscoverySummaryValue(
      payload,
      "block_candidate_count",
      blockCandidates.length,
      nonNegativeInt,
    );
    const busy = discoveryState?.loading || discoveryState?.running ? "disabled" : "";
    const loadingChip = discoveryState?.loading ? '<span class="strategy-data-chip neutral">loading</span>' : "";
    const runningChip = discoveryState?.running ? '<span class="strategy-data-chip warn">running</span>' : "";
    const errorHtml = discoveryState?.error
      ? `<div class="notice warn">아침 탐사 실패: ${escape(discoveryState.error)}</div>`
      : "";
    return `
      <section class="daily-discovery-panel">
        <div class="panel-head compact">
          <div>
            <h3>쥬 아침 탐사</h3>
            <p>장전 KOSPI/KOSDAQ 심층 스터디 결과</p>
          </div>
          <div class="daily-discovery-actions">
            <button class="btn small ghost" type="button" data-discovery-action="refresh" ${busy}>새로고침</button>
            <button class="btn small warm" type="button" data-discovery-action="run" ${busy}>심층 탐사 실행</button>
          </div>
        </div>
        ${errorHtml}
        <div class="daily-discovery-summary">
          <span><b>${escape(payload.status || "missing")}</b>Status</span>
          <span><b>${escape(payload.trading_day || "-")}</b>Trading day</span>
          <span><b>${escape(String(candidateCount))}</b>후보</span>
          <span><b>${escape(String(selectedCount))}</b>Selected</span>
          <span><b>${escape(String(analyzedCount))}</b>Analyzed</span>
        </div>
        <div class="daily-discovery-strip">
          <span class="strategy-data-chip ${candidateCount ? "good" : "neutral"}">block candidates ${escape(String(candidateCount))}</span>
          <span class="strategy-data-chip neutral">cards ${escape(String(items.length))}</span>
          ${payload.updated_at ? `<span class="strategy-data-chip good">updated ${escape(formatKST(payload.updated_at, true))}</span>` : ""}
          ${loadingChip}${runningChip}
        </div>
        <div class="daily-discovery-grid">
          ${items.length ? items.slice(0, 10).map((row) => renderDailyDiscoveryCard(row, options)).join("") : '<div class="notice compact">아직 오늘 아침 탐사 결과가 없습니다.</div>'}
        </div>
      </section>
    `;
  }

  window.HERMES_KIS_TRADER_TAB = Object.freeze({
    activeBlockStatuses,
    blockHorizons,
    blockStatusLabel,
    blockTone,
    timelineDateValue,
    dateKeyKST,
    timelineDate,
    historyDates,
    exitOrder,
    historyPnl,
    statusMatches,
    filteredHistoryBlocks,
    daySummary,
    normalizeBlockHorizon,
    blockHorizonLabel,
    blockHorizonDescription,
    blockHorizonClass,
    blockHorizonForBlock,
    blockHorizonWeight,
    blockDirectiveContext,
    renderBlockHero,
    renderHorizonAllocation,
    renderBlockCard,
    renderHorizonBlockGroups,
    renderBlockHistoryRow,
    renderBlockHistoryDetail,
    renderBlockHistoryBoard,
    renderBlockAllocation,
    renderBlockEventFeed,
    renderBlockManagerRun,
    renderKisHoldDecisionDetailText,
    renderKisHoldDecision,
    renderKisCreativeHypothesesDetailText,
    renderKisCreativeHypotheses,
    dailyDiscoveryItems,
    dailyDiscoverySummaryValue,
    renderDailyDiscoveryCard,
    renderDailyDiscoveryPanel,
  });
})();
