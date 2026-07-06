(() => {
  const UI_FORMATTERS = window.HERMES_UI_FORMATTERS || {};

  function fallbackEscape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  const escapeHTML = typeof UI_FORMATTERS.escapeHTML === "function"
    ? UI_FORMATTERS.escapeHTML
    : fallbackEscape;
  const fmtBytes = typeof UI_FORMATTERS.fmtBytes === "function"
    ? UI_FORMATTERS.fmtBytes
    : (value) => `${Number(value || 0).toFixed(0)} B`;
  const fmtKST = typeof UI_FORMATTERS.fmtKST === "function"
    ? UI_FORMATTERS.fmtKST
    : (value) => String(value || "");
  const fmtPercent = typeof UI_FORMATTERS.fmtPercent === "function"
    ? UI_FORMATTERS.fmtPercent
    : (value) => `${Number(value || 0).toFixed(1)}%`;

  function metricTone(value, warnAt, badAt) {
    const numeric = Number(value || 0);
    if (numeric >= badAt) return "bad";
    if (numeric >= warnAt) return "warn";
    return "good";
  }

  function renderSystemMetricsWidget({
    payload = {},
    error = "",
    collapsed = true,
    hasAdminToken = false,
  } = {}) {
    const system = payload.system || {};
    const memory = system.memory || {};
    const network = payload.network || {};
    const hermes = payload.hermes || {};
    const processes = Array.isArray(hermes.processes) ? hermes.processes : [];
    const cpu = Number(system.cpu_percent || 0);
    const memoryPct = Number(memory.percent || 0);
    const netPerSec = Number(network.recv_per_sec || 0) + Number(network.sent_per_sec || 0);
    const tone = error
      ? "bad"
      : payload.status === "unavailable"
        ? "warn"
        : metricTone(Math.max(cpu, memoryPct), 70, 90);
    const className = `system-metrics-widget ${collapsed ? "collapsed" : ""} ${tone}`;

    if (!hasAdminToken) {
      return {
        className,
        html: `
          <button class="system-metrics-summary" type="button" data-system-metrics-action="toggle">
            <span>SYS</span>
            <strong>토큰 필요</strong>
            <small>운영 지표 대기</small>
          </button>
        `,
      };
    }

    const summary = error
      ? escapeHTML(error)
      : `CPU ${fmtPercent(cpu)} · RAM ${fmtPercent(memoryPct)} · NET ${fmtBytes(netPerSec)}/s`;
    const updated = payload.generated_at ? `KST ${fmtKST(payload.generated_at)}` : "대기 중";
    const processRows = processes.slice(0, 8).map((row) => `
      <div class="system-metrics-process">
        <span>${escapeHTML(row.component || row.name || "-")}</span>
        <b>${escapeHTML(fmtBytes(row.memory_rss_bytes))}</b>
        <small>${escapeHTML(fmtPercent(row.cpu_percent))}</small>
      </div>
    `).join("");

    return {
      className,
      html: `
        <button class="system-metrics-summary" type="button" data-system-metrics-action="toggle">
          <span>SYS</span>
          <strong>${summary}</strong>
          <small>${escapeHTML(updated)}</small>
        </button>
        ${collapsed ? "" : `
          <div class="system-metrics-detail">
            <div class="system-metrics-grid">
              <span><b>${escapeHTML(fmtPercent(cpu))}</b>CPU</span>
              <span><b>${escapeHTML(fmtPercent(memoryPct))}</b>RAM</span>
              <span><b>${escapeHTML(fmtBytes(hermes.memory_rss_bytes))}</b>HERMES RAM</span>
              <span><b>${escapeHTML(fmtBytes(netPerSec))}/s</b>NET</span>
            </div>
            <div class="system-metrics-process-list">
              ${processRows || `<div class="system-metrics-empty">HERMES 프로세스 감지 대기</div>`}
            </div>
            <div class="system-metrics-actions">
              <span>${escapeHTML(payload.cache?.hit ? "캐시 응답" : `샘플 TTL ${payload.sample_ttl_sec || 10}s`)}</span>
              <button class="btn tiny ghost" type="button" data-system-metrics-action="refresh">갱신</button>
            </div>
          </div>
        `}
      `,
    };
  }

  window.HERMES_SYSTEM_METRICS_WIDGET = Object.freeze({
    metricTone,
    renderSystemMetricsWidget,
  });
})();
