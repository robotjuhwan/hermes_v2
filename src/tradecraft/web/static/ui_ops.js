(function () {
  const UI_SHARED = window.HERMES_UI_SHARED || {};
  const UI_FORMATTERS = window.HERMES_UI_FORMATTERS || {};
  const {
    escapeHTML,
    fmtPercent,
    truncateWithEllipsis,
  } = UI_FORMATTERS;

  function formatOpsSignalLabel(value) {
    const key = String(value || "").trim();
    if (!key) return "";
    if (UI_SHARED.opsSignalLabels?.[key]) return UI_SHARED.opsSignalLabels[key];
    const runnerMatch = key.match(/^(.+)_runner_stopped$/);
    if (runnerMatch) {
      return `${runnerMatch[1].replaceAll("_", " ")} 러너 중지`;
    }
    if (key.startsWith("trading_validation_")) {
      return key
        .replace(/^trading_validation_/, "거래 검증 ")
        .replaceAll("_", " ");
    }
    return key.replaceAll("_", " ");
  }

  function formatOpsSignalList(items) {
    const labels = Array.isArray(items)
      ? items.map((item) => formatOpsSignalLabel(item)).filter(Boolean)
      : [];
    return labels.length ? labels.join(" · ") : "모든 핵심 루프가 준비 상태입니다.";
  }

  function processLabelForOpsSummary(key, processes) {
    const process = processes?.[key] && typeof processes[key] === "object"
      ? processes[key]
      : {};
    return String(process.label || key || "-").replaceAll("_", " ");
  }

  function formatOpsRestartProcessSummary(readiness, limit = 4) {
    const payload = readiness && typeof readiness === "object" ? readiness : {};
    const processes = payload.processes && typeof payload.processes === "object"
      ? payload.processes
      : {};
    const rows = [];
    const appendRows = (items, suffix) => {
      if (!Array.isArray(items)) return;
      items.forEach((key) => {
        const label = processLabelForOpsSummary(String(key || ""), processes);
        if (label && label !== "-") rows.push(`${label} ${suffix}`);
      });
    };
    appendRows(payload.stale_processes, "재시작 필요");
    appendRows(payload.missing_processes, "중지");
    appendRows(payload.duplicate_processes, "중복 실행");
    if (!rows.length) return "";
    const visible = rows.slice(0, Math.max(Number(limit || 0), 1));
    const more = rows.length > visible.length ? ` · 외 ${rows.length - visible.length}개` : "";
    return `대상 ${visible.join(" · ")}${more}`;
  }

  function renderOpsRemediationActions(actions, limit = 3) {
    const rows = Array.isArray(actions) ? actions.filter((row) => row && typeof row === "object") : [];
    if (!rows.length) return "";
    return `
      <div class="ops-action-row" aria-label="운영 다음 조치">
        <span class="ops-action-label">다음 조치</span>
        ${rows.slice(0, limit).map((row) => {
          const severity = String(row.severity || "warn").toLowerCase();
          const tone = severity === "blocker" ? "bad" : severity === "ok" ? "good" : "warn";
          const endpoint = row.endpoint ? ` data-ops-action-endpoint="${escapeHTML(row.endpoint)}"` : "";
          return `
            <span class="ops-action-card ${escapeHTML(tone)}"${endpoint}>
              <strong>${escapeHTML(row.label || row.id || "조치")}</strong>
              <small>${escapeHTML(row.detail || row.method || "")}</small>
            </span>
          `;
        }).join("")}
      </div>
    `;
  }

  function renderOpsAdvisoryDetails(details, limit = 4) {
    const rows = Array.isArray(details)
      ? details.filter((row) => row && typeof row === "object")
      : [];
    if (!rows.length) return "";
    return `
      <div class="ops-advisory-row" aria-label="전략 advisory 상세">
        <span class="ops-action-label">advisory 원인</span>
        ${rows.slice(0, limit).map((row) => {
          const failCount = Number(row.diagnostic_fail_count || row.fail_count || 0);
          const reducedLaneCount = Number(row.reduced_lane_count || 0);
          const scaleBlockedLaneCount = Number(row.scale_blocked_lane_count || reducedLaneCount || 0);
          const probeLaneCount = Number(row.probe_lane_count || 0);
          const tone = failCount > 0
            ? "bad"
            : scaleBlockedLaneCount > 0 || String(row.readiness || "").toLowerCase() === "probe"
              ? "warn"
              : "good";
          const sampleText = Number(row.min_samples_to_scale || 0) > 0
            ? `표본 ${Number(row.sample_count || 0)}/${Number(row.min_samples_to_scale || 0)}`
            : "";
          const failText = failCount > 0 ? `fail ${failCount}` : "";
          const probeText = probeLaneCount > 0 ? `탐색 ${probeLaneCount}` : "";
          const laneText = scaleBlockedLaneCount > 0 ? `확대 제한 ${scaleBlockedLaneCount}` : "";
          const meta = [sampleText, failText, probeText, laneText].filter(Boolean).join(" · ");
          const failedIds = Array.isArray(row.failed_discipline_ids)
            ? row.failed_discipline_ids.slice(0, 3).join(", ")
            : "";
          const note = row.note || failedIds || formatOpsSignalLabel(row.signal);
          const topBottlenecks = Array.isArray(row.top_bottlenecks)
            ? row.top_bottlenecks.filter((item) => item && typeof item === "object").slice(0, 2)
            : [];
          const bottleneckText = topBottlenecks.map((item) => {
            const label = item.label || item.id || "병목";
            const evidence = item.evidence || item.action || item.status || "";
            return `${label}: ${truncateWithEllipsis(evidence, 52)}`;
          }).join(" / ");
          return `
            <span class="ops-advisory-detail ${escapeHTML(tone)}">
              <b>${escapeHTML(String(row.venue || "ALL").toUpperCase())}</b>
              <strong>${escapeHTML(formatOpsSignalLabel(row.signal || row.readiness || "-"))}</strong>
              <small>${escapeHTML(meta || row.diagnostic_status || row.readiness || "-")}</small>
              <em>${escapeHTML(truncateWithEllipsis(note, 96))}</em>
              ${bottleneckText ? `<i>병목 ${escapeHTML(bottleneckText)}</i>` : ""}
            </span>
          `;
        }).join("")}
      </div>
    `;
  }

  function renderTradingValidationCostAttribution(tradingValidation, limit = 3) {
    const venues = tradingValidation?.venues && typeof tradingValidation.venues === "object"
      ? tradingValidation.venues
      : {};
    const costRows = [];
    Object.entries(venues).forEach(([venue, venuePayload]) => {
      const disciplines = Array.isArray(venuePayload?.payload?.disciplines)
        ? venuePayload.payload.disciplines
        : Array.isArray(venuePayload?.disciplines)
          ? venuePayload.disciplines
          : [];
      const costDiscipline = disciplines.find((row) => row?.id === "cost_simulation");
      const metric = costDiscipline?.metric && typeof costDiscipline.metric === "object"
        ? costDiscipline.metric
        : {};
      const groups = Array.isArray(metric.worst_cost_groups)
        ? metric.worst_cost_groups
        : [];
      const rows = Array.isArray(metric.worst_cost_rows)
        ? metric.worst_cost_rows
        : [];
      groups.slice(0, 2).forEach((group) => {
        const drag = Number(group.cost_drag_pct_of_abs_gross_pnl || 0);
        if (!drag) return;
        costRows.push({
          kind: "group",
          venue,
          label: `${group.group_type || "group"}=${group.group || "-"}`,
          drag,
          netNegativeAfterCost: Boolean(group.net_negative_after_cost),
        });
      });
      rows
        .filter((row) => row?.net_negative_after_cost)
        .slice(0, 2)
        .forEach((row) => {
          costRows.push({
            kind: "row",
            venue,
            label: `${row.symbol || "-"} · ${row.horizon || "-"}`,
            drag: Number(row.cost_drag_pct_of_abs_gross_pnl || 0),
            netNegativeAfterCost: true,
          });
        });
    });
    const visible = costRows
      .sort((left, right) => {
        if (left.kind !== right.kind) return left.kind === "row" ? -1 : 1;
        return Number(right.drag || 0) - Number(left.drag || 0);
      })
      .slice(0, limit);
    if (!visible.length) return "";
    return `
      <span class="ops-action-label">비용 귀속</span>
      ${visible.map((row) => `
        <span class="ops-validation-cost ${row.netNegativeAfterCost ? "bad" : "warn"}">
          <b>${escapeHTML(String(row.venue || "").toUpperCase() || "ALL")}</b>
          <strong>${escapeHTML(row.kind === "row" ? "비용 역전" : "비용 취약")}</strong>
          <small>${escapeHTML(truncateWithEllipsis(row.label || "-", 42))} · ${escapeHTML(fmtPercent(row.drag || 0))}</small>
        </span>
      `).join("")}
    `;
  }

  function renderTradingValidationBottleneckSummary(tradingValidation, limit = 4) {
    const summary = tradingValidation?.summary && typeof tradingValidation.summary === "object"
      ? tradingValidation.summary
      : {};
    const readiness = String(tradingValidation?.readiness || summary.readiness || "").trim();
    const diagnosticStatus = String(
      tradingValidation?.diagnostic_status || summary.diagnostic_status || ""
    ).trim();
    const disciplineCount = Number(tradingValidation?.discipline_count || 0);
    const expectedDisciplineCount = Number(tradingValidation?.expected_discipline_count || 0);
    const failCount = Number(summary.fail_count || 0);
    const warnCount = Number(summary.warn_count || 0);
    const missingCount = Number(summary.missing_count || 0);
    const bottlenecks = Array.isArray(tradingValidation?.bottlenecks)
      ? tradingValidation.bottlenecks.filter((row) => row && typeof row === "object")
      : [];
    const nextActions = Array.isArray(tradingValidation?.primary_next_actions)
      ? tradingValidation.primary_next_actions.filter((row) => row && typeof row === "object")
      : [];
    const hasAggregate = Boolean(readiness || diagnosticStatus || disciplineCount || expectedDisciplineCount || failCount || warnCount || missingCount);
    if (!hasAggregate && !bottlenecks.length && !nextActions.length) return "";
    const aggregateTone = diagnosticStatus === "risk_repair" || failCount > 0 || readiness === "blocked_by_validation"
      ? "bad"
      : ["watch", "incomplete"].includes(diagnosticStatus) || warnCount > 0 || missingCount > 0 || ["research_only", "probe"].includes(readiness)
        ? "warn"
        : "good";
    const countText = disciplineCount && expectedDisciplineCount
      ? `${disciplineCount}/${expectedDisciplineCount}`
      : disciplineCount
        ? `${disciplineCount}`
        : "-";
    const issueText = `fail ${failCount} · warn ${warnCount} · missing ${missingCount}`;
    const statusLabel = diagnosticStatus && diagnosticStatus !== "clear"
      ? `${formatValidationGateLabel(readiness || "-")} · ${formatValidationGateLabel(diagnosticStatus)}`
      : formatValidationGateLabel(readiness || "-");
    const aggregateRow = hasAggregate ? `
      <span class="ops-validation-state ${escapeHTML(aggregateTone)}">
        <b>19검증 집계</b>
        <strong>${escapeHTML(statusLabel)}</strong>
        <small>${escapeHTML(countText)} · ${escapeHTML(issueText)}</small>
      </span>
    ` : "";
    const actionRows = nextActions.slice(0, 2).map((row) => `
      <span class="ops-validation-action">
        <b>${escapeHTML(String(row.venue || "").toUpperCase() || "ALL")}</b>
        ${escapeHTML(truncateWithEllipsis(row.action || "-", 80))}
      </span>
    `).join("");
    const costAttributionRows = renderTradingValidationCostAttribution(tradingValidation, 3);
    const bottleneckRows = bottlenecks.slice(0, limit).map((row) => {
      const tone = tradingValidationTone(row.status);
      return `
        <span class="ops-validation-bottleneck ${escapeHTML(tone)}">
          <b>${escapeHTML(String(row.venue || "").toUpperCase())}</b>
          ${escapeHTML(row.label || row.id || "검증 항목")}
          <small>${escapeHTML(row.status || "-")}</small>
        </span>
      `;
    }).join("");
    return `
      <div class="ops-validation-summary" aria-label="19검증 병목">
        ${aggregateRow}
        <span class="ops-action-label">19검증 병목</span>
        ${bottleneckRows}
        ${costAttributionRows}
        ${actionRows ? `<span class="ops-action-label">최우선 복구</span>${actionRows}` : ""}
      </div>
    `;
  }

  function formatValidationGateLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    if (!key) return "-";
    return UI_SHARED.validationGateLabels?.[key] || key.replaceAll("_", " ");
  }

  function formatValidationEvidenceLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    if (!key) return "-";
    return UI_SHARED.validationEvidenceLabels?.[key] || key.replaceAll("_", " ");
  }

  function validationEvidenceTone(value) {
    const key = String(value || "").trim().toLowerCase();
    if (key === "validated") return "good";
    if (["failed"].includes(key)) return "bad";
    if (["missing", "partial"].includes(key)) return "warn";
    return "neutral";
  }

  function formatCostEvidenceLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    if (!key) return "-";
    return UI_SHARED.costEvidenceLabels?.[key] || key.replaceAll("_", " ");
  }

  function costEvidenceTone(value) {
    const key = String(value || "").trim().toLowerCase();
    if (key === "recorded_enough") return "good";
    if (["hybrid_needs_market_cost_repair", "estimated_or_missing"].includes(key)) return "warn";
    if (key === "no_alpha_cost_samples") return "muted";
    return "neutral";
  }

  function formatRiskGovernorLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    if (!key || key === "-") return "-";
    return UI_SHARED.riskGovernorLabels?.[key] || key.replaceAll("_", " ");
  }

  function formatRiskGovernorSourceLabel(value) {
    const key = String(value || "").trim().toLowerCase();
    if (!key || key === "-") return "";
    return UI_SHARED.riskGovernorSourceLabels?.[key] || key.replaceAll("_", " ");
  }

  function formatValidationGateReason(value) {
    const raw = String(value || "").trim();
    if (!raw) return "-";
    if (UI_SHARED.validationGateReasonLabels?.[raw]) return UI_SHARED.validationGateReasonLabels[raw];
    if (raw.startsWith("live_authority_risk_governor:")) {
      const action = raw.split(":", 2)[1] || "";
      const label = formatRiskGovernorLabel(action);
      return label === "-" ? "리스크 governor가 신규 진입 중단" : `리스크 governor: ${label}`;
    }
    const incomplete = raw.match(/discipline_count=(\d+),\s*expected=(\d+)/);
    if (incomplete) {
      return `검증 항목 수 부족: ${incomplete[1]}/${incomplete[2]}`;
    }
    const failCount = raw.match(/fail_count=(\d+)/);
    if (raw.includes("blocked_by_validation") && failCount) {
      return `실패 항목 ${failCount[1]}개`;
    }
    return raw.replaceAll("_", " ");
  }

  function tradingValidationTone(status) {
    const normalized = String(status || "").trim().toLowerCase();
    if (["pass", "ok", "scale_ready", "normal", "clear"].includes(normalized)) return "good";
    if (["warn", "warning", "probe", "research_only", "stale"].includes(normalized)) return "warn";
    if (["fail", "error", "blocked_by_validation", "blocked", "validation_stale"].includes(normalized)) return "bad";
    return "muted";
  }

  window.HERMES_UI_OPS = Object.freeze({
    costEvidenceTone,
    formatCostEvidenceLabel,
    formatOpsSignalLabel,
    formatOpsSignalList,
    formatOpsRestartProcessSummary,
    formatRiskGovernorLabel,
    formatRiskGovernorSourceLabel,
    formatValidationEvidenceLabel,
    formatValidationGateLabel,
    formatValidationGateReason,
    renderOpsRemediationActions,
    renderOpsAdvisoryDetails,
    renderTradingValidationBottleneckSummary,
    renderTradingValidationCostAttribution,
    tradingValidationTone,
    validationEvidenceTone,
  });
})();
