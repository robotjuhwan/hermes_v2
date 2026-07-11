(function () {
  const lanes = [
    Object.freeze({ id: "short", label: "단기 현물", description: "빠른 모멘텀·촉매 대응" }),
    Object.freeze({ id: "mid", label: "중기 현물", description: "스윙 thesis 관리" }),
    Object.freeze({ id: "long", label: "장기 현물", description: "포지션 thesis 관리" }),
    Object.freeze({ id: "futures", label: "선물", description: "고위험 방향성 블록" }),
    Object.freeze({ id: "upbit_spot", label: "업비트 현물", description: "KRW 현물 블록" }),
    Object.freeze({ id: "volatile_attack", label: "초변동 공격", description: "소액·넓은 손절·대기진입" }),
  ];

  const laneLabels = Object.freeze({
    short: "단기 현물",
    mid: "중기 현물",
    long: "장기 현물",
    futures: "선물",
    spot: "현물",
    "spot:long": "현물 롱",
    "spot:long:short": "현물 단기 롱",
    "spot:long:mid": "현물 중기 롱",
    "spot:long:long": "현물 장기 롱",
    "futures:long": "선물 롱",
    futures_long: "선물 롱",
    "futures:short": "선물 숏",
    futures_short: "선물 숏",
    "upbit_spot:long": "업비트 현물",
    upbit_spot: "업비트 현물",
    volatile_attack: "초변동 공격",
  });

  function laneLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    return laneLabels[key] || value || "-";
  }

  function blockLane(block) {
    const lane = String(block?.lane || block?.metadata?.lane || block?.calculated?.lane || "").toLowerCase();
    if (lane === "volatile_attack") return "volatile_attack";
    const market = String(block?.market || block?.venue || "spot").toLowerCase();
    if (market === "upbit_spot") return "upbit_spot";
    if (market === "futures") return "futures";
    const horizon = String(block?.horizon || block?.metadata?.horizon || block?.lane || "short").toLowerCase();
    return ["short", "mid", "long"].includes(horizon) ? horizon : "short";
  }

  function blockPrice(block, key, aliasKey) {
    const market = String(block?.market || block?.venue || "spot").toLowerCase();
    if (market === "upbit_spot") return block?.[key];
    return block?.[aliasKey] ?? block?.[key];
  }

  function groupBlocksByLane(blocks, laneRows = lanes) {
    return laneRows.reduce((acc, lane) => {
      acc[lane.id] = blocks.filter((block) => blockLane(block) === lane.id);
      return acc;
    }, {});
  }

  const activeBlockStatuses = Object.freeze(["proposed", "entry_pending", "open", "exit_pending", "paused", "error"]);

  function activeBlocks(payload) {
    if (Array.isArray(payload?.active_blocks)) return payload.active_blocks;
    const rows = Array.isArray(payload?.blocks) ? payload.blocks : [];
    return rows.filter((block) => activeBlockStatuses.includes(String(block?.status || "")));
  }

  function historyDateKey(block, dateFormatter) {
    const raw = String(block?.closed_at || block?.updated_at || block?.created_at || "");
    if (!raw) return "";
    if (typeof dateFormatter === "function") {
      return String(dateFormatter(raw) || "");
    }
    return raw.slice(0, 10);
  }

  function historyRows(payload, statuses = ["closed", "error"]) {
    if (Array.isArray(payload?.block_history)) return payload.block_history;
    const rows = Array.isArray(payload?.blocks) ? payload.blocks : [];
    return rows.filter((block) => statuses.includes(String(block?.status || "")));
  }

  function toNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function escapeDefault(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatNumberDefault(value, digits = 2) {
    const parsed = toNumber(value, 0);
    return parsed.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function formatUsdtDefault(value, digits = 2) {
    return `${formatNumberDefault(value, digits)} USDT`;
  }

  function formatPercentDefault(value, digits = 1) {
    return `${formatNumberDefault(value, digits)}%`;
  }

  function displayOptions(options = {}) {
    return {
      escapeHTML: typeof options.escapeHTML === "function" ? options.escapeHTML : escapeDefault,
      fmtNum: typeof options.fmtNum === "function" ? options.fmtNum : formatNumberDefault,
      fmtUSDT: typeof options.fmtUSDT === "function" ? options.fmtUSDT : formatUsdtDefault,
      fmtPercent: typeof options.fmtPercent === "function" ? options.fmtPercent : formatPercentDefault,
      asNumber: typeof options.asNumber === "function" ? options.asNumber : toNumber,
    };
  }

  function historyPerformance(block, numberParser = toNumber) {
    const parse = typeof numberParser === "function" ? numberParser : toNumber;
    const performance = block?.performance || block?.performance_reflection || {};
    const realizedPnl = parse(block?.realized_pnl_usdt ?? performance.pnl_usdt, 0);
    const rMultiple = parse(block?.r_multiple ?? performance.r_multiple, 0);
    return {
      performance,
      realizedPnl,
      rMultiple,
      pnlClass: realizedPnl >= 0 ? "gain" : "loss",
    };
  }

  function growthTargetStatusLabel(status) {
    const labels = {
      ahead_target: "목표 초과",
      on_track: "정상 속도",
      behind_target: "가속 필요",
      missing_equity: "계좌 기준 없음",
    };
    const key = String(status || "").trim();
    return labels[key] || key || "-";
  }

  function growthGovernorModeMeta(governor) {
    const payload = governor && typeof governor === "object" ? governor : {};
    const labels = {
      steady: "균형 운용",
      edge_rebuild: "Edge 재건",
      press_verified_edges: "검증 Edge 가속",
      halt_new_entries: "신규 중지",
    };
    const mode = String(payload.mode || payload.status || "steady");
    const allow = Boolean(payload.allow_new_blocks);
    return {
      mode,
      label: labels[mode] || mode,
      allow,
      requireWaiting: Boolean(payload.require_waiting_entry),
      tone: mode === "press_verified_edges" ? "good" : (allow ? "warn" : "bad"),
    };
  }

  function growthUnlockPhaseMeta(unlock) {
    const payload = unlock && typeof unlock === "object" ? unlock : {};
    const labels = {
      halted: "중지",
      rebuilding: "재건 중",
      probe_ready: "대기진입 탐색 가능",
      immediate_ready: "즉시진입 가능",
      scale_ready: "증액 가능",
    };
    const phase = String(payload.phase || "rebuilding");
    return {
      phase,
      label: labels[phase] || phase,
      tone: phase === "scale_ready" || phase === "immediate_ready"
        ? "good"
        : (phase === "halted" ? "bad" : "warn"),
    };
  }

  function riskGuardTone(guard) {
    const payload = guard && typeof guard === "object" ? guard : {};
    return Boolean(payload.allow_new_entries) ? "good" : "warn";
  }

  function filteredHistory(payload, filters = {}, options = {}) {
    const rows = historyRows(payload, options.statuses || ["closed", "error"]);
    const date = String(filters.date || "");
    const lane = String(filters.lane || "all");
    const query = String(filters.query || "").trim().toUpperCase();
    const laneFn = typeof options.blockLane === "function" ? options.blockLane : blockLane;
    const dateKeyFn = typeof options.historyDateKey === "function"
      ? options.historyDateKey
      : (block) => historyDateKey(block, options.dateFormatter);
    return rows.filter((block) => {
      if (date && dateKeyFn(block) !== date) return false;
      if (lane !== "all" && laneFn(block) !== lane) return false;
      if (query && !String(block?.symbol || "").toUpperCase().includes(query)) return false;
      return true;
    });
  }

  function renderBlockCard(block, options = {}) {
    const escapeHTML = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeDefault;
    const fmtNum = typeof options.fmtNum === "function" ? options.fmtNum : formatNumberDefault;
    const renderBlockValidationChips = typeof options.renderBlockValidationChips === "function"
      ? options.renderBlockValidationChips
      : () => "";
    const renderValidationPassportChips = typeof options.renderValidationPassportChips === "function"
      ? options.renderValidationPassportChips
      : () => "";
    const renderBlockPolicyEffectChips = typeof options.renderBlockPolicyEffectChips === "function"
      ? options.renderBlockPolicyEffectChips
      : () => "";
    const status = String(block?.status || "-");
    const tone = status === "open" ? "ok" : (status === "error" ? "bad" : "warn");
    const venue = String(block?.venue || block?.market || "spot").toLowerCase();
    const lane = blockLane(block);
    const metadata = block?.metadata && typeof block.metadata === "object" ? block.metadata : {};
    const createdBy = String(block?.created_by || "").toLowerCase();
    const fillProvenance = String(
      metadata.fill_provenance
      || metadata.fill_source
      || block?.fill_provenance
      || block?.fill_source
      || block?.execution_source
      || ""
    ).toLowerCase();
    const provenanceChips = [
      createdBy === "wallet_adoption"
        ? '<span class="strategy-data-chip warn">Wallet 채택 · 쥬 진입 성과 제외</span>'
        : createdBy === "existing_position"
          ? '<span class="strategy-data-chip warn">기존 포지션 채택 · 쥬 진입 성과 제외</span>'
          : "",
      fillProvenance.includes("exchange") || fillProvenance === "live_fill"
        ? '<span class="strategy-data-chip good">거래소 체결</span>'
        : fillProvenance.includes("paper")
          ? '<span class="strategy-data-chip neutral">Paper 체결</span>'
          : "",
      ["failed", "failed_entry", "rejected", "error"].includes(status.toLowerCase())
        ? '<span class="strategy-data-chip bad">진입 실패 · 체결 없음</span>'
        : "",
    ].join("");
    return `
    <article class="binance-block-card ${escapeHTML(venue)} ${escapeHTML(lane)}">
      <div class="block-card-head">
        <div>
          <span class="section-kicker">${escapeHTML(`${venue} · ${block?.side || "-"}`)}</span>
          <h4>${escapeHTML(block?.symbol || "-")}</h4>
          <p>${escapeHTML(block?.block_id || "")}</p>
        </div>
        <span class="helper-runtime-chip ${tone}">${escapeHTML(status)}</span>
      </div>
      <div class="block-price-grid">
        <div><span>진입</span><strong>${escapeHTML(fmtNum(blockPrice(block, "entry_price", "entry_price_usdt"), 4))}</strong></div>
        <div><span>현재</span><strong>${escapeHTML(fmtNum(blockPrice(block, "current_price", "current_price_usdt"), 4))}</strong></div>
        <div><span>목표</span><strong>${escapeHTML(fmtNum(blockPrice(block, "target_price", "target_price_usdt"), 4))}</strong></div>
        <div><span>손절</span><strong>${escapeHTML(fmtNum(blockPrice(block, "stop_price", "stop_price_usdt"), 4))}</strong></div>
        <div><span>수량</span><strong>${escapeHTML(fmtNum(block?.qty_open ?? block?.qty_initial, 8))}</strong></div>
        <div><span>레버리지</span><strong>${escapeHTML(fmtNum(block?.leverage || 1, 1))}x</strong></div>
      </div>
      <p class="helper-text">${escapeHTML(block?.thesis || block?.llm_reason || block?.risk_note || "-")}</p>
      <div class="strategy-chip-row">${provenanceChips}${renderBlockValidationChips(metadata)}${renderValidationPassportChips(metadata)}${renderBlockPolicyEffectChips(metadata)}</div>
    </article>
  `;
  }

  function renderBlockHistory(payload, options = {}) {
    const escapeHTML = typeof options.escapeHTML === "function" ? options.escapeHTML : escapeDefault;
    const fmtNum = typeof options.fmtNum === "function" ? options.fmtNum : formatNumberDefault;
    const fmtUSDT = typeof options.fmtUSDT === "function" ? options.fmtUSDT : formatUsdtDefault;
    const fmtKST = typeof options.fmtKST === "function" ? options.fmtKST : null;
    const asNumber = typeof options.asNumber === "function" ? options.asNumber : toNumber;
    const state = options.state && typeof options.state === "object" ? options.state : {};
    const laneRows = Array.isArray(options.lanes) ? options.lanes : lanes;
    const dateKeyFn = (block) => historyDateKey(block, fmtKST
      ? (raw) => fmtKST(raw, true).slice(0, 10)
      : undefined);
    const rows = filteredHistory(payload, {
      date: state.historyDate,
      lane: state.historyLane,
      query: state.historyQuery,
    }, {
      statuses: options.statuses || ["closed", "error"],
      historyDateKey: dateKeyFn,
    });
    return `
    <section class="memory-section binance-history-panel">
      <div class="panel-head compact">
        <h3>블록 히스토리</h3>
        <p>닫힌 블록과 오류 블록을 날짜·레인·심볼로 다시 봅니다.</p>
      </div>
      <div class="binance-history-toolbar">
        <input id="binanceHistoryDate" type="date" value="${escapeHTML(state.historyDate || "")}" />
        <select id="binanceHistoryLane">
          ${["all", ...laneRows.map((row) => row.id)].map((lane) => (
            `<option value="${lane}" ${state.historyLane === lane ? "selected" : ""}>${escapeHTML(lane === "all" ? "전체" : laneLabel(lane))}</option>`
          )).join("")}
        </select>
        <input id="binanceHistoryQuery" type="search" value="${escapeHTML(state.historyQuery || "")}" placeholder="심볼 검색" />
      </div>
      <div class="binance-history-list">
        ${rows.slice(0, 40).map((block) => {
          const performance = historyPerformance(block, asNumber);
          return `
          <article class="binance-history-row">
            <strong>${escapeHTML(block?.symbol || "-")}</strong>
            <span>${escapeHTML(`${laneLabel(blockLane(block))} · ${block?.status || "-"}`)}</span>
            <span>${escapeHTML(dateKeyFn(block) || "-")}</span>
            <span class="${performance.pnlClass}">${escapeHTML(fmtUSDT(performance.realizedPnl, 4))}</span>
            <span>${escapeHTML(`R ${fmtNum(performance.rMultiple, 2)}`)}</span>
          </article>
          `;
        }).join("") || '<div class="notice">조건에 맞는 히스토리가 없습니다.</div>'}
      </div>
    </section>
  `;
  }

  function latestCandidateGeneration(payload) {
    if (payload?.candidate_generation && typeof payload.candidate_generation === "object") {
      return payload.candidate_generation;
    }
    const runs = Array.isArray(payload?.manager_runs) ? payload.manager_runs : [];
    for (const run of runs) {
      const prompt = run?.prompt && typeof run.prompt === "object" ? run.prompt : {};
      const generation = prompt.candidate_generation && typeof prompt.candidate_generation === "object"
        ? prompt.candidate_generation
        : null;
      if (generation) return generation;
    }
    return {};
  }

  function renderCandidatePacketList(title, rows, options = {}) {
    const { escapeHTML, fmtNum } = displayOptions(options);
    const items = Array.isArray(rows) ? rows : [];
    return `
    <div class="binance-packet-card">
      <h4>${escapeHTML(title)}</h4>
      <div class="binance-packet-list">
        ${items.slice(0, 8).map((row) => `
          <article>
            <strong>${escapeHTML(row?.symbol || "-")}</strong>
            <span>${escapeHTML(`${fmtNum(row?.score ?? 0, 1)} · ${fmtNum(row?.change_pct_24h ?? 0, 2)}% · vol x${fmtNum(row?.volume_expansion_ratio ?? 0, 1)}`)}</span>
          </article>
        `).join("") || '<div class="notice compact">후보 없음</div>'}
      </div>
    </div>
  `;
  }

  function renderUniversePipeline(payload, options = {}) {
    const { escapeHTML, fmtNum, asNumber } = displayOptions(options);
    const generation = latestCandidateGeneration(payload);
    const stages = generation.stage_counts && typeof generation.stage_counts === "object"
      ? generation.stage_counts
      : {};
    const packets = generation.candidate_packets && typeof generation.candidate_packets === "object"
      ? generation.candidate_packets
      : {};
    const volatileCount = asNumber(generation.volatile_attack_candidate_count, 0);
    return `
    <section class="memory-section binance-universe-panel">
      <div class="panel-head compact">
        <h3>유니버스 파이프라인</h3>
        <p>상위 300 관찰층을 리서치·실행 구조로 압축해 쥬 판단에 넣습니다.</p>
      </div>
      <div class="binance-universe-steps">
        <article><span>상위 300 관찰</span><strong>${escapeHTML(fmtNum(stages.observe_universe ?? 0, 0))}</strong></article>
        <article><span>리서치 압축</span><strong>${escapeHTML(fmtNum(stages.research_universe ?? 0, 0))}</strong></article>
        <article><span>쥬 판단 후보</span><strong>${escapeHTML(fmtNum(stages.manager_candidates ?? 0, 0))}</strong></article>
        <article><span>실행 후보</span><strong>${escapeHTML(fmtNum(stages.trade_candidates ?? 0, 0))}</strong></article>
        <article><span>초변동 후보</span><strong>${escapeHTML(fmtNum(volatileCount, 0))}</strong></article>
      </div>
      <div class="binance-packet-grid">
        ${renderCandidatePacketList("초변동 공격", packets.volatile_candidates, options)}
        ${renderCandidatePacketList("상위 변동", packets.top_movers, options)}
        ${renderCandidatePacketList("레짐 리더", packets.regime_leaders, options)}
        ${renderCandidatePacketList("스퀴즈", packets.squeeze_setup, options)}
      </div>
    </section>
  `;
  }

  function renderGrowthTarget(payload, options = {}) {
    const { escapeHTML, fmtPercent, fmtUSDT, asNumber } = displayOptions(options);
    const target = payload?.growth_target || {};
    const performance = payload?.performance || {};
    const status = growthTargetStatusLabel(target.status);
    const basis = target.basis
      ? `기준: ${String(target.basis).replaceAll("_", " ")}`
      : "기준: 계좌 총자산";
    const juePnl = asNumber(performance.realized_pnl_usdt, 0);
    return `
    <section class="memory-section binance-growth-target">
      <div class="panel-head compact">
        <h3>월간 성장 타겟</h3>
        <p>월 50% 목표 대비 계좌 기준 속도와 쥬 블록 실현손익을 분리해서 판단에 반영합니다. ${escapeHTML(basis)}</p>
      </div>
      <div class="binance-edge-grid">
        <div>
          <span>목표 수익률</span>
          <strong>${escapeHTML(fmtPercent(target.monthly_target_pct || 0, 1))}</strong>
        </div>
        <div>
          <span>계좌 기준 현재 수익률</span>
          <strong class="${asNumber(target.current_return_pct, 0) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtPercent(target.current_return_pct || 0, 2))}</strong>
        </div>
        <div>
          <span>쥬 블록 실현손익</span>
          <strong class="${juePnl >= 0 ? "gain" : "loss"}">${escapeHTML(fmtUSDT(juePnl, 4))}</strong>
        </div>
        <div>
          <span>필요 일일 속도</span>
          <strong>${escapeHTML(fmtPercent(target.required_daily_return_pct || 0, 2))}</strong>
        </div>
        <div>
          <span>현재 / 목표</span>
          <strong>${escapeHTML(`${fmtUSDT(target.current_equity_usdt || 0, 2)} / ${fmtUSDT(target.target_equity_usdt || 0, 2)}`)}</strong>
        </div>
        <div>
          <span>상태</span>
          <strong>${escapeHTML(status)}</strong>
        </div>
      </div>
    </section>
  `;
  }

  function renderGrowthGovernor(payload = {}, options = {}) {
    const { escapeHTML, fmtNum, fmtPercent, asNumber } = displayOptions(options);
    const governor = payload.growth_governor || {};
    const metrics = governor.metrics && typeof governor.metrics === "object"
      ? governor.metrics
      : {};
    const modeMeta = growthGovernorModeMeta(governor);
    const reasons = Array.isArray(governor.reasons) ? governor.reasons : [];
    return `
    <section class="memory-section binance-growth-governor">
      <div class="panel-head compact">
        <h3>성장 Governor</h3>
        <p>월간 목표와 최근 실전 Edge를 같이 보고 이번 사이클의 신규 블록 강도를 조절합니다.</p>
      </div>
      <div class="binance-edge-grid">
        <div>
          <span>운용 모드</span>
          <strong class="${modeMeta.tone}">${escapeHTML(modeMeta.label)}</strong>
        </div>
        <div>
          <span>신규 블록</span>
          <strong>${escapeHTML(modeMeta.allow ? `${fmtNum(governor.max_new_blocks ?? 0, 0)}개까지` : "중지")}</strong>
        </div>
        <div>
          <span>진입 방식</span>
          <strong>${escapeHTML(modeMeta.requireWaiting ? "대기진입 필수" : "즉시/대기 선택")}</strong>
        </div>
        <div>
          <span>공격 배수</span>
          <strong>${escapeHTML(`x${fmtNum(governor.aggression_multiplier ?? 1, 2)}`)}</strong>
        </div>
        <div>
          <span>최근 승률</span>
          <strong>${escapeHTML(fmtPercent(metrics.win_rate_pct || 0, 1))}</strong>
        </div>
        <div>
          <span>최근 Avg R</span>
          <strong class="${asNumber(metrics.avg_r_multiple, 0) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtNum(metrics.avg_r_multiple || 0, 2))}</strong>
        </div>
      </div>
      ${reasons.length ? `
        <div class="strategy-data-warning-strip">
          ${reasons.slice(0, 4).map((reason) => `
            <span class="strategy-data-chip ${modeMeta.tone === "bad" ? "bad" : "warn"}">${escapeHTML(String(reason).replaceAll("_", " "))}</span>
          `).join("")}
        </div>
      ` : ""}
    </section>
  `;
  }

  function renderGrowthUnlock(payload = {}, options = {}) {
    const { escapeHTML } = displayOptions(options);
    const unlock = payload.growth_unlock || {};
    const criteria = Array.isArray(unlock.criteria) ? unlock.criteria : [];
    const missions = Array.isArray(unlock.next_missions) ? unlock.next_missions : [];
    const permissions = unlock.action_permissions && typeof unlock.action_permissions === "object"
      ? unlock.action_permissions
      : {};
    const phaseMeta = growthUnlockPhaseMeta(unlock);
    const criteriaRows = criteria.slice(0, 6).map((row) => `
    <div>
      <span>${escapeHTML(row?.label || row?.id || "-")}</span>
      <strong class="${row?.passed ? "gain" : "warn"}">${escapeHTML(row?.passed ? "PASS" : "WAIT")}</strong>
      <small>${escapeHTML(`${row?.current ?? "-"} / ${row?.target ?? "-"}`)}</small>
    </div>
  `).join("");
    const missionRows = missions.slice(0, 5).map((row) => `
    <div class="helper-card">
      <h4>${escapeHTML(`${row?.priority || "-"} · ${String(row?.mission || "-").replaceAll("_", " ")}`)}</h4>
      <p class="helper-text">${escapeHTML(`${row?.lane || "all"} · ${row?.success_condition || "-"}`)}</p>
    </div>
  `).join("");
    const permissionChips = [
      ["대기진입", permissions.new_waiting_entry_probe],
      ["즉시진입", permissions.immediate_entry],
      ["증액", permissions.scale_up],
      ["초변동 탐색", permissions.volatile_attack_probe],
    ].map(([label, ok]) => `
    <span class="strategy-data-chip ${ok ? "good" : "neutral"}">${escapeHTML(`${label}: ${ok ? "ON" : "OFF"}`)}</span>
  `).join("");
    return `
    <section class="memory-section binance-growth-unlock">
      <div class="panel-head compact">
        <div>
          <h3>공격 권한 Unlock</h3>
          <p>관망·Edge 재건 상태에서 어떤 증거가 모이면 더 공격적으로 전환되는지 추적합니다.</p>
        </div>
        <span class="helper-runtime-chip ${phaseMeta.tone}">${escapeHTML(phaseMeta.label)}</span>
      </div>
      <div class="strategy-chip-row">${permissionChips}</div>
      <div class="binance-edge-grid">
        ${criteriaRows || '<div><span>기준</span><strong>대기</strong><small>growth_unlock 없음</small></div>'}
      </div>
      ${missionRows ? `<div class="binance-edge-grid">${missionRows}</div>` : ""}
    </section>
  `;
  }

  function renderLaneEdgePanel(payload = {}, options = {}) {
    const { escapeHTML, fmtNum, fmtUSDT, fmtPercent, asNumber } = displayOptions(options);
    const performance = payload.performance || {};
    const risk = payload.risk || payload.risk_budget || {};
    const cards = Array.isArray(performance.lane_scorecards) ? performance.lane_scorecards : [];
    const staticMultipliers = risk.lane_risk_multipliers || {};
    const liveMultipliers = risk.lane_performance_multipliers || {};
    const keys = Array.from(new Set([
      "volatile_attack",
      "futures",
      "futures:long",
      "futures:short",
      "upbit_spot:long",
      "upbit_spot",
      "spot:long",
      "short",
      "mid",
      "long",
      ...cards.map((row) => String(row.lane || "")).filter(Boolean),
    ]));
    const rows = keys.map((key) => {
      const card = cards.find((row) => String(row.lane || "") === key) || {};
      const live = asNumber(liveMultipliers[key], 1);
      const base = asNumber(staticMultipliers[key], 1);
      const pnl = asNumber(card.pnl_usdt, 0);
      const tone = live < 1 ? "warn" : (live > 1 ? "good" : "neutral");
      return `
      <div class="binance-lane-edge-row">
        <span>${escapeHTML(laneLabel(key))}</span>
        <strong class="${pnl >= 0 ? "gain" : "loss"}">${escapeHTML(fmtUSDT(pnl, 4))}</strong>
        <small>${escapeHTML(`${fmtNum(card.sample_count || 0, 0)}건 · 승률 ${fmtPercent(card.win_rate_pct || 0, 1)} · AvgR ${fmtNum(card.avg_r_multiple || 0, 2)}`)}</small>
        <em class="${tone}">${escapeHTML(`base ${fmtNum(base, 2)} · live ${fmtNum(live, 2)}`)}</em>
      </div>
    `;
    }).join("");
    return `
    <section class="memory-section binance-lane-edge-panel">
      <div class="panel-head compact">
        <h3>Lane 실전 Edge</h3>
        <p>블록 반성 결과가 lane별 수량 배수와 진입 강도에 반영됩니다.</p>
      </div>
      <div class="binance-lane-edge-list">
        ${rows || '<div class="notice compact">lane 성과 대기</div>'}
      </div>
    </section>
  `;
  }

  function renderRiskGuard(payload = {}, options = {}) {
    const { escapeHTML, fmtNum, fmtUSDT, fmtPercent, asNumber } = displayOptions(options);
    const guard = payload.risk_guard || {};
    const breaches = Array.isArray(guard.breaches) ? guard.breaches : [];
    const status = String(guard.status || "missing").replaceAll("_", " ");
    const allow = Boolean(guard.allow_new_entries);
    const tone = riskGuardTone(guard);
    const day = guard.day || {};
    const month = guard.month || {};
    const breachRows = breaches.map((row) => `
    <span class="strategy-data-chip bad">
      ${escapeHTML(row?.scope || "-")} ${escapeHTML(fmtPercent(row?.return_pct || 0, 2))}
      / ${escapeHTML(fmtPercent(row?.limit_pct || 0, 2))}
    </span>
  `).join("");
    return `
    <section class="memory-section binance-risk-guard-panel">
      <div class="panel-head compact">
        <h3>계좌 손실 Guard</h3>
        <p>기존 블록의 청산은 유지하고, 손실 중지선 아래에서는 신규 진입만 차단합니다.</p>
      </div>
      <div class="binance-edge-grid">
        <div>
          <span>신규 진입</span>
          <strong class="${tone}">${escapeHTML(allow ? "OPEN" : "HALT")}</strong>
        </div>
        <div>
          <span>Guard 상태</span>
          <strong>${escapeHTML(status)}</strong>
        </div>
        <div>
          <span>현재 Equity</span>
          <strong>${escapeHTML(fmtUSDT(guard.current_equity_usdt, 2))}</strong>
        </div>
        <div>
          <span>당일 손익률</span>
          <strong class="${asNumber(day.return_pct, 0) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtPercent(day.return_pct || 0, 2))}</strong>
        </div>
        <div>
          <span>월간 손익률</span>
          <strong class="${asNumber(month.return_pct, 0) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtPercent(month.return_pct || 0, 2))}</strong>
        </div>
        <div>
          <span>중지선</span>
          <strong>${escapeHTML(`일 -${fmtNum(guard.daily_loss_stop_pct || 0, 1)}% · 월 -${fmtNum(guard.monthly_loss_stop_pct || 0, 1)}%`)}</strong>
        </div>
      </div>
      ${breachRows ? `<div class="strategy-data-warning-strip">${breachRows}</div>` : ""}
    </section>
  `;
  }

  function renderKpiGrid(payload, blocks, options = {}) {
    const { escapeHTML, fmtNum, fmtUSDT, fmtPercent, asNumber } = displayOptions(options);
    const execution = payload?.execution || {};
    const account = payload?.account || {};
    const risk = payload?.risk || payload?.risk_budget || {};
    const performance = payload?.performance || payload?.performance_feedback || {};
    const performanceToday = payload?.performance_today || {};
    const pnlPerformance = Object.keys(performanceToday).length ? performanceToday : performance;
    const blockRows = Array.isArray(blocks) ? blocks : activeBlocks(payload);
    const killEnabled = Boolean(payload?.kill_switch?.enabled || payload?.summary?.kill_switch?.enabled);
    return `
      <div class="block-trader-kpis">
        <article class="mini-card"><p>현물 모드</p><h4>${escapeHTML(execution.spot_mode || "-")}</h4></article>
        <article class="mini-card"><p>선물 모드</p><h4>${escapeHTML(execution.futures_mode || "-")}</h4></article>
        <article class="mini-card"><p>킬스위치</p><h4>${escapeHTML(killEnabled ? "ON" : "OFF")}</h4></article>
        <article class="mini-card"><p>블록 리스크</p><h4>${escapeHTML(fmtPercent(risk.account_risk_pct ?? risk.risk_pct ?? 0, 2))}</h4></article>
        <article class="mini-card"><p>최소 R/R</p><h4>${escapeHTML(fmtNum(risk.min_reward_risk ?? risk.min_rr ?? 0, 2))}</h4></article>
        <article class="mini-card"><p>현물 USDT</p><h4>${escapeHTML(fmtNum(account.spot_cash_usdt, 2))}</h4></article>
        <article class="mini-card"><p>선물 USDT</p><h4>${escapeHTML(fmtNum(account.futures_cash_usdt, 2))}</h4></article>
        <article class="mini-card"><p>승률 / Avg R</p><h4>${escapeHTML(`${fmtPercent(performance.win_rate_pct ?? 0, 1)} / ${fmtNum(performance.avg_r_multiple ?? 0, 2)}`)}</h4></article>
        <article class="mini-card"><p>오늘 실현 손익</p><h4 class="${asNumber(pnlPerformance.realized_pnl_usdt, 0) >= 0 ? "gain" : "loss"}">${escapeHTML(fmtUSDT(pnlPerformance.realized_pnl_usdt, 4))}</h4></article>
        <article class="mini-card"><p>활성 블록</p><h4>${escapeHTML(fmtNum(blockRows.length, 0))}</h4></article>
      </div>
    `;
  }

  function renderLaneBoard(payload, blocks, options = {}) {
    const { escapeHTML, fmtNum, fmtPercent } = displayOptions(options);
    const laneRows = Array.isArray(options.lanes) ? options.lanes : lanes;
    const blockRows = Array.isArray(blocks) ? blocks : activeBlocks(payload);
    const renderCard = typeof options.renderBlockCard === "function"
      ? options.renderBlockCard
      : (block) => renderBlockCard(block, options);
    const allocationRows = Array.isArray(payload?.lane_allocation?.items) ? payload.lane_allocation.items : [];
    const allocationByLane = allocationRows.reduce((acc, row) => {
      const lane = String(row?.lane || "");
      if (lane) acc[lane] = row;
      return acc;
    }, {});
    const blocksByLane = groupBlocksByLane(blockRows, laneRows);
    const laneBoard = laneRows.map((lane) => {
      const laneBlocks = blocksByLane[lane.id] || [];
      const allocation = allocationByLane[lane.id] || {};
      const allocationText = allocation.value_usdt
        ? `${fmtNum(allocation.value_usdt, 0)} USDT · ${fmtPercent(allocation.weight_pct ?? 0, 1)}`
        : lane.description;
      return `
      <section class="binance-lane-column ${escapeHTML(lane.id)}">
        <div class="binance-lane-head">
          <div>
            <h4>${escapeHTML(lane.label)}</h4>
            <p>${escapeHTML(allocationText)}</p>
          </div>
          <span>${escapeHTML(fmtNum(laneBlocks.length, 0))}</span>
        </div>
        <div class="binance-lane-list">
          ${laneBlocks.map(renderCard).join("") || '<div class="notice compact">블록 없음</div>'}
        </div>
      </section>
    `;
    }).join("");
    return `<div class="binance-lane-board">${laneBoard}</div>`;
  }

  window.HERMES_BINANCE_TAB = Object.freeze({
    lanes: Object.freeze(lanes),
    historyStatuses: Object.freeze(["closed", "error"]),
    activeBlockStatuses,
    blockLane,
    laneLabel,
    blockPrice,
    groupBlocksByLane,
    activeBlocks,
    historyDateKey,
    historyRows,
    historyPerformance,
    growthTargetStatusLabel,
    growthGovernorModeMeta,
    growthUnlockPhaseMeta,
    riskGuardTone,
    filteredHistory,
    renderBlockCard,
    renderBlockHistory,
    latestCandidateGeneration,
    renderCandidatePacketList,
    renderUniversePipeline,
    renderGrowthTarget,
    renderGrowthGovernor,
    renderGrowthUnlock,
    renderLaneEdgePanel,
    renderRiskGuard,
    renderKpiGrid,
    renderLaneBoard,
  });
})();
