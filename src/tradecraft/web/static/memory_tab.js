(function () {
  function escapeDefault(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function truncateDefault(value, maxLength = 120) {
    const text = String(value ?? "");
    if (text.length <= maxLength) return text;
    return `${text.slice(0, Math.max(maxLength - 1, 0))}…`;
  }

  function displayOptions(options = {}) {
    return {
      escapeHTML: typeof options.escapeHTML === "function" ? options.escapeHTML : escapeDefault,
      truncateWithEllipsis: typeof options.truncateWithEllipsis === "function"
        ? options.truncateWithEllipsis
        : truncateDefault,
      registerHelperDetail: typeof options.registerHelperDetail === "function"
        ? options.registerHelperDetail
        : () => "",
    };
  }

  function renderPolicyStrip(memory, options = {}) {
    const { escapeHTML, truncateWithEllipsis } = displayOptions(options);
    const policies = Array.isArray(memory?.active_policies) ? memory.active_policies : [];
    if (!policies.length) {
      return '<div class="notice">아직 활성화된 메모리 운용 원칙이 없습니다. 장전/마감 루틴이 쌓이면 이 영역이 채워집니다.</div>';
    }
    return `
      <div class="memory-policy-strip">
        ${policies.slice(0, 6).map((row) => `
          <span class="strategy-data-chip">
            ${escapeHTML(row.policy_id || row.action || "policy")}
            <small>${escapeHTML(truncateWithEllipsis(row.reason || row.action || "", 58))}</small>
          </span>
        `).join("")}
      </div>
    `;
  }

  function renderJournalCard(row, options = {}) {
    const { escapeHTML, truncateWithEllipsis, registerHelperDetail } = displayOptions(options);
    const message = String(row?.message_md || "").trim();
    const detailId = message.length > 420
      ? registerHelperDetail({
        title: row?.title || "메모리 저널",
        body: message,
        meta: [`${row?.trading_day || ""} · ${row?.slot || ""}`],
      })
      : "";
    return `
      <article class="memory-journal-card">
        <div class="block-card-head">
          <div>
            <h4>${escapeHTML(row?.title || row?.slot_label || row?.slot || "메모리")}</h4>
            <p>${escapeHTML(row?.trading_day || "-")} · ${escapeHTML(row?.slot_label || row?.slot || "-")}</p>
          </div>
          <span class="block-status">${row?.sent_telegram ? "Telegram" : "저널"}</span>
        </div>
        <p class="helper-text">${escapeHTML(truncateWithEllipsis(message || "아직 내용이 없습니다.", 420))}</p>
        ${
          detailId
            ? `<button class="btn tiny ghost" type="button" data-helper-detail-id="${escapeHTML(detailId)}">전문 보기</button>`
            : ""
        }
      </article>
    `;
  }

  function renderDecisionSkills(memoryStatus, options = {}) {
    const { escapeHTML } = displayOptions(options);
    const skills = memoryStatus?.decision_skills || {};
    const skillStatus = memoryStatus?.decision_skill_status || {};
    const entries = Object.entries(skills);
    if (!entries.length) {
      return '<div class="memory-skill-strip muted">판단 스킬 상태 없음</div>';
    }
    const missing = Array.isArray(skillStatus.missing) ? skillStatus.missing : [];
    return `
      <div class="memory-skill-strip">
        ${entries.map(([key, value]) => {
          const preview = value?.preview || "";
          const isMissing = missing.includes(key) || !String(value?.version || "").trim();
          return `
            <article class="memory-skill-chip ${isMissing ? "missing" : ""}">
              <strong>${escapeHTML(key)}</strong>
              <span>${escapeHTML(value?.version || "missing")}</span>
              <p>${escapeHTML(preview || "스킬 내용 대기")}</p>
            </article>
          `;
        }).join("")}
      </div>
    `;
  }

  function renderJueSourceManifestPanel(state = {}, options = {}) {
    const { escapeHTML } = displayOptions(options);
    const manifest = state?.sourceManifest?.manifest || {};
    const mappings = Array.isArray(manifest.mappings) ? manifest.mappings : [];
    const error = String(state?.sourceManifestError || "");
    if (error && !mappings.length) {
      return `<section class="memory-section"><div class="notice">Source manifest 조회 실패: ${escapeHTML(error)}</div></section>`;
    }
    return `
      <section class="memory-section">
        <div class="panel-head compact">
          <div>
            <span class="section-kicker">Imported Operating Patterns</span>
            <h3>Financial Services 흡수 맵</h3>
          </div>
          <button class="btn tiny ghost" type="button" data-memory-action="refresh_jue_context">다시 확인</button>
        </div>
        <div class="memory-skill-strip">
          ${mappings.slice(0, 6).map((row) => `
            <article class="memory-skill-chip">
              <strong>${escapeHTML(row.local_skill_id || row.source_skill || "-")}</strong>
              <span>${escapeHTML(row.source_vertical || "source")} · ${escapeHTML(row.source_skill || "-")}</span>
              <p>${escapeHTML((Array.isArray(row.adopted_principles) ? row.adopted_principles : []).slice(0, 3).join(" · ") || "mapping")}</p>
            </article>
          `).join("") || '<div class="notice">아직 source manifest가 비어 있습니다.</div>'}
        </div>
      </section>
    `;
  }

  function renderJueLifecyclePanel(state = {}, options = {}) {
    const { escapeHTML, truncateWithEllipsis, registerHelperDetail } = displayOptions(options);
    const payload = state?.lifecycleLatest || {};
    const items = Array.isArray(payload.items) ? payload.items : [];
    const error = String(state?.lifecycleError || "");
    if (error && !items.length) {
      return `<section class="memory-section"><div class="notice">Lifecycle artifact 조회 실패: ${escapeHTML(error)}</div></section>`;
    }
    return `
      <section class="memory-section">
        <div class="panel-head compact">
          <div>
            <span class="section-kicker">Decision Lifecycle v3</span>
            <h3>최근 판단 아티팩트</h3>
          </div>
          <span class="strategy-data-chip">${escapeHTML(items.length)}개</span>
        </div>
        <div class="helper-grid">
          ${items.slice(0, 6).map((row) => {
            const payloadRow = row?.payload && typeof row.payload === "object" ? row.payload : {};
            const implications = Array.isArray(payloadRow.block_implications) ? payloadRow.block_implications : [];
            const evidence = Array.isArray(row?.evidence) ? row.evidence : [];
            const detail = [
              row?.summary_md || "",
              implications.length ? `\n\n블록 시사점:\n${implications.map((item) => `- ${item.action || item.intent || "implication"}: ${item.reason || item.summary || ""}`).join("\n")}` : "",
              evidence.length ? `\n\n근거 ${evidence.length}개` : "",
            ].join("");
            const detailId = detail.length > 260
              ? registerHelperDetail({
                title: row.title || row.artifact_id || "Lifecycle artifact",
                body: detail,
                meta: [row.symbol || "", row.workflow_id || "", row.updated_at || ""].filter(Boolean),
              })
              : "";
            return `
              <article class="helper-card">
                <h4>${escapeHTML(row.title || row.artifact_id || "Lifecycle artifact")}</h4>
                <p class="helper-text">${escapeHTML(truncateWithEllipsis(row.summary_md || "-", 220))}</p>
                <div class="chip-row">
                  <span class="strategy-data-chip">${escapeHTML(row.symbol || "공통")}</span>
                  <span class="strategy-data-chip">${escapeHTML(row.workflow_id || "-")}</span>
                  <span class="strategy-data-chip">근거 ${escapeHTML(evidence.length)}</span>
                </div>
                ${detailId ? `<button class="btn tiny ghost" type="button" data-helper-detail-id="${escapeHTML(detailId)}">전문 보기</button>` : ""}
              </article>
            `;
          }).join("") || '<div class="notice">아직 저장된 lifecycle artifact가 없습니다. 쥬가 deep-dive/모델업데이트를 만들면 여기에 쌓입니다.</div>'}
        </div>
      </section>
    `;
  }

  window.HERMES_MEMORY_TAB = Object.freeze({
    renderPolicyStrip,
    renderJournalCard,
    renderDecisionSkills,
    renderJueSourceManifestPanel,
    renderJueLifecyclePanel,
  });
})();
