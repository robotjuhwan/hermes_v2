(function () {
  const UI_FORMATTERS = window.HERMES_UI_FORMATTERS || {};
  const UI_OPS = window.HERMES_UI_OPS || {};
  const {
    asNumber,
    escapeHTML,
    fmtNum,
    fmtPercent,
    formatLiveMultiplier,
    truncateWithEllipsis,
  } = UI_FORMATTERS;
  const {
    costEvidenceTone,
    formatCostEvidenceLabel,
    formatRiskGovernorLabel,
    formatRiskGovernorSourceLabel,
    formatValidationEvidenceLabel,
    formatValidationGateLabel,
    formatValidationGateReason,
    tradingValidationTone,
    validationEvidenceTone,
  } = UI_OPS;

  function renderLiveAuthorityPanel(venue, authorityPayload = {}, options = {}) {
    const authority = authorityPayload && typeof authorityPayload === "object" ? authorityPayload : {};
    const mergeTradingValidationWithGateMatrix = options.mergeTradingValidationWithGateMatrix;
    const renderTradingValidationDetails = options.renderTradingValidationDetails;
    const repairExecutionTone = options.repairExecutionTone;
    const formatActiveRevisionEvidenceLabel = options.formatActiveRevisionEvidenceLabel;
    const activeRevisionEvidenceTone = options.activeRevisionEvidenceTone;
    const scorecards = Array.isArray(authority.scorecards) ? authority.scorecards : [];
    const grade = authority.live_grade || authority.status || "missing";
    const allowScale = Boolean(authority.allow_scale_up);
    const venueLabel = venue === "binance" ? "Binance" : "KIS";
    const validationGate = authority.validation_gate && typeof authority.validation_gate === "object"
      ? authority.validation_gate
      : {};
    const tradingValidation = authority.trading_validation && typeof authority.trading_validation === "object"
      ? authority.trading_validation
      : {};
    const mergedTradingValidation = mergeTradingValidationWithGateMatrix(tradingValidation, validationGate);
    const validationSummary = mergedTradingValidation.summary && typeof mergedTradingValidation.summary === "object"
      ? mergedTradingValidation.summary
      : {};
    const laneAuthority = authority.lane_authority && typeof authority.lane_authority === "object"
      ? authority.lane_authority
      : {};
    const probeLaneNames = Array.isArray(laneAuthority.probe_lane_names)
      ? laneAuthority.probe_lane_names.map((value) => String(value || "")).filter(Boolean)
      : [];
    const scaleBlockedLaneNames = Array.isArray(laneAuthority.scale_blocked_lanes)
      ? laneAuthority.scale_blocked_lanes.map((value) => String(value || "")).filter(Boolean)
      : [];
    const laneExecutionPosture = String(laneAuthority.execution_posture || "").trim();
    const laneExecutionLabel = {
      scale_allowed: "확대 가능",
      probe_allowed_scale_blocked: "탐색 가능 · 확대 보류",
      probe_allowed_sample_building: "탐색 표본 축적",
      review_required_no_scale: "검토 필요 · 확대 보류",
      normal_selective: "선별 운용",
    }[laneExecutionPosture] || laneExecutionPosture;
    const laneExecutionHtml = Object.keys(laneAuthority).length
      ? `
        <div>
          <span>Lane 실행자세</span>
          <strong>${escapeHTML(laneExecutionLabel || "선별 운용")}</strong>
          <small class="strategy-data-chip ${escapeHTML(probeLaneNames.length ? "good" : "warn")}">
            probe ${escapeHTML(fmtNum(laneAuthority.probe_lane_count ?? probeLaneNames.length, 0))}
            ${probeLaneNames.length ? ` · ${escapeHTML(probeLaneNames.slice(0, 4).join(", "))}` : ""}
            ${scaleBlockedLaneNames.length ? ` · scale 보류 ${escapeHTML(scaleBlockedLaneNames.slice(0, 3).join(", "))}` : ""}
          </small>
        </div>
      `
      : "";
    const validationMatrix = validationGate.discipline_matrix && typeof validationGate.discipline_matrix === "object"
      ? validationGate.discipline_matrix
      : {};
    const validationMatrixSummary = validationMatrix.summary && typeof validationMatrix.summary === "object"
      ? validationMatrix.summary
      : {};
    const validationCountsSummary = Object.keys(validationMatrixSummary).length
      ? validationMatrixSummary
      : validationSummary;
    const validationDetailHtml = renderTradingValidationDetails(mergedTradingValidation, {
      summaryTitle: "검증 랩 요약",
      weakTitle: "취약 테스트",
      capacityTitle: "용량 병목",
    });
    const gateStatus = validationGate.status || "clear";
    const readiness = validationGate.readiness || validationSummary.readiness || "-";
    const gateReason = validationGate.reason || "";
    const validationPassport = validationGate.validation_passport && typeof validationGate.validation_passport === "object"
      ? validationGate.validation_passport
      : {};
    const passportFailedIds = Array.isArray(validationPassport.failed_ids)
      ? validationPassport.failed_ids.map((value) => String(value)).filter(Boolean)
      : [];
    const passportWeakIds = Array.isArray(validationPassport.weak_ids)
      ? validationPassport.weak_ids.map((value) => String(value)).filter(Boolean)
      : [];
    const passportRequiresRevalidation = Boolean(validationPassport.requires_revalidation);
    const passportExpectedCount = asNumber(validationPassport.expected_count ?? 19, 19);
    const passportActualCount = asNumber(validationPassport.actual_count ?? 0, 0);
    const passportRowDetailCount = validationPassport.row_detail_count === undefined || validationPassport.row_detail_count === null
      ? null
      : asNumber(validationPassport.row_detail_count, 0);
    const passportRowDetailComplete = Boolean(validationPassport.row_detail_complete);
    const passportRowDetailLabel = passportRowDetailCount === null
      ? ""
      : `상세 row ${fmtNum(passportRowDetailCount, 0)}/${fmtNum(passportExpectedCount || 19, 0)}${passportRowDetailComplete ? "" : " 부분"}`;
    const passportScore = validationPassport.score === undefined || validationPassport.score === null
      ? null
      : asNumber(validationPassport.score, 0);
    const passportStatus = validationPassport.status || validationPassport.readiness || gateStatus;
    const passportTone = tradingValidationTone(passportStatus);
    const passportPrimaryNote = passportFailedIds.length
      ? `실패 ${passportFailedIds.slice(0, 3).join(", ")}`
      : (
        passportWeakIds.length
          ? `취약 ${passportWeakIds.slice(0, 3).join(", ")}`
          : "취약 항목 없음"
      );
    const validationPassportHtml = Object.keys(validationPassport).length
      ? `
        <div class="live-authority-passport ${escapeHTML(passportTone)}">
          <span>검증 여권</span>
          <strong>${escapeHTML(passportRequiresRevalidation ? "재검증" : formatValidationGateLabel(passportStatus || "clear"))}</strong>
          <p>
            ${escapeHTML(`${fmtNum(passportActualCount, 0)}/${fmtNum(passportExpectedCount || 19, 0)}`)}
            ${passportRowDetailLabel ? ` · ${escapeHTML(passportRowDetailLabel)}` : ""}
            ${passportScore === null ? "" : ` · ${escapeHTML(fmtNum(passportScore, 1))}점`}
            · ${escapeHTML(passportPrimaryNote)}
          </p>
        </div>
      `
      : "";
    const authorityActiveRevisionEvidence = authority.active_revision_evidence && typeof authority.active_revision_evidence === "object"
      ? authority.active_revision_evidence
      : {};
    const authorityActiveRevisionStatus = String(authorityActiveRevisionEvidence.status || "").trim();
    const authorityPendingLaneCounts = authorityActiveRevisionEvidence.pending_block_lane_counts
      && typeof authorityActiveRevisionEvidence.pending_block_lane_counts === "object"
      ? authorityActiveRevisionEvidence.pending_block_lane_counts
      : {};
    const authorityPendingLaneSummary = Object.entries(authorityPendingLaneCounts)
      .slice(0, 4)
      .map(([lane, count]) => `${lane} ${fmtNum(count, 0)}`)
      .join(" · ");
    const authorityActiveRevisionHtml = authorityActiveRevisionStatus
      ? `
        <div class="live-authority-active-revision ${escapeHTML(activeRevisionEvidenceTone(authorityActiveRevisionStatus))}">
          <span>Active revision</span>
          <strong>${escapeHTML(formatActiveRevisionEvidenceLabel(authorityActiveRevisionStatus))}</strong>
          <p>
            ${escapeHTML(authorityActiveRevisionEvidence.strategy_revision_id || "-")}
            · 표본 ${escapeHTML(fmtNum(authorityActiveRevisionEvidence.effective_sample_count ?? 0, 0))}
            / ${escapeHTML(fmtNum(authorityActiveRevisionEvidence.min_samples_to_scale ?? 0, 0))}
            · 대기 ${escapeHTML(fmtNum(authorityActiveRevisionEvidence.pending_block_count ?? 0, 0))}
            ${authorityPendingLaneSummary ? ` · ${escapeHTML(authorityPendingLaneSummary)}` : ""}
          </p>
        </div>
      `
      : "";
    const riskGovernorAction = validationGate.risk_governor_action || "-";
    const riskGovernorSource = validationGate.risk_governor_source || "";
    const riskGovernorActionLabel = formatRiskGovernorLabel(riskGovernorAction);
    const riskGovernorSourceLabel = formatRiskGovernorSourceLabel(riskGovernorSource);
    const appliedValidationMultiplier = validationGate.applied_max_budget_multiplier;
    const lossCooldown = validationGate.loss_cooldown && typeof validationGate.loss_cooldown === "object"
      ? validationGate.loss_cooldown
      : {};
    const cooldownSymbols = Array.isArray(lossCooldown.symbols) ? lossCooldown.symbols : [];
    const cooldownGroups = Array.isArray(lossCooldown.groups) ? lossCooldown.groups : [];
    const cooldownActionLabels = {
      do_not_scale_or_create_live_entry_without_new_evidence: "신규 확대 금지",
      deprioritize_until_revalidated: "재검증 전 우선순위 하향",
    };
    const cooldownRows = [
      ...cooldownSymbols.slice(0, 4).map((row) => ({
        title: row.symbol || "-",
        label: "symbol",
        action: row.action || "do_not_scale_or_create_live_entry_without_new_evidence",
        detail: [
          row.total_net_pnl !== undefined ? `net ${fmtNum(row.total_net_pnl, 2)}` : "",
          row.profit_factor !== undefined ? `PF ${fmtNum(row.profit_factor, 2)}` : "",
          row.expectancy_pct !== undefined ? `exp ${fmtNum(row.expectancy_pct, 2)}%` : "",
          row.risk_score !== undefined ? `risk ${fmtNum(row.risk_score, 1)}` : "",
        ].filter(Boolean).join(" · "),
      })),
      ...cooldownGroups.slice(0, 4).map((row) => ({
        title: row.group || "-",
        label: row.group_type || "group",
        action: row.action || "deprioritize_until_revalidated",
        detail: [
          row.total_net_pnl !== undefined ? `net ${fmtNum(row.total_net_pnl, 2)}` : "",
          row.profit_factor !== undefined ? `PF ${fmtNum(row.profit_factor, 2)}` : "",
          row.expectancy_pct !== undefined ? `exp ${fmtNum(row.expectancy_pct, 2)}%` : "",
          row.risk_score !== undefined ? `risk ${fmtNum(row.risk_score, 1)}` : "",
        ].filter(Boolean).join(" · "),
      })),
    ];
    const lossCooldownHtml = cooldownRows.length
      ? `
        <section class="trading-validation-remediation loss-cooldown-panel">
          <div class="trading-validation-remediation-head">
            <h5>손실 쿨다운</h5>
            <span>${escapeHTML(fmtNum(cooldownRows.length, 0))}개</span>
          </div>
          <p class="trading-validation-primary-action">
            ${escapeHTML(lossCooldown.instruction || "최근 손실 귀속이 큰 심볼/그룹은 새 근거가 생기기 전까지 확대하지 않습니다.")}
          </p>
          <div class="trading-validation-list">
            ${cooldownRows.map((row) => `
              <article class="trading-validation-row bad">
                <span>${escapeHTML(row.label)}</span>
                <strong>${escapeHTML(row.title)}</strong>
                <p>
                  ${escapeHTML(cooldownActionLabels[row.action] || row.action)}
                  ${row.detail ? ` · ${escapeHTML(row.detail)}` : ""}
                </p>
              </article>
            `).join("")}
          </div>
        </section>
      `
      : "";
    const repairExecution = authority.repair_execution && typeof authority.repair_execution === "object"
      ? authority.repair_execution
      : {};
    const repairActions = Array.isArray(repairExecution.actions)
      ? repairExecution.actions
      : [];
    const repairStatus = String(repairExecution.status || "").trim();
    const repairExecutionRows = repairActions.slice(0, 5).map((action) => {
      const status = String(action?.status || "queued").trim();
      const mode = action?.validation_mode || action?.discipline_id || "";
      const flags = [
        action?.scale_up_blocked ? "scale-up 차단" : "",
        action?.live_shadow_required ? "live shadow 필요" : "",
        action?.runner_status ? `runner ${action.runner_status}` : "",
      ].filter(Boolean).join(" · ");
      return `
        <li class="${escapeHTML(repairExecutionTone(status))}">
          <span>${escapeHTML(action?.priority || status)}</span>
          <strong>${escapeHTML(`${action?.discipline_id || "-"} · ${status}`)}</strong>
          <p>
            ${escapeHTML(truncateWithEllipsis(mode || action?.artifact || "-", 132))}
            ${action?.artifact ? ` · ${escapeHTML(truncateWithEllipsis(action.artifact, 80))}` : ""}
            ${flags ? ` · ${escapeHTML(flags)}` : ""}
          </p>
        </li>
      `;
    }).join("");
    const repairExecutionHtml = Object.keys(repairExecution).length
      ? `
        <section class="trading-validation-remediation repair-execution-panel">
          <div class="trading-validation-remediation-head">
            <h5>검증 복구 실행</h5>
            <span>${escapeHTML(repairStatus || "대기")}</span>
          </div>
          <p class="trading-validation-primary-action">
            실행 ${escapeHTML(fmtNum(repairExecution.executed_count ?? 0, 0))}
            · 대기 ${escapeHTML(fmtNum(repairExecution.queued_count ?? 0, 0))}
            ${repairExecution.error_count !== undefined ? ` · 오류 ${escapeHTML(fmtNum(repairExecution.error_count, 0))}` : ""}
            ${repairExecution.m1_execution_posture ? ` · ${escapeHTML(repairExecution.m1_execution_posture)}` : ""}
          </p>
          ${
            repairExecutionRows
              ? `
                <article class="trading-validation-remediation-card trading-validation-work-queue">
                  <div>
                    <span>repair_execution</span>
                    <strong>실제 복구 큐</strong>
                  </div>
                  <ul>${repairExecutionRows}</ul>
                </article>
              `
              : ""
          }
        </section>
      `
      : "";
    const validationCounts = [
      `P ${fmtNum(validationCountsSummary.pass_count ?? 0, 0)}`,
      `W ${fmtNum(validationCountsSummary.warn_count ?? 0, 0)}`,
      `F ${fmtNum((validationGate.fail_count ?? validationCountsSummary.fail_count) ?? 0, 0)}`,
      `M ${fmtNum(validationCountsSummary.missing_count ?? 0, 0)}`,
    ].join(" · ");
    const rows = scorecards.slice(0, 4).map((row) => {
      const evidenceStatus = row.validation_evidence_status || "";
      const evidenceTone = validationEvidenceTone(evidenceStatus);
      const missingDims = Array.isArray(row.validation_missing_dimensions)
        ? row.validation_missing_dimensions
        : [];
      const failedDims = Array.isArray(row.validation_failed_dimensions)
        ? row.validation_failed_dimensions
        : [];
      const evidenceDetail = failedDims.length
        ? `실패 ${failedDims.slice(0, 3).join(", ")}`
        : missingDims.length
          ? `부족 ${missingDims.slice(0, 3).join(", ")}`
          : "";
      return `
        <div>
          <span>${escapeHTML(`${row.strategy_family || "-"} · ${row.evidence_key || "all"}`)}</span>
          <strong>${escapeHTML(`${row.grade || "-"} · ${fmtNum(row.sample_count ?? 0, 0)}건 · ${fmtPercent(row.win_rate ?? 0, 1)}`)}</strong>
          ${
            evidenceStatus
              ? `<small class="strategy-data-chip ${escapeHTML(evidenceTone)}">검증 증거 · ${escapeHTML(formatValidationEvidenceLabel(evidenceStatus))}${evidenceDetail ? ` · ${escapeHTML(evidenceDetail)}` : ""}</small>`
              : ""
          }
        </div>
      `;
    }).join("");
    const performanceLanes = Array.isArray(authority.performance_lanes)
      ? authority.performance_lanes
      : [];
    const costEvidenceRows = performanceLanes
      .filter((row) => row && typeof row === "object" && (
        row.cost_evidence_status
        || row.cost_precision_counts
        || row.scale_blocked_by_cost_precision
        || row.scale_blocked_by_verified_edge_samples
        || row.cost_hybrid_alpha_count
        || row.cost_verified_alpha_count
      ))
      .slice(0, 4)
      .map((row) => {
        const counts = row.cost_precision_counts && typeof row.cost_precision_counts === "object"
          ? row.cost_precision_counts
          : {};
        const countSummary = [
          counts.recorded ? `실측 ${fmtNum(counts.recorded, 0)}` : "",
          counts.hybrid ? `혼합 ${fmtNum(counts.hybrid, 0)}` : "",
          counts.estimated ? `추정 ${fmtNum(counts.estimated, 0)}` : "",
          counts.partial ? `부분 ${fmtNum(counts.partial, 0)}` : "",
          counts.missing ? `누락 ${fmtNum(counts.missing, 0)}` : "",
        ].filter(Boolean).join(" · ");
        const costStatus = row.cost_evidence_status || "";
        const tone = costEvidenceTone(costStatus);
        const gateNote = row.scale_blocked_by_verified_edge_samples
          ? "검증 α 샘플 부족"
          : row.scale_blocked_by_cost_precision || row.scale_blocked_by_cost_evidence
            ? "스케일업 보류"
            : "증거 축적";
        return `
          <div>
            <span>${escapeHTML(`비용 증거 · ${row.lane || "-"}`)}</span>
            <strong>${escapeHTML(formatCostEvidenceLabel(costStatus))}</strong>
            <small class="strategy-data-chip ${escapeHTML(tone)}">
              ${escapeHTML(gateNote)}
              ${row.cost_precision_verified_rate !== undefined ? ` · 실측 ${escapeHTML(fmtNum(row.cost_precision_verified_rate, 1))}%` : ""}
              ${countSummary ? ` · ${escapeHTML(countSummary)}` : ""}
              ${row.cost_verified_alpha_count ? ` · 검증 α ${escapeHTML(fmtNum(row.cost_verified_alpha_count, 0))}` : ""}
              ${row.cost_unverified_alpha_count ? ` · 미검증 α ${escapeHTML(fmtNum(row.cost_unverified_alpha_count, 0))}` : ""}
              ${row.cost_hybrid_alpha_count ? ` · hybrid α ${escapeHTML(fmtNum(row.cost_hybrid_alpha_count, 0))}` : ""}
            </small>
          </div>
        `;
      }).join("");
    const error = options.liveAuthorityError
      ? `<p class="compact-warn">live authority 조회 실패: ${escapeHTML(options.liveAuthorityError)}</p>`
      : "";
    return `
      <section class="memory-section binance-edge-panel live-authority-panel">
        <div class="panel-head compact">
          <h3>Live Authority · ${escapeHTML(venueLabel)}</h3>
          <p>실제 블록 성과와 실행 오류를 압축해 쥬의 공격성, 빈도, 크기 판단에 넣습니다.</p>
        </div>
        <div class="binance-edge-grid">
          <div>
            <span>실전 등급</span>
            <strong>${escapeHTML(grade)}</strong>
          </div>
          <div>
            <span>최대 예산 배수</span>
            <strong>${escapeHTML(formatLiveMultiplier(authority.max_budget_multiplier))}</strong>
          </div>
          <div>
            <span>적용 배수</span>
            <strong>${escapeHTML(appliedValidationMultiplier === undefined ? "-" : `${fmtNum(appliedValidationMultiplier, 2)}x`)}</strong>
          </div>
          <div>
            <span>스케일업</span>
            <strong>${escapeHTML(allowScale ? "허용" : "보류")}</strong>
          </div>
          <div>
            <span>스코어카드</span>
            <strong>${escapeHTML(fmtNum(authority.scorecard_count ?? scorecards.length, 0))}</strong>
          </div>
          ${authorityActiveRevisionHtml}
          <div>
            <span>검증 게이트</span>
            <strong>${escapeHTML(formatValidationGateLabel(gateStatus))}</strong>
          </div>
          <div>
            <span>Readiness</span>
            <strong>${escapeHTML(readiness)}</strong>
          </div>
          <div>
            <span>19검증</span>
            <strong>${escapeHTML(validationCounts)}</strong>
          </div>
          ${laneExecutionHtml}
          ${validationPassportHtml}
          <div>
            <span>게이트 사유</span>
            <strong>${escapeHTML(formatValidationGateReason(gateReason))}</strong>
          </div>
          <div>
            <span>Risk Governor</span>
            <strong>${escapeHTML(riskGovernorSourceLabel ? `${riskGovernorActionLabel} · ${riskGovernorSourceLabel}` : riskGovernorActionLabel)}</strong>
          </div>
          ${rows}
          ${costEvidenceRows}
        </div>
        ${validationDetailHtml}
        ${lossCooldownHtml}
        ${repairExecutionHtml}
        ${error}
      </section>
    `;
  }

  window.HERMES_UI_LIVE_AUTHORITY = Object.freeze({
    renderLiveAuthorityPanel,
  });
})();
