(() => {
  function htmlEscape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function optionHelpers(options = {}) {
    return {
      escapeHTML: typeof options.escapeHTML === "function" ? options.escapeHTML : htmlEscape,
      fmtKRW: typeof options.fmtKRW === "function" ? options.fmtKRW : (value) => String(Math.round(Number(value || 0))),
      fmtKST: typeof options.fmtKST === "function" ? options.fmtKST : (value) => String(value || "--"),
      fmtBytes: typeof options.fmtBytes === "function" ? options.fmtBytes : (value) => `${Math.round(Number(value || 0))} B`,
      normalizeNonNegativeInt:
        typeof options.normalizeNonNegativeInt === "function"
          ? options.normalizeNonNegativeInt
          : (value) => {
              const parsed = Number(value);
              return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null;
            },
      helperStateChip:
        typeof options.helperStateChip === "function"
          ? options.helperStateChip
          : (value) => ({ text: String(value ?? "-"), cls: "neutral" }),
    };
  }

  function renderLLMUsagePanel(viewState, options = {}) {
    const { escapeHTML, fmtKRW } = optionHelpers(options);
    const payload = viewState?.llmUsage;
    const activePeriod = String(viewState?.llmUsagePeriod || payload?.period || "today");
    const periodButtons = `
      <button class="btn small ${activePeriod === "today" ? "warm" : "ghost"}" type="button" data-llm-usage-period="today">오늘</button>
      <button class="btn small ${activePeriod === "7d" ? "warm" : "ghost"}" type="button" data-llm-usage-period="7d">최근 7일</button>
      <button class="btn small ${activePeriod === "all" ? "warm" : "ghost"}" type="button" data-llm-usage-period="all">전체</button>
    `;
    if (!payload) {
      return `
        <section class="memory-section llm-usage-panel">
          <div class="panel-head compact">
            <div>
              <h3>LLM 사용량</h3>
              <p>쥬 판단, 리서치, 메모리의 Codex native 호출 계량</p>
            </div>
            <div class="panel-actions">${periodButtons}</div>
          </div>
          <div class="notice">${escapeHTML(viewState?.llmUsageError || "LLM 사용량 집계 대기")}</div>
        </section>
      `;
    }
    const total = payload.total || {};
    const rows = Array.isArray(payload.by_component) ? payload.by_component : [];
    const periodText =
      activePeriod === "all"
        ? `${payload.start_day || "-"} ~ ${payload.end_day || "-"} · 전체`
        : activePeriod === "7d"
          ? `${payload.start_day || "-"} ~ ${payload.end_day || "-"} · 최근 7일`
          : `${payload.trading_day || ""} · 오늘`;
    return `
      <section class="memory-section llm-usage-panel">
        <div class="panel-head compact">
          <div>
            <h3>LLM 사용량</h3>
            <p>${escapeHTML(periodText)} · 쥬 판단/리서치/메모리 gpt 호출 계량</p>
          </div>
          <div class="panel-actions">${periodButtons}</div>
        </div>
        <div class="strategy-intel-metrics">
          <span><b>${escapeHTML(fmtKRW(total.call_count || 0))}</b>호출</span>
          <span><b>${escapeHTML(fmtKRW(total.total_tokens || 0))}</b>총 토큰</span>
          <span><b>${escapeHTML(fmtKRW(total.estimated_token_count || 0))}</b>추정 집계</span>
          <span><b>${escapeHTML(fmtKRW(total.error_count || 0))}</b>실패</span>
        </div>
        <div class="table-wrap compact">
          <table>
            <thead><tr><th>컴포넌트</th><th>역할</th><th>호출</th><th>토큰</th><th>입력</th><th>출력</th></tr></thead>
            <tbody>
              ${rows.length ? rows.map((row) => `
                <tr>
                  <td>
                    <strong>${escapeHTML(row.label || row.component || "-")}</strong>
                    <small class="muted mono">${escapeHTML(row.component || "-")}</small>
                  </td>
                  <td>${escapeHTML(row.description || "-")}</td>
                  <td>${escapeHTML(fmtKRW(row.call_count || 0))}</td>
                  <td>${escapeHTML(fmtKRW(row.total_tokens || 0))}</td>
                  <td>${escapeHTML(fmtKRW(row.prompt_tokens || 0))}</td>
                  <td>${escapeHTML(fmtKRW(row.completion_tokens || 0))}</td>
                </tr>
              `).join("") : '<tr><td colspan="6">선택한 기간에 LLM 호출 없음</td></tr>'}
            </tbody>
          </table>
        </div>
      </section>
    `;
  }

  function runnerLabel(runnerProcesses, key, fallback) {
    const row = runnerProcesses?.[key] || {};
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
  }

  function kisVenueFromBlockAccount(account) {
    if (!account || typeof account !== "object") return null;
    if (String(account.status || "").toLowerCase() !== "ok") return null;
    const cash = Number(account.cash_krw ?? account.orderable_cash_krw ?? 0);
    const orderableCash = Number(account.orderable_cash_krw ?? cash);
    const positionValue = Number(account.position_value_krw || 0);
    const total = Number(account.total_value_krw || cash + positionValue);
    return {
      id: "kr_stock",
      label: account.account_label || "국장1",
      market: "KRX",
      assets: [
        {
          asset: "KRW",
          asset_name: "KRW",
          kind: "cash",
          qty: cash,
          available: orderableCash,
          value_krw: cash,
        },
      ],
      cash_krw: cash,
      invested_krw: positionValue,
      unrealized_pnl_krw: account.unrealized_pnl_krw || 0,
      total_krw: total,
      cache_status: "kis_blocks_account_fallback",
      cached_at: account.captured_at || "",
      position_count: Number(account.position_count || 0),
    };
  }

  function kisVenuesForRuntimeSnapshot(venues, kisBlockStatus) {
    const kisVenues = (Array.isArray(venues) ? venues : []).filter((row) =>
      ["kr_stock", "kr_stock_2"].includes(String(row.id || ""))
    );
    if (kisVenues.length) return kisVenues;
    const fallback = kisVenueFromBlockAccount(kisBlockStatus?.account);
    return fallback ? [fallback] : [];
  }

  function renderKisAccountSnapshot(venues, kisBlockStatus, options = {}) {
    const { escapeHTML, fmtKRW, fmtKST } = optionHelpers(options);
    const kisVenues = kisVenuesForRuntimeSnapshot(venues, kisBlockStatus);
    if (!kisVenues.length && options.authRequired && !options.hasAdminToken) {
      const message = options.authMessage || "운영 토큰이 필요한 요청입니다.";
      return `
        <article class="helper-card helper-card-wide runtime-kis-snapshot auth-gated">
          <div class="panel-head compact">
            <div>
              <h4>KIS 국장 계좌 인증 대기</h4>
              <p>국장 계좌 데이터 공백이 아니라 보호 API 인증 대기입니다.</p>
            </div>
            <button class="btn small warm" type="button" data-auth-focus="true">운영 토큰 입력</button>
          </div>
          <p class="helper-text">${escapeHTML(message)} 브라우저 세션에 Admin token을 넣으면 국장1/국장2 총자산, 현금, 보유 종목이 표시됩니다. 토큰은 세션에만 저장되므로 재부팅이나 새 브라우저 후에는 다시 입력해야 합니다.</p>
        </article>
      `;
    }
    if (!kisVenues.length) {
      return `
        <article class="helper-card helper-card-wide">
          <h4>KIS 국장 계좌 스냅샷</h4>
          <p class="helper-text">국장 계좌 데이터가 아직 대시보드/KIS 블록 payload에 없습니다.</p>
        </article>
      `;
    }

    const rows = kisVenues
      .map((venue) => {
        const assets = Array.isArray(venue.assets) ? venue.assets : [];
        const positions = assets.filter((asset) => asset.kind !== "cash");
        const cashAsset = assets.find((asset) => asset.kind === "cash") || {};
        const topPositions = positions.slice(0, 5);
        const moreCount = Math.max(positions.length - topPositions.length, 0);
        const positionCount = Number(venue.position_count || positions.length || 0);
        const statusText = venue.cache_status
          ? `${venue.cache_status}${venue.cached_at ? ` · ${fmtKST(venue.cached_at, true)}` : ""}`
          : "live";
        const positionText = topPositions.length
          ? topPositions
              .map((asset) => {
                const name = asset.asset_name || asset.asset || "-";
                const qty = Number(asset.qty || 0);
                return `${name} ${qty.toLocaleString("ko-KR")}주`;
              })
              .join(" · ")
          : positionCount > 0
            ? `보유 ${positionCount.toLocaleString("ko-KR")}종목`
          : "보유 종목 없음";
        const suffix = moreCount > 0 ? ` · 외 ${moreCount}개` : "";
        return `
          <li>
            <div>
              <strong>${escapeHTML(venue.label || venue.id || "국장")}</strong>
              <span>${escapeHTML(positionText + suffix)}</span>
            </div>
            <div class="runtime-kis-values">
              <span>총 ${escapeHTML(fmtKRW(venue.total_krw))}</span>
              <span>현금 ${escapeHTML(fmtKRW(venue.cash_krw ?? cashAsset.cash_krw ?? cashAsset.value_krw))}</span>
              <span class="${Number(venue.unrealized_pnl_krw || 0) >= 0 ? "gain" : "loss"}">손익 ${escapeHTML(fmtKRW(venue.unrealized_pnl_krw))}</span>
              <em>${escapeHTML(statusText)}</em>
            </div>
          </li>
        `;
      })
      .join("");

    return `
      <article class="helper-card helper-card-wide runtime-kis-snapshot">
        <div class="panel-head compact">
          <div>
            <h4>KIS 국장 계좌 스냅샷</h4>
            <p>국장1/국장2 총자산, 현금, 보유 종목을 시스템 화면에서도 바로 확인</p>
          </div>
        </div>
        <ul class="runtime-kis-list">
          ${rows}
        </ul>
      </article>
    `;
  }

  function renderTab(viewState, options = {}) {
    const {
      escapeHTML,
      fmtKRW,
      fmtKST,
      fmtBytes,
      normalizeNonNegativeInt,
      helperStateChip,
    } = optionHelpers(options);
    const health = viewState?.healthStatus;
    const healthError = viewState?.healthError;
    const reports = viewState?.reportsStatus;
    const ops = viewState?.opsReadiness || {};
    const dashboard = viewState?.dashboard || {};
    const runtime = dashboard.runtime || {};
    const storage = viewState?.runtimeStorage || {};
    const storageCleanup = viewState?.runtimeStorageCleanup || {};
    const cleanupCandidates = storage.cleanup_candidates || {};
    const retainedArtifacts = storage.retained_artifacts || {};
    const retainedRagRebuildBackups = retainedArtifacts.rag_rebuild_backups || {};
    const unrefPdfs = cleanupCandidates.unreferenced_report_pdfs || {};
    const extractedPdfs = cleanupCandidates.extracted_report_pdfs || {};
    const zeroByteSqlite = cleanupCandidates.zero_byte_sqlite_placeholders || {};
    const zeroByteMarkers = cleanupCandidates.zero_byte_runtime_markers || {};
    const compactCandidates = Array.isArray(storage.database_compact_candidates)
      ? storage.database_compact_candidates
      : [];
    const compactCandidateBytes = compactCandidates.reduce(
      (total, row) => total + Number(row?.free_bytes || 0),
      0,
    );
    const databaseGrowthPressure = Array.isArray(storage.database_growth_pressure)
      ? storage.database_growth_pressure
      : [];
    const databaseGrowthRows = databaseGrowthPressure.slice(0, 6);
    const databaseGrowthPressureBytes = Number(storage.database_growth_pressure_bytes || 0);
    const databaseGrowthArchiveRows = normalizeNonNegativeInt(storage.database_growth_pressure_archive_rows);
    const databaseSummaries = storage.database_summaries || {};
    const ragChroma = databaseSummaries.rag_chroma || {};
    const ragDiagnostics = ragChroma.diagnostics || {};
    const ragContentBytes = ragChroma.content_bytes || {};
    const ragQueue = ragDiagnostics.queue || {};
    const ragTopMetadata = Array.isArray(ragDiagnostics.metadata_key_bytes)
      ? ragDiagnostics.metadata_key_bytes.slice(0, 5)
      : [];
    const venues = Array.isArray(dashboard.venues) ? dashboard.venues : [];
    const kisBlockStatus = viewState?.kisBlockStatus || {};

    const opsProcesses = ops.processes || {};
    const runtimeStatus = runtime.status || health?.runtime_status || "-";
    const researchStatus = reports?.rag?.available || reports?.intelligence?.llm_facts?.active
      ? "active"
      : dashboard.research?.status || health?.research_status || "-";
    const naverReportsEnabled = ops.reports?.enabled ?? health?.naver_reports_enabled ?? Boolean(reports?.repository);
    const blockTraderProcess = opsProcesses.kis_block_trader || {};
    const blockTraderEnabled = health?.kis_block_trader_enabled ?? Boolean(blockTraderProcess.alive || blockTraderProcess.effective_alive);
    const blockTraderMode =
      health?.kis_block_trader_execution_mode
      || ops.kis_block_trader?.execution_mode
      || blockTraderProcess.status
      || "-";

    const systemRows = [
      { label: "API 상태", value: health?.status || (healthError ? "offline" : "-") },
      { label: "Runtime 데이터 상태", value: runtimeStatus },
      { label: "Runtime 역할", value: runtime.role_label || health?.runtime_role || "-" },
      {
        label: "Runtime 주문 권한",
        value: `${runtime.execution_mode || health?.runtime_execution_mode || "-"} · orders ${
          runtime.executes_orders || health?.runtime_executes_orders ? "on" : "off"
        }`,
      },
      { label: "Research/RAG 상태", value: researchStatus },
      { label: "Naver Reports", value: naverReportsEnabled ? "enabled" : "disabled" },
      { label: "Block Trader", value: `${blockTraderMode} · ${blockTraderEnabled ? "enabled" : "disabled"}` },
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

    const runnerProcesses = ops.processes || health?.runner_processes || {};
    const hasRunnerProcess = (key) => Boolean(
      runnerProcesses
        && Object.prototype.hasOwnProperty.call(runnerProcesses, key)
    );
    const healthHas = (key) => Boolean(health && Object.prototype.hasOwnProperty.call(health, key));
    const healthBool = (key) => (healthHas(key) ? Boolean(health[key]) : undefined);
    const controlFallback = healthHas("status") ? health.status === "ok" : undefined;
    const runnerRow = (label, key, fallback) => ({
      label,
      value: runnerLabel(runnerProcesses, key, fallback),
    });
    const optionalRunnerRow = (label, key, fallback) => (
      hasRunnerProcess(key) || fallback !== undefined ? runnerRow(label, key, fallback) : null
    );
    const runnerRows = [
      { label: "control API", value: runnerLabel(runnerProcesses, "control", controlFallback) },
      { label: "runtime runner", value: runnerLabel(runnerProcesses, "runtime", healthBool("runtime_runner_alive")) },
      optionalRunnerRow("intelligence runner (optional)", "intelligence"),
      optionalRunnerRow("research runner (optional)", "research", healthBool("research_runner_alive")),
      optionalRunnerRow("reports crawler", "naver_reports"),
      optionalRunnerRow("strategy insight runner", "strategy_insights"),
      { label: "Block trader runner", value: runnerLabel(runnerProcesses, "kis_block_trader", healthBool("kis_block_trader_runner_alive")) },
      { label: "Binance trader runner", value: runnerLabel(runnerProcesses, "binance_block_trader", healthBool("binance_block_trader_runner_alive")) },
      optionalRunnerRow("crypto research runner", "crypto_market_research"),
      optionalRunnerRow("crypto pattern runner", "crypto_pattern_lab"),
      optionalRunnerRow("crypto alpha runner", "crypto_alpha"),
      optionalRunnerRow("live evaluator runner", "live_evaluator"),
      optionalRunnerRow("Jue wiki runner", "jue_wiki"),
      { label: "memory runner", value: runnerLabel(runnerProcesses, "investment_memory", healthBool("investment_memory_runner_alive")) },
      { label: "market judge runner", value: runnerLabel(runnerProcesses, "market_judge", healthBool("market_judge_runner_alive")) },
      { label: "market pulse runner", value: runnerLabel(runnerProcesses, "market_pulse", healthBool("market_pulse_runner_alive")) },
      { label: "watchdog runner", value: runnerLabel(runnerProcesses, "watchdog", healthBool("watchdog_runner_alive")) },
    ].filter(Boolean);

    const reportTotal = Number(reports?.repository?.total_reports || 0);
    const reportRepository = reports?.repository || {};
    const learningTotalCount = normalizeNonNegativeInt(dashboard.research?.learning_total_count);
    const llmFacts = reports?.intelligence?.llm_facts || {};
    const codexRuntime = reports?.intelligence?.codex_runtime || {};
    const fundamentals = reports?.fundamentals || {};
    const reportUpdated = reports?.repository?.last_updated_at
      ? fmtKST(reports.repository.last_updated_at, true)
      : "--";
    const fundamentalsUpdated = fundamentals.latest_crawled_at
      ? fmtKST(fundamentals.latest_crawled_at, true)
      : "--";
    const fundamentalsTotalSymbols = Number(fundamentals.total_symbols || 0);
    const fundamentalsFreshSymbols = Number(fundamentals.fresh_symbol_count || 0);
    const fundamentalsStaleSymbols = Number(fundamentals.stale_symbol_count || 0);
    const fundamentalsStaleRatio = Number(fundamentals.stale_ratio || 0);
    const fundamentalsFreshnessLabel = fundamentalsTotalSymbols > 0
      ? `${String(fundamentalsFreshSymbols)} fresh · ${String(fundamentalsStaleSymbols)} stale · ${Math.round(fundamentalsStaleRatio * 100)}% stale`
      : "no symbols";
    const reportQuality = reports?.repository?.quality || {};
    const ragAvailable = reports?.rag?.available ? "available" : "unavailable";
    const ragCount = Number(reports?.rag?.count || 0);
    const llmFactsLabel = llmFacts.active ? "active" : llmFacts.enabled ? "waiting" : "off";
    const dataRows = [
      { label: "reports db", value: String(reportTotal) },
      { label: "reports updated", value: reportUpdated },
      { label: "report symbol links", value: String(reportRepository.symbol_link_count || 0) },
      { label: "ETF linked reports", value: String(reportRepository.linked_report_count || 0) },
      { label: "ETF symbol links", value: String(reportRepository.etf_link_count || 0) },
      { label: "unlinked ETF keyword reports", value: String(reportRepository.unlinked_etf_keyword_report_count || 0) },
      {
        label: "symbol links updated",
        value: reportRepository.last_symbol_link_updated_at
          ? fmtKST(reportRepository.last_symbol_link_updated_at, true)
          : "--",
      },
      { label: "fundamentals db", value: String(fundamentals.total_snapshots || 0) },
      { label: "fundamentals symbols", value: String(fundamentalsTotalSymbols) },
      { label: "fundamentals freshness", value: fundamentalsFreshnessLabel },
      { label: "fundamentals updated", value: fundamentalsUpdated },
      { label: "fundamentals errors", value: String(fundamentals.error_count || 0) },
      { label: "report identity suspect", value: String(reportQuality.identity_suspect_count || 0) },
      { label: "symbol drift", value: String(reportQuality.symbol_directory_drift_count || 0) },
      { label: "rag status", value: ragAvailable },
      { label: "rag chunks", value: String(ragCount) },
      { label: "llm facts", value: `${llmFactsLabel} · ${codexRuntime.mode || "none"}` },
      { label: "누적 학습 횟수", value: learningTotalCount === null ? "-" : String(learningTotalCount) },
      { label: "runtime cycle", value: runtime.cycle === undefined ? "-" : String(runtime.cycle) },
      { label: "runtime sessions", value: runtime.sessions === undefined ? "-" : String(runtime.sessions) },
      {
        label: ".runtime size",
        value:
          storage.total_size_mb === undefined
            ? storage.total_bytes === undefined ? "-" : fmtBytes(storage.total_bytes)
            : `${storage.total_size_mb}MB · ${fmtBytes(storage.total_bytes || 0)}`,
      },
      {
        label: "large files",
        value:
          storage.large_file_count === undefined
            ? "-"
            : `${storage.large_file_count}개 · ${fmtBytes(storage.total_bytes || 0)} total`,
      },
      {
        label: "cleanup candidates",
        value:
          storage.cleanup_candidate_count === undefined
            ? "-"
            : `${storage.cleanup_candidate_count}개 · ${
                storage.cleanup_candidate_size_mb === undefined
                  ? fmtBytes(storage.cleanup_candidate_bytes || 0)
                  : `${storage.cleanup_candidate_size_mb}MB`
              }`,
      },
      {
        label: "unref PDFs",
        value:
          unrefPdfs.count === undefined
            ? "-"
            : `${unrefPdfs.count}개 · ${fmtBytes(unrefPdfs.bytes)}`,
      },
      {
        label: "extracted PDFs",
        value:
          extractedPdfs.count === undefined
            ? "-"
            : `${extractedPdfs.count}개 · ${fmtBytes(extractedPdfs.bytes)}`,
      },
      {
        label: "zero sqlite",
        value:
          zeroByteSqlite.count === undefined
            ? "-"
            : `${zeroByteSqlite.count}개 · ${fmtBytes(zeroByteSqlite.bytes)}`,
      },
      {
        label: "zero markers",
        value:
          zeroByteMarkers.count === undefined
            ? "-"
            : `${zeroByteMarkers.count}개 · ${fmtBytes(zeroByteMarkers.bytes)}`,
      },
      {
        label: "compact DBs",
        value: `${compactCandidates.length}개 · ${fmtBytes(compactCandidateBytes)}`,
      },
      {
        label: "DB growth pressure",
        value:
          storage.database_growth_pressure_count === undefined
            ? "-"
            : `${storage.database_growth_pressure_count}개 · top ${
                databaseGrowthRows[0]?.key || "-"
              }`,
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
    const cleanupResult = storageCleanup.result || {};
    const cleanupAfter = cleanupResult.after || {};
    const cleanupDryRun = Boolean(cleanupResult.dry_run);
    const cleanupActionCount = cleanupDryRun
      ? cleanupResult.would_delete_count ?? cleanupResult.deleted_count
      : cleanupResult.actual_deleted_count ?? cleanupResult.deleted_count;
    const cleanupActionBytes = cleanupDryRun
      ? cleanupResult.would_delete_bytes ?? cleanupResult.deleted_bytes
      : cleanupResult.actual_deleted_bytes ?? cleanupResult.deleted_bytes;
    const cleanupStatusText = storageCleanup.running
      ? "정리 실행 중"
      : storageCleanup.error
        ? `정리 오류 · ${storageCleanup.error}`
        : cleanupActionCount === undefined
          ? "dry-run으로 삭제 후보를 먼저 확인할 수 있습니다."
          : `${cleanupDryRun ? "정리 후보" : "정리 완료"} · ${cleanupActionCount}개 · ${fmtBytes(cleanupActionBytes || 0)}`;
    const cleanupAfterText = cleanupAfter.cleanup_candidate_count === undefined
      ? ""
      : `남은 후보 ${cleanupAfter.cleanup_candidate_count}개 · ${fmtBytes(cleanupAfter.cleanup_candidate_bytes || 0)}`;
    const cleanupCanRun = Number(storage.cleanup_candidate_count || 0) > 0 || compactCandidates.length > 0;
    const cleanupDisabled = storageCleanup.running || !cleanupCanRun ? "disabled" : "";
    const cleanupCard = `
      <article class="helper-card helper-card-wide">
        <div class="panel-head compact">
          <div>
            <h4>Runtime 정리</h4>
            <p>오래된 로그, scratch, 미사용 PDF, 0바이트 marker를 안전 정책에 맞춰 정리합니다.</p>
          </div>
          <div class="panel-actions">
            <button class="btn small ghost" type="button" data-runtime-storage-cleanup="dry-run" ${storageCleanup.running ? "disabled" : ""}>dry-run</button>
            <button class="btn small warm" type="button" data-runtime-storage-cleanup="apply" ${cleanupDisabled}>정리 실행</button>
          </div>
        </div>
        <div class="strategy-intel-metrics">
          <span><b>${escapeHTML(String(storage.cleanup_candidate_count ?? "-"))}</b>후보</span>
          <span><b>${escapeHTML(fmtBytes(storage.cleanup_candidate_bytes || 0))}</b>정리 가능</span>
          <span><b>${escapeHTML(String(compactCandidates.length))}</b>compact DB</span>
          <span><b>${escapeHTML(fmtBytes(databaseGrowthPressureBytes))}</b>DB pressure</span>
          <span><b>${escapeHTML(String(databaseGrowthArchiveRows))}</b>archive rows</span>
        </div>
        <p class="helper-text">${escapeHTML(cleanupStatusText)}</p>
        ${cleanupAfterText ? `<p class="helper-text">${escapeHTML(cleanupAfterText)}</p>` : ""}
      </article>
    `;
    const retainedRagRebuildCount = Number(retainedRagRebuildBackups.count || 0);
    const retainedRagRebuildSample = Array.isArray(retainedRagRebuildBackups.sample)
      ? retainedRagRebuildBackups.sample.slice(0, 3)
      : [];
    const retainedRagRebuildCard = retainedRagRebuildCount > 0
      ? `
        <article class="helper-card helper-card-wide">
          <div class="panel-head compact">
            <div>
              <h4>RAG 재빌드 백업</h4>
              <p>현재 벡터 저장소 복구용으로 남겨 둔 보존 대상입니다. retention이 지나면 cleanup 정책으로 정리됩니다.</p>
            </div>
            <strong class="helper-runtime-chip neutral">
              ${escapeHTML(`${retainedRagRebuildCount}개 · ${fmtBytes(retainedRagRebuildBackups.bytes || 0)}`)}
            </strong>
          </div>
          <ul class="helper-runtime-list">
            <li>
              <span>보존 대상</span>
              <strong class="helper-runtime-chip neutral">
                ${escapeHTML(`${retainedRagRebuildBackups.retention_days ?? "-"}일 retention`)}
              </strong>
            </li>
            ${retainedRagRebuildSample.map((path) => `
              <li>
                <span>${escapeHTML(path)}</span>
                <strong class="helper-runtime-chip neutral">retained</strong>
              </li>
            `).join("")}
          </ul>
        </article>
      `
      : "";
    const ragHealthClass = Number(ragDiagnostics.duplicate_embedding_ids || 0) > 0
      ? "bad"
      : ragChroma.status === "ok" ? "good" : "neutral";
    const ragStorageCard = `
      <article class="helper-card helper-card-wide">
        <div class="panel-head compact">
          <div>
            <h4>RAG 저장소 진단</h4>
            <p>리포트 벡터 저장소가 정상 누적인지, 중복 삽입인지 빠르게 분해합니다.</p>
          </div>
          <strong class="helper-runtime-chip ${ragHealthClass}">
            ${escapeHTML(ragChroma.status || "unknown")}
          </strong>
        </div>
        <div class="strategy-intel-metrics">
          <span><b>${escapeHTML(String(ragDiagnostics.embedding_count ?? ragChroma.tables?.embeddings ?? "-"))}</b>vectors</span>
          <span><b>${escapeHTML(String(ragDiagnostics.duplicate_embedding_ids ?? "-"))}</b>duplicate ids</span>
          <span><b>${escapeHTML(fmtBytes(ragDiagnostics.document_metadata_bytes || 0))}</b>document metadata</span>
          <span><b>${escapeHTML(fmtBytes(ragDiagnostics.fulltext_document_bytes || ragContentBytes["embedding_fulltext_search_content.c0"] || 0))}</b>fulltext</span>
          <span><b>${escapeHTML(String(ragQueue.rows ?? ragChroma.tables?.embeddings_queue ?? "-"))}</b>queue</span>
        </div>
        <ul class="helper-runtime-list">
          <li>
            <span>metadata strings</span>
            <strong class="helper-runtime-chip neutral">${escapeHTML(fmtBytes(ragContentBytes["embedding_metadata.string_value"] || 0))}</strong>
          </li>
          <li>
            <span>queue payload</span>
            <strong class="helper-runtime-chip neutral">${escapeHTML(fmtBytes((ragQueue.vector_bytes || 0) + (ragQueue.metadata_bytes || 0) + (ragQueue.encoding_bytes || 0)))}</strong>
          </li>
          ${ragTopMetadata.length ? ragTopMetadata.map((row) => `
            <li>
              <span>${escapeHTML(`metadata · ${row.key || "-"}`)}</span>
              <strong class="helper-runtime-chip neutral">${escapeHTML(`${fmtBytes(row.bytes || 0)} · ${row.rows ?? "-"} rows`)}</strong>
            </li>
          `).join("") : `
            <li>
              <span>metadata breakdown</span>
              <strong class="helper-runtime-chip neutral">waiting</strong>
            </li>
          `}
        </ul>
      </article>
    `;
    const databaseGrowthCard = `
      <article class="helper-card helper-card-wide">
        <div class="panel-head compact">
          <div>
            <h4>DB 성장 압력</h4>
            <p>삭제 후보가 0이어도 어떤 DB와 테이블이 공간을 밀어 올리는지 분해합니다.</p>
          </div>
          <strong class="helper-runtime-chip ${databaseGrowthRows.length ? "warn" : "good"}">
            ${escapeHTML(String(storage.database_growth_pressure_count ?? 0))}개
          </strong>
        </div>
        <ul class="helper-runtime-list">
          <li>
            <span>압박 총량 / archive rows</span>
            <strong class="helper-runtime-chip neutral">
              ${escapeHTML(`${fmtBytes(databaseGrowthPressureBytes)} · ${databaseGrowthArchiveRows} archive rows`)}
            </strong>
          </li>
          ${databaseGrowthRows.length ? databaseGrowthRows.map((row) => {
            const tables = Array.isArray(row.largest_tables)
              ? row.largest_tables.slice(0, 2)
              : [];
            const tableText = tables.length
              ? tables.map((table) => `${table.table || "-"} ${table.rows ?? 0}`).join(" · ")
              : "table breakdown 대기";
            const reasons = Array.isArray(row.reasons) ? row.reasons.join(", ") : "-";
            const actionLabel = row.action_label || row.action || "monitor";
            const reclaimability = row.reclaimability ? ` · ${row.reclaimability}` : "";
            const retention = row.archive_retention_status && row.archive_retention_status !== "none"
              ? ` · archive ${row.archive_retention_status}/${row.archive_retention_days || "-"}d`
              : "";
            return `
              <li>
                <span>${escapeHTML(`${row.key || row.group || "db"} · ${reasons} · ${actionLabel}${reclaimability}${retention}`)}</span>
                <strong class="helper-runtime-chip neutral">
                  ${escapeHTML(`${fmtBytes(row.bytes || 0)} · ${tableText}`)}
                </strong>
              </li>
            `;
          }).join("") : `
            <li>
              <span>성장 압력</span>
              <strong class="helper-runtime-chip good">stable</strong>
            </li>
          `}
        </ul>
      </article>
    `;

    return `
      ${renderKisAccountSnapshot(venues, kisBlockStatus, {
        ...options,
        authRequired: Boolean(viewState?.auth?.required),
        hasAdminToken: Boolean(viewState?.hasAdminToken),
        authMessage: viewState?.auth?.message || "",
      })}
      ${renderLLMUsagePanel(viewState, options)}
      ${cleanupCard}
      ${retainedRagRebuildCard}
      ${ragStorageCard}
      ${databaseGrowthCard}
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

  window.HERMES_RUNTIME_TAB = {
    renderLLMUsagePanel,
    runnerLabel,
    renderTab,
  };
})();
