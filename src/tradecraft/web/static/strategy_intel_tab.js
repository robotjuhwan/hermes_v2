(() => {
  function htmlEscape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function escaper(options) {
    return typeof options?.escapeHTML === "function" ? options.escapeHTML : htmlEscape;
  }

  function toneFor(value, options) {
    return typeof options?.sourceTone === "function" ? options.sourceTone(value) : String(value || "neutral");
  }

  function valuationLabel(value, options) {
    return typeof options?.strategyValuationLabel === "function"
      ? options.strategyValuationLabel(value)
      : String(value || "");
  }

  function strategySuitability(row) {
    return row?.suitability && typeof row.suitability === "object" ? row.suitability : {};
  }

  function strategyHorizonLabel(key) {
    if (key === "short_term") return "단기";
    if (key === "mid_term") return "중기";
    if (key === "long_term") return "장기";
    return "균형";
  }

  function strategyHorizonPayload(row, key) {
    const suitability = strategySuitability(row);
    const payload = suitability[key] && typeof suitability[key] === "object" ? suitability[key] : {};
    const score = Math.max(0, Math.min(100, Number(payload.score ?? row?.score ?? 0)));
    return {
      score,
      grade: String(payload.grade || "-"),
      drivers: Array.isArray(payload.drivers) ? payload.drivers : [],
      risks: Array.isArray(payload.risks) ? payload.risks : [],
    };
  }

  function renderSuitabilityBars(row, options = {}) {
    const escapeHTML = escaper(options);
    const rows = ["short_term", "mid_term", "long_term"].map((key) => {
      const payload = strategyHorizonPayload(row, key);
      return `
        <div class="strategy-horizon-row">
          <span>${escapeHTML(strategyHorizonLabel(key))}</span>
          <b>${escapeHTML(`${payload.grade} / ${Math.round(payload.score)}`)}</b>
          <div class="strategy-horizon-track"><i style="width:${escapeHTML(String(payload.score))}%"></i></div>
        </div>
      `;
    });
    return `<div class="strategy-horizon-grid">${rows.join("")}</div>`;
  }

  function renderSuitabilityDetail(row, options = {}) {
    const escapeHTML = escaper(options);
    const coverage = row?.data_coverage && typeof row.data_coverage === "object" ? row.data_coverage : {};
    const rows = ["short_term", "mid_term", "long_term"].map((key) => {
      const payload = strategyHorizonPayload(row, key);
      const drivers = payload.drivers.slice(0, 3);
      const risks = payload.risks.slice(0, 2);
      return `
        <div class="strategy-horizon-detail">
          <div class="helper-row-head">
            <strong>${escapeHTML(strategyHorizonLabel(key))}</strong>
            <span class="helper-row-status muted">${escapeHTML(`${payload.grade} / ${Math.round(payload.score)}`)}</span>
          </div>
          <ul>
            ${(drivers.length ? drivers : ["근거 보강 필요"]).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}
          </ul>
          <small>${escapeHTML((risks.length ? risks : ["리스크 추가 점검"]).join(" · "))}</small>
        </div>
      `;
    });
    const missing = Array.isArray(coverage.missing) ? coverage.missing : [];
    return `
      <div class="strategy-detail-section strategy-suitability-detail">
        <strong>기간별 적합도</strong>
        <div class="strategy-horizon-detail-grid">${rows.join("")}</div>
        <p class="strategy-coverage-note">
          ${escapeHTML(`자료 커버리지 ${coverage.coverage_score ?? "-"} · 소스 ${coverage.source_count ?? "-"}개${missing.length ? ` · 미수집 ${missing.join(", ")}` : ""}`)}
        </p>
      </div>
    `;
  }

  function dataWarnings(row) {
    const warnings = Array.isArray(row?.data_warnings)
      ? row.data_warnings.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    if (!warnings.length) {
      const coverage = row?.data_coverage && typeof row.data_coverage === "object" ? row.data_coverage : {};
      const missing = Array.isArray(coverage.missing) ? coverage.missing : [];
      return missing.slice(0, 4).map((item) => `미수집 ${item}`);
    }
    return [...new Set(warnings)].slice(0, 6);
  }

  function renderDataWarnings(row, options = {}) {
    const escapeHTML = escaper(options);
    const warnings = dataWarnings(row);
    const identity = row?.identity_status && typeof row.identity_status === "object" ? row.identity_status : {};
    const identityOk = String(identity.status || "") === "ok";
    const chips = [
      ...(identity.label ? [{ label: identity.label, tone: identityOk ? "good" : "warn" }] : []),
      ...warnings.map((label) => ({
        label,
        tone: /검증|미수집|없음|1개/.test(label) ? "warn" : "neutral",
      })),
    ];
    if (!chips.length) {
      return "";
    }
    return `
      <div class="strategy-data-warning-strip">
        ${chips
          .slice(0, 7)
          .map((item) => `<span class="strategy-data-chip ${escapeHTML(item.tone)}">${escapeHTML(item.label)}</span>`)
          .join("")}
      </div>
    `;
  }

  function renderDataHealth(result, options = {}) {
    const escapeHTML = escaper(options);
    const candidates = Array.isArray(result?.candidates) ? result.candidates : [];
    const sources = Array.isArray(result?.sources) ? result.sources : [];
    const warningCount = candidates.reduce((sum, row) => sum + (dataWarnings(row).length ? 1 : 0), 0);
    const valuationMissing = candidates.filter((row) => {
      const valuation = row?.valuation && typeof row.valuation === "object" ? row.valuation : {};
      return String(valuation.status || "").toLowerCase() !== "ok";
    }).length;
    const identitySuspect = candidates.filter((row) => {
      const identity = row?.identity_status && typeof row.identity_status === "object" ? row.identity_status : {};
      return identity.status && identity.status !== "ok";
    }).length;
    const activeSources = sources.filter((row) => ["ok", "active", "updated"].includes(String(row.status || "").toLowerCase())).length;
    return `
      <div class="strategy-data-health">
        <span>점수 ${escapeHTML(result?.score_method_version || "v2")}</span>
        <span>활성 소스 ${escapeHTML(String(activeSources))}/${escapeHTML(String(sources.length))}</span>
        <span>자료주의 ${escapeHTML(String(warningCount))}</span>
        <span>밸류 미수집 ${escapeHTML(String(valuationMissing))}</span>
        <span>종목명 검증 ${escapeHTML(String(identitySuspect))}</span>
      </div>
    `;
  }

  function renderCollectResult(result, errorMessage, options = {}) {
    const escapeHTML = escaper(options);
    if (errorMessage) {
      return `<div class="strategy-collect-panel bad">시그널 수집 실패: ${escapeHTML(errorMessage)}</div>`;
    }
    if (!result) {
      return "";
    }
    const sources = Array.isArray(result.sources) ? result.sources : [];
    const errors = Array.isArray(result.errors) ? result.errors : [];
    const status = String(result.status || "-");
    const sourceRows = sources
      .map((row) => {
        const warnings = Array.isArray(row.warnings) ? row.warnings : [];
        const cache = row.cache ? ` · cache ${row.cache}` : "";
        const unresolved = row.unresolved ? ` · unresolved ${row.unresolved}` : "";
        return `
          <li>
            <span>${escapeHTML(row.label || row.source_id || "source")}</span>
            <strong class="helper-runtime-chip ${escapeHTML(toneFor(row.status, options))}">
              ${escapeHTML(`${row.status || "-"} · loaded ${row.loaded || 0} · inserted ${row.inserted || 0} · skipped ${row.skipped || 0}${cache}${unresolved}`)}
            </strong>
            ${warnings.length ? `<small>${escapeHTML(warnings.join(" / "))}</small>` : ""}
          </li>
        `;
      })
      .join("");
    const errorRows = errors
      .slice(0, 5)
      .map((row) => `<li>${escapeHTML(`${row.source_id || "source"}: ${row.detail || row.message || row}`)}</li>`)
      .join("");
    return `
      <div class="strategy-collect-panel ${errors.length ? "warn" : ""}">
        <div class="helper-row-head">
          <strong>시그널 수집 ${escapeHTML(status)}</strong>
          <span class="helper-row-status ${escapeHTML(toneFor(status, options))}">inserted ${escapeHTML(String(result.inserted || 0))}</span>
        </div>
        ${sourceRows ? `<ul class="helper-runtime-list strategy-collect-list">${sourceRows}</ul>` : ""}
        ${errorRows ? `<ul class="helper-plain-list strategy-collect-errors">${errorRows}</ul>` : ""}
      </div>
    `;
  }

  function renderFundamentalsCollectResult(result, errorMessage, options = {}) {
    const escapeHTML = escaper(options);
    if (errorMessage) {
      return `<div class="strategy-collect-panel bad">밸류 갱신 실패: ${escapeHTML(errorMessage)}</div>`;
    }
    if (!result) {
      return "";
    }
    const errors = Array.isArray(result.errors) ? result.errors : [];
    const items = Array.isArray(result.items) ? result.items.slice(0, 8) : [];
    const status = String(result.status || "-");
    const itemRows = items
      .map((row) => {
        const latest = row.latest && typeof row.latest === "object" ? row.latest : {};
        const label = latest.score?.label ? ` · ${valuationLabel(latest.score.label, options)}` : "";
        return `
          <li>
            <span>${escapeHTML(row.symbol || "-")}</span>
            <strong class="helper-runtime-chip ${escapeHTML(toneFor(row.status, options))}">
              ${escapeHTML(`${row.status || "-"}${label}`)}
            </strong>
          </li>
        `;
      })
      .join("");
    const errorRows = errors
      .slice(0, 5)
      .map((row) => `<li>${escapeHTML(`${row.symbol || "symbol"}: ${row.error || row.message || row}`)}</li>`)
      .join("");
    return `
      <div class="strategy-collect-panel ${errors.length ? "warn" : ""}">
        <div class="helper-row-head">
          <strong>밸류 갱신 ${escapeHTML(status)}</strong>
          <span class="helper-row-status ${escapeHTML(toneFor(status, options))}">
            ${escapeHTML(`collected ${result.collected || 0} · skipped ${result.skipped || 0}`)}
          </span>
        </div>
        ${itemRows ? `<ul class="helper-runtime-list strategy-collect-list">${itemRows}</ul>` : ""}
        ${errorRows ? `<ul class="helper-plain-list strategy-collect-errors">${errorRows}</ul>` : ""}
      </div>
    `;
  }

  function renderSources(sources, options = {}) {
    const escapeHTML = escaper(options);
    const rows = Array.isArray(sources) ? sources : [];
    if (!rows.length) {
      return '<div class="notice">전략 소스 상태가 없습니다.</div>';
    }
    return `
      <div class="strategy-intel-source-grid">
        ${rows
          .map((row) => {
            const tone = toneFor(row.status, options);
            const count = row.count === undefined || row.count === null ? "-" : String(row.count);
            return `
              <article class="strategy-intel-source">
                <div class="helper-row-head">
                  <h4>${escapeHTML(row.label || row.source_id || "source")}</h4>
                  <span class="helper-row-status ${escapeHTML(tone)}">${escapeHTML(row.status || "unknown")}</span>
                </div>
                <p class="helper-row-summary">${escapeHTML(row.role || row.caution || "대기 중")}</p>
                <div class="helper-row-meta">
                  <span>${escapeHTML(`signals ${count}`)}</span>
                  <span>${escapeHTML(row.source_id || "")}</span>
                </div>
              </article>
            `;
          })
          .join("")}
      </div>
    `;
  }

  function renderScoreComponents(row, options = {}) {
    const escapeHTML = escaper(options);
    const components = row.score_components || {};
    const parts = [
      ["report", "리포트", components.report],
      ["research", "리서치", components.research],
      ["whale", "고래", components.whale],
      ["after_close", "종가수급", components.after_close],
      ["valuation", "밸류", components.valuation],
      ["recency", "최신성", components.recency],
      ["evidence", "근거", components.evidence],
      ["fit", "적합도", components.fit],
      ["risk", "리스크", components.risk_penalty],
    ];
    return `
      <div class="strategy-score-grid">
        ${parts
          .map(([key, label, value]) => {
            const score = Math.max(0, Math.min(100, Number(value || 0)));
            return `
              <div class="strategy-score-component ${key === "risk" ? "risk" : ""}">
                <span>${escapeHTML(label)}</span>
                <div class="strategy-score-track"><i style="width:${escapeHTML(String(score))}%"></i></div>
                <b>${escapeHTML(String(Math.round(score)))}</b>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
  }

  window.HERMES_STRATEGY_INTEL_TAB = {
    strategySuitability,
    strategyHorizonLabel,
    strategyHorizonPayload,
    renderSuitabilityBars,
    renderSuitabilityDetail,
    dataWarnings,
    renderDataWarnings,
    renderDataHealth,
    renderCollectResult,
    renderFundamentalsCollectResult,
    renderSources,
    renderScoreComponents,
  };
})();
