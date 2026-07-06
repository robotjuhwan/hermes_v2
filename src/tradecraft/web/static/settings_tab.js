(function () {
  const riskLabels = Object.freeze({
    danger: "위험",
    warn: "주의",
    normal: "일반",
  });
  const riskClasses = Object.freeze({
    danger: "bad",
    warn: "warn",
    normal: "ok",
  });
  const DEFAULT_ALL_RENDER_LIMIT = 80;
  const FILTERED_ALL_RENDER_LIMIT = 160;

  function filteredItems(catalog, filterValue = "", categoryValue = "all") {
    const items = Array.isArray(catalog?.items) ? catalog.items : [];
    const filter = String(filterValue || "").trim().toLowerCase();
    const category = String(categoryValue || "all");
    return items.filter((item) => {
      if (category !== "all" && item.category !== category) return false;
      if (!filter) return true;
      const haystack = [
        item.key,
        item.label,
        item.description,
        item.env,
        item.category_label,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(filter);
    });
  }

  function renderInput(item, value, escapeValue = String) {
    const escape = typeof escapeValue === "function" ? escapeValue : String;
    if (!item?.editable) {
      const configured = item?.secret
        ? item?.configured
          ? "설정됨"
          : "미설정"
        : item?.locked_reason || "잠김";
      return `<span class="settings-locked">${escape(configured)}</span>`;
    }
    const common = `data-setting-input="${escape(item.key)}"`;
    if (item.input_type === "toggle") {
      const checked = value === true || String(value).toLowerCase() === "true" ? "checked" : "";
      return `
        <label class="settings-switch">
          <input type="checkbox" ${common} ${checked} />
          <span></span>
        </label>
      `;
    }
    if (item.input_type === "select") {
      const options = (item.choices || [])
        .map((choice) => {
          const selected = String(choice) === String(value) ? "selected" : "";
          return `<option value="${escape(choice)}" ${selected}>${escape(choice)}</option>`;
        })
        .join("");
      return `<select class="settings-input" ${common}>${options}</select>`;
    }
    if (item.input_type === "number") {
      const min = item.min !== null && item.min !== undefined ? `min="${escape(item.min)}"` : "";
      const max = item.max !== null && item.max !== undefined ? `max="${escape(item.max)}"` : "";
      const step = item.step !== null && item.step !== undefined ? `step="${escape(item.step)}"` : "";
      return `<input class="settings-input mono" type="number" value="${escape(value)}" ${min} ${max} ${step} ${common} />`;
    }
    if (item.input_type === "textarea") {
      return `<textarea class="settings-input settings-textarea mono" rows="3" ${common}>${escape(value)}</textarea>`;
    }
    return `<input class="settings-input mono" type="text" value="${escape(value)}" ${common} />`;
  }

  function draftValue(page, item) {
    if (!item) return "";
    if (Object.prototype.hasOwnProperty.call(page?.draft || {}, item.key)) {
      return page.draft[item.key];
    }
    return item.value ?? "";
  }

  function riskLabel(risk) {
    return riskLabels[risk] || riskLabels.normal || "일반";
  }

  function riskClass(risk) {
    return riskClasses[risk] || riskClasses.normal || "ok";
  }

  function renderSettingInput(page, item, escapeValue) {
    return renderInput(item, draftValue(page, item), escapeValue);
  }

  function renderJueWorkflowStatus(page, escapeValue = String) {
    const escape = typeof escapeValue === "function" ? escapeValue : String;
    if (page?.jueWorkflowLoading && !page?.jueWorkflowStatus) {
      return '<section class="settings-workflows"><div class="notice">쥬 워크플로우를 확인하는 중입니다.</div></section>';
    }
    if (page?.jueWorkflowError && !page?.jueWorkflowStatus) {
      return `<section class="settings-workflows"><div class="notice">쥬 워크플로우 조회 실패: ${escape(page.jueWorkflowError)}</div></section>`;
    }
    const payload = page?.jueWorkflowStatus;
    if (!payload) {
      return "";
    }
    const workflows = Object.values(payload.workflows || {});
    const rowsHtml = workflows
      .map((workflow) => {
        const skills = Array.isArray(workflow.skills) ? workflow.skills : [];
        const contracts = Array.isArray(workflow.contracts) ? workflow.contracts : [];
        const gates = Array.isArray(workflow.safety_gates) ? workflow.safety_gates : [];
        const model = workflow.model_policy?.expected_runtime_model
          || workflow.model_policy?.default_model
          || "모델 미지정";
        const reasoning = workflow.model_policy?.expected_reasoning_effort
          || workflow.model_policy?.default_reasoning_effort
          || "";
        const statusClass = workflow.status === "error" ? "bad" : "ok";
        return `
          <article class="jue-workflow-card">
            <div>
              <div class="settings-row-head">
                <h4>${escape(workflow.workflow_id || "workflow")}</h4>
                <span class="settings-chip ${statusClass}">${escape(workflow.status || "ok")}</span>
              </div>
              <p>${escape(workflow.scope || "scope")} · ${escape(model)} ${reasoning ? `· ${escape(reasoning)}` : ""}</p>
            </div>
            <div class="settings-chip-row">
              <span class="settings-chip muted">skills ${escape(skills.length)}</span>
              <span class="settings-chip muted">contracts ${escape(contracts.length)}</span>
              <span class="settings-chip muted">gates ${escape(gates.length)}</span>
            </div>
          </article>
        `;
      })
      .join("");
    const errorChip = payload.error_count
      ? `<span class="settings-chip bad">오류 ${escape(payload.error_count)}</span>`
      : '<span class="settings-chip ok">정상</span>';
    return `
      <section class="settings-workflows" data-jue-workflow-panel>
        <div class="settings-workflows-head">
          <div>
            <span class="section-kicker">Jue Workflow Packs</span>
            <h4>쥬 판단 스킬/계약 로드 상태</h4>
            <p>KIS, Binance, 크립토 리서치, 메모리 반성 루프에 주입되는 작업 규약입니다.</p>
          </div>
          <div class="settings-chip-row">
            ${errorChip}
            <span class="settings-chip muted">총 ${escape(payload.workflow_count || workflows.length)}개</span>
            <button class="btn ghost" type="button" data-settings-action="refresh-jue-workflows" ${page?.jueWorkflowLoading ? "disabled" : ""}>다시 확인</button>
          </div>
        </div>
        <div class="jue-workflow-grid">
          ${rowsHtml || '<div class="notice">로드된 워크플로우가 없습니다.</div>'}
        </div>
      </section>
    `;
  }

  function renderCodexNativeStatus(page, escapeValue = String) {
    const escape = typeof escapeValue === "function" ? escapeValue : String;
    if (page?.codexNativeLoading && !page?.codexNativeStatus) {
      return '<section class="settings-workflows" data-codex-native-panel><div class="notice">Codex Native 상태를 확인하는 중입니다.</div></section>';
    }
    if (page?.codexNativeError && !page?.codexNativeStatus) {
      return `<section class="settings-workflows" data-codex-native-panel><div class="notice">Codex Native 조회 실패: ${escape(page.codexNativeError)}</div></section>`;
    }
    const payload = page?.codexNativeStatus;
    if (!payload) return "";
    const account = payload.account || payload.latest_account_check || {};
    const accountStatus = account?.status || "pending";
    const models = Array.isArray(payload.models) ? payload.models : [];
    const components = Array.isArray(payload.components) ? payload.components : [];
    const turns = Array.isArray(payload.recent_turns) ? payload.recent_turns : [];
    const lastError = payload.last_error || null;
    const modelChips = models.length
      ? models.map((row) => {
          const model = row.model || row;
          const ok = row.available === undefined ? true : Boolean(row.available);
          return `<span class="settings-chip ${ok ? "ok" : "warn"}">${escape(model)}</span>`;
        }).join("")
      : '<span class="settings-chip muted">model check pending</span>';
    const componentRows = components.slice(0, 14).map((row) => `
      <article class="jue-workflow-card codex-native-turn">
        <div>
          <div class="settings-row-head">
            <h4>${escape(row.component || "component")}</h4>
            <span class="settings-chip muted">${escape(row.mode || payload.mode || "sdk")}</span>
          </div>
          <p>${escape(row.model || "-")} · ${escape(row.reasoning_effort || "-")} · ${escape(row.usage_component || "-")}</p>
        </div>
      </article>
    `).join("");
    const turnRows = turns.slice(0, 6).map((row) => {
      const statusClass = row.status === "ok" ? "ok" : "bad";
      return `
        <article class="jue-workflow-card codex-native-turn">
          <div>
            <div class="settings-row-head">
              <h4>${escape(row.component || "component")}</h4>
              <span class="settings-chip ${statusClass}">${escape(row.status || "-")}</span>
            </div>
            <p>${escape(row.workflow_id || row.operation || "workflow")} · ${escape(row.model || payload.model || "-")}</p>
          </div>
          <div class="settings-chip-row">
            <span class="settings-chip muted">tokens ${escape(row.usage?.total_tokens || 0)}</span>
            <span class="settings-chip muted mono">${escape(String(row.thread_id || "").slice(0, 12) || "-")}</span>
          </div>
        </article>
      `;
    }).join("");
    return `
      <section class="settings-workflows" data-codex-native-panel>
        <div class="settings-workflows-head">
          <div>
            <span class="section-kicker">Codex Native</span>
            <h4>계정/모델/thread 런타임 상태</h4>
            <p>쥬 판단이 사용하는 native SDK, persistent thread, 최근 turn 기록입니다.</p>
          </div>
          <div class="settings-chip-row">
            <span class="settings-chip ${accountStatus === "ok" ? "ok" : "warn"}">account ${escape(accountStatus)}</span>
            <span class="settings-chip muted">${escape(payload.mode || "sdk")} · ${escape(payload.thread_mode || "-")}</span>
            <button class="btn ghost" type="button" data-settings-action="refresh-codex-native" ${page?.codexNativeLoading ? "disabled" : ""}>다시 확인</button>
          </div>
        </div>
        <div class="settings-chip-row codex-native-models">${modelChips}</div>
        ${lastError ? `<div class="notice warn">최근 Native 오류: ${escape(lastError.component || "component")} · ${escape(lastError.error_message || lastError.status || "error")}</div>` : ""}
        <div class="jue-workflow-grid">
          ${componentRows || '<div class="notice">등록된 native component 매핑이 없습니다.</div>'}
        </div>
        <div class="jue-workflow-grid">
          ${turnRows || '<div class="notice">최근 native turn 기록이 없습니다.</div>'}
        </div>
      </section>
    `;
  }

  function renderPage(page, options = {}) {
    const escape = typeof options.escapeValue === "function" ? options.escapeValue : String;
    const formatValue = typeof options.formatValue === "function" ? options.formatValue : String;
    if (page?.loading && !page?.catalog) {
      return '<div class="notice">설정 catalog를 불러오는 중입니다.</div>';
    }
    if (page?.error && !page?.catalog) {
      return `<div class="notice">설정 조회 실패: ${escape(page.error)}</div>`;
    }
    const catalog = page?.catalog;
    if (!catalog) {
      return '<div class="notice">설정 catalog가 아직 없습니다.</div>';
    }

    const catalogItems = Array.isArray(catalog.items) ? catalog.items : [];
    const categories = Array.isArray(catalog.categories) ? catalog.categories : [];
    const filterValue = String(page?.filter || "").trim();
    const categoryValue = String(page?.category || "all");
    const items = filteredItems(catalog, filterValue, categoryValue);
    const renderLimit = categoryValue === "all"
      ? (filterValue ? FILTERED_ALL_RENDER_LIMIT : DEFAULT_ALL_RENDER_LIMIT)
      : Infinity;
    const visibleItems = Number.isFinite(renderLimit) ? items.slice(0, renderLimit) : items;
    const hiddenItemCount = Math.max(items.length - visibleItems.length, 0);
    const dirtyKeys = Object.keys(page?.draft || {});
    const dirtySet = new Set(dirtyKeys);
    const byKey = new Map(catalogItems.map((item) => [item.key, item]));
    const highRiskDirty = dirtyKeys.some((key) => byKey.get(key)?.risk === "danger");
    const pendingRestartCount = catalogItems.filter((item) => item.pending_restart).length;
    const editableCount = catalogItems.filter((item) => item.editable).length;
    const secretCount = catalogItems.filter((item) => item.secret).length;

    const categoryButtons = [
      { key: "all", label: "전체", count: catalogItems.length },
      ...categories,
    ]
      .map((category) => {
        const active = String(page?.category || "all") === category.key ? "active" : "";
        const count = category.count ?? catalogItems.length;
        return `
          <button class="settings-category ${active}" type="button" data-settings-category="${escape(category.key)}">
            <span>${escape(category.label)}</span>
            <strong>${escape(count)}</strong>
          </button>
        `;
      })
      .join("");

    const rowsHtml = visibleItems
      .map((item) => {
        const dirty = dirtySet.has(item.key);
        const pending = item.pending_restart ? '<span class="settings-chip warn">재시작 대기</span>' : "";
        const dirtyChip = dirty ? '<span class="settings-chip ok">변경됨</span>' : "";
        const secretChip = item.secret ? '<span class="settings-chip muted">secret</span>' : "";
        const riskChip = `<span class="settings-chip ${riskClass(item.risk)}">${escape(riskLabel(item.risk))}</span>`;
        const valueLabel = item.secret
          ? item.configured
            ? "값은 마스킹됨"
            : "아직 설정되지 않음"
          : `현재: ${formatValue(item.value)}`;
        return `
          <article class="settings-row ${dirty ? "dirty" : ""} ${item.risk === "danger" ? "danger" : ""}">
            <div class="settings-row-main">
              <div class="settings-row-head">
                <h4>${escape(item.label)}</h4>
                <div class="settings-chip-row">
                  ${riskChip}
                  ${secretChip}
                  ${pending}
                  ${dirtyChip}
                </div>
              </div>
              <p>${escape(item.description)}</p>
              <div class="settings-meta">
                <span class="mono">${escape(item.key)}</span>
                <span class="mono">${escape(item.env)}</span>
                <span>${escape(item.category_label)}</span>
                <span>${escape(valueLabel)}</span>
              </div>
            </div>
            <div class="settings-control">
              ${renderSettingInput(page, item, escape)}
            </div>
          </article>
        `;
      })
      .join("");
    const overflowNotice = hiddenItemCount > 0
      ? `
        <div class="notice settings-overflow-notice">
          전체 설정 ${escape(items.length)}개 중 ${escape(visibleItems.length)}개만 먼저 표시합니다.
          검색어를 입력하거나 왼쪽 카테고리를 선택하면 나머지 설정도 바로 편집할 수 있습니다.
        </div>
      `
      : "";

    const saveStatus = page?.saveResult
      ? `<div class="settings-save-result">${escape(page.saveResult.message || "저장 완료")}</div>`
      : "";
    const restartStatus = page?.restartResult
      ? `<div class="settings-save-result">${escape(page.restartResult.message || "재시작 예약 완료")}</div>`
      : "";
    const errorHtml = page?.error ? `<div class="notice">설정 처리 오류: ${escape(page.error)}</div>` : "";

    return `
      <div class="settings-shell">
        <div class="settings-hero">
          <div>
            <span class="section-kicker">Control Surface</span>
            <h3>운영 설정</h3>
            <p>쥬의 LLM, 블록 트레이딩, 장중 판단, 메모리, 리서치, ETF, KIS 호출 제한을 한 곳에서 관리합니다.</p>
          </div>
          <div class="settings-summary">
            <span>총 ${escape(catalogItems.length)}개</span>
            <span>수정 가능 ${escape(editableCount)}개</span>
            <span>secret ${escape(secretCount)}개</span>
            <span>재시작 대기 ${escape(pendingRestartCount)}개</span>
          </div>
        </div>

        <div class="settings-toolbar">
          <input id="settingsSearch" class="settings-search" type="search" value="${escape(page?.filter || "")}" placeholder="설정명, env, 설명 검색" />
          <div class="settings-actions">
            <button class="btn ghost" type="button" data-settings-action="refresh">새로고침</button>
            <button class="btn ghost" type="button" data-settings-action="restart" ${page?.restarting ? "disabled" : ""}>
              ${page?.restarting ? "재시작 예약 중..." : "control/runner 재시작"}
            </button>
            <button class="btn ghost" type="button" data-settings-action="reset" ${dirtyKeys.length ? "" : "disabled"}>되돌리기</button>
            <button class="btn warm" type="button" data-settings-action="save" ${dirtyKeys.length && !page?.saving ? "" : "disabled"}>
              ${page?.saving ? "저장 중..." : `저장 ${dirtyKeys.length ? `(${dirtyKeys.length})` : ""}`}
            </button>
          </div>
        </div>

        ${renderJueWorkflowStatus(page, escape)}
        ${renderCodexNativeStatus(page, escape)}

        <div class="settings-warning ${highRiskDirty ? "danger" : ""}">
          <strong>${highRiskDirty ? "실주문/보안 위험 설정 변경 포함" : "저장 방식"}</strong>
          <span>.env에 저장되고, 실행 중인 control/runner 재시작 후 반영됩니다. secret 값은 노출하지 않고 설정 여부만 보여줍니다.</span>
        </div>

        ${saveStatus}
        ${restartStatus}
        ${errorHtml}

        <div class="settings-layout">
          <aside class="settings-categories">${categoryButtons}</aside>
          <section class="settings-list">
            ${overflowNotice}
            ${rowsHtml || '<div class="notice">검색 조건에 맞는 설정이 없습니다.</div>'}
          </section>
        </div>
      </div>
    `;
  }

  window.HERMES_SETTINGS_TAB = Object.freeze({
    riskLabels: Object.freeze(riskLabels),
    riskClasses: Object.freeze(riskClasses),
    DEFAULT_ALL_RENDER_LIMIT,
    FILTERED_ALL_RENDER_LIMIT,
    filteredItems,
    renderInput,
    renderPage,
    renderJueWorkflowStatus,
    renderCodexNativeStatus,
  });
})();
