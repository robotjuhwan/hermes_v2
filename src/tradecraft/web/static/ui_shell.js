(function () {
  const RESOURCE_KINDS = Object.freeze([
    "loading",
    "auth_required",
    "empty",
    "ready",
    "stale",
    "error",
  ]);

  function asRows(value) {
    return Array.isArray(value) ? value : [];
  }

  function normalizeMode(payload) {
    if (!payload || typeof payload !== "object") return "확인 중";
    const laneModes = [
      payload.execution?.spot_mode,
      payload.execution?.futures_mode,
      payload.execution?.upbit_spot_mode,
      payload.readiness?.execution?.spot_mode,
      payload.readiness?.execution?.futures_mode,
      payload.readiness?.execution?.upbit_spot_mode,
    ].map((value) => String(value || "").trim().toLowerCase()).filter(Boolean);
    if (laneModes.includes("live")) return "LIVE";
    if (laneModes.some((mode) => mode === "paper" || mode === "dry_run")) return "PAPER";
    const mode = String(
      payload.execution_mode
      || payload.execution?.mode
      || payload.mode
      || ""
    ).trim().toLowerCase();
    if (payload.execute_orders === true || mode === "live" || mode === "real") return "LIVE";
    if (payload.execute_orders === false || mode === "paper" || mode === "dry_run") return "PAPER";
    return "확인 중";
  }

  function normalizeResourceState(payload, options = {}) {
    if (options.authRequired) {
      return {
        kind: "auth_required",
        title: options.title || "운영 토큰 필요",
        message: options.message || "보호된 운영 정보를 보려면 인증이 필요합니다.",
        updatedAt: "",
        action: options.action || "토큰 입력",
      };
    }
    if (options.loading) {
      return {
        kind: "loading",
        title: options.title || "불러오는 중",
        message: options.message || "최신 운영 상태를 확인하고 있습니다.",
        updatedAt: "",
        action: "",
      };
    }
    if (options.error) {
      return {
        kind: "error",
        title: options.title || "상태 확인 실패",
        message: String(options.error),
        updatedAt: "",
        action: options.action || "다시 시도",
      };
    }
    if (!payload || typeof payload !== "object") {
      return {
        kind: "empty",
        title: options.title || "표시할 정보 없음",
        message: options.message || "아직 수집된 운영 정보가 없습니다.",
        updatedAt: "",
        action: "",
      };
    }
    const status = String(payload.status || payload.cache_status || "ready").trim().toLowerCase();
    const kind = RESOURCE_KINDS.includes(status)
      ? status
      : status === "ok" || status === "green" ? "ready" : "ready";
    return {
      kind,
      title: options.title || (kind === "stale" ? "마지막 정상 정보" : "운영 정보 준비됨"),
      message: options.message || "",
      updatedAt: String(payload.updated_at || payload.generated_at || payload.as_of || ""),
      action: options.action || "",
    };
  }

  function blockRows(payload) {
    if (!payload || typeof payload !== "object") return [];
    if (Array.isArray(payload.blocks)) return payload.blocks;
    if (Array.isArray(payload.active_blocks)) return payload.active_blocks;
    if (Array.isArray(payload.items)) return payload.items;
    return [];
  }

  function venueSummary(label, payload) {
    const rows = blockRows(payload);
    const activeStates = new Set(["active", "open", "proposed", "paused", "pending"]);
    const failedStates = new Set(["failed", "failed_entry", "rejected", "error"]);
    const killSwitch = Boolean(
      payload?.kill_switch?.enabled
      ?? payload?.kill_switch_enabled
      ?? payload?.summary?.kill_switch?.enabled
      ?? payload?.readiness?.kill_switch?.enabled
    );
    return {
      label,
      mode: normalizeMode(payload),
      killSwitch,
      activeCount: rows.filter((row) => activeStates.has(String(row?.status || "").toLowerCase())).length,
      failedCount: rows.filter((row) => failedStates.has(String(row?.status || "").toLowerCase())).length,
      status: payload ? "ready" : "empty",
    };
  }

  function buildSafetySummary(input = {}) {
    const readiness = input.readiness && typeof input.readiness === "object" ? input.readiness : {};
    const blockers = asRows(readiness.blockers);
    const warnings = asRows(readiness.warnings);
    const kis = venueSummary("KIS 국장", input.kisStatus);
    const binance = venueSummary("Binance", input.binanceStatus);
    const live = readiness.live_trading_enabled === true
      || kis.mode === "LIVE"
      || binance.mode === "LIVE";
    const readinessStatus = String(readiness.status || "unknown").toLowerCase();
    const tone = readinessStatus === "red" || blockers.length || kis.killSwitch || binance.killSwitch
      ? "bad"
      : readinessStatus === "yellow" || warnings.length || input.authRequired
        ? "warn"
        : readinessStatus === "green"
          ? "good"
          : "muted";
    return {
      tone,
      mode: live ? "LIVE" : "PAPER",
      readiness: readinessStatus,
      blockerCount: blockers.length,
      warningCount: warnings.length,
      primaryIssue: String(blockers[0] || warnings[0] || ""),
      updatedAt: String(readiness.updated_at || readiness.generated_at || ""),
      authRequired: Boolean(input.authRequired || !input.hasAdminToken),
      kis,
      binance,
    };
  }

  function renderVenueCard(venue, tab, escape) {
    const modeTone = venue.mode === "LIVE" ? "bad" : venue.mode === "PAPER" ? "good" : "muted";
    const riskText = venue.killSwitch
      ? "킬스위치 활성"
      : venue.failedCount
        ? `실패·거절 ${venue.failedCount}`
        : "실행 차단 없음";
    return `
      <article class="home-ops-card card venue-${escape(tab)}">
        <div class="home-ops-card-head">
          <div>
            <span class="section-kicker">${escape(venue.label)}</span>
            <strong>${escape(venue.activeCount)}개 진행 상태</strong>
          </div>
          <span class="status-chip ${escape(modeTone)}">${escape(venue.mode)}</span>
        </div>
        <p class="${venue.killSwitch || venue.failedCount ? "status-bad" : ""}">${escape(riskText)}</p>
        <button class="btn small ghost" type="button" data-open-helper="${escape(tab)}">작업공간 열기</button>
      </article>
    `;
  }

  function renderHomeOpsSummaryHtml(summary, dependencies = {}) {
    const escape = typeof dependencies.escapeHTML === "function"
      ? dependencies.escapeHTML
      : (value) => String(value ?? "");
    const readinessText = summary.authRequired
      ? "인증 후 운영 상태 확인"
      : summary.blockerCount
        ? `차단 ${summary.blockerCount}건`
        : summary.warningCount
          ? `경고 ${summary.warningCount}건`
          : summary.readiness === "green"
            ? "운영 준비 완료"
            : "운영 상태 확인 중";
    const issue = summary.primaryIssue || (summary.authRequired
      ? "토큰은 이 브라우저 세션에만 저장됩니다."
      : "차단 요인이 감지되면 이 영역에 먼저 표시됩니다.");
    return `
      <article class="home-ops-card home-readiness-card card ${escape(summary.tone)}">
        <div class="home-ops-card-head">
          <div>
            <span class="section-kicker">Safety &amp; Readiness</span>
            <strong>${escape(readinessText)}</strong>
          </div>
          <span class="status-chip ${summary.mode === "LIVE" ? "bad" : "good"}">${escape(summary.mode)}</span>
        </div>
        <p>${escape(issue)}</p>
        ${summary.authRequired ? '<button class="btn small warm" type="button" data-auth-focus="true">운영 토큰 입력</button>' : ""}
      </article>
      ${renderVenueCard(summary.kis, "kis_trader", escape)}
      ${renderVenueCard(summary.binance, "binance_trader", escape)}
    `;
  }

  function renderResourceStateHtml(resource, dependencies = {}) {
    const escape = typeof dependencies.escapeHTML === "function"
      ? dependencies.escapeHTML
      : (value) => String(value ?? "");
    return `
      <section class="resource-state resource-${escape(resource.kind)}" data-resource-state="${escape(resource.kind)}">
        <strong>${escape(resource.title)}</strong>
        ${resource.message ? `<p>${escape(resource.message)}</p>` : ""}
        ${resource.updatedAt ? `<small>업데이트 ${escape(resource.updatedAt)}</small>` : ""}
      </section>
    `;
  }

  function renderWorkspaceJumpNav(venue, dependencies = {}) {
    const escape = typeof dependencies.escapeHTML === "function"
      ? dependencies.escapeHTML
      : (value) => String(value ?? "");
    const cleanVenue = venue === "binance" ? "binance" : "kis";
    const label = cleanVenue === "binance" ? "Binance" : "KIS";
    const items = [
      ["overview", "개요·안전"],
      ["active", "진행·제안"],
      ["history", "기록·실패"],
    ];
    return `
      <nav class="workspace-jump-nav" aria-label="${escape(`${label} 작업공간 구역`)}">
        ${items.map(([key, itemLabel]) => `
          <button class="btn small ghost" type="button" data-workspace-jump="${escape(`${cleanVenue}-workspace-${key}`)}">
            ${escape(itemLabel)}
          </button>
        `).join("")}
      </nav>
    `;
  }

  window.HERMES_UI_SHELL = Object.freeze({
    RESOURCE_KINDS,
    buildSafetySummary,
    normalizeResourceState,
    renderHomeOpsSummaryHtml,
    renderResourceStateHtml,
    renderWorkspaceJumpNav,
  });
})();
