(() => {
  function node(root, id) {
    const scope = root && typeof root.getElementById === "function" ? root : document;
    return scope.getElementById(id);
  }

  function escapeValue(value, options = {}) {
    const escapeFn = typeof options.escapeHTML === "function"
      ? options.escapeHTML
      : (next) => String(next ?? "");
    return escapeFn(String(value ?? ""));
  }

  function numberValue(value, fallback = 0, options = {}) {
    if (typeof options.asNumber === "function") {
      return options.asNumber(value, fallback);
    }
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function formatNumber(value, digits = 0, options = {}) {
    if (typeof options.fmtNum === "function") {
      return options.fmtNum(value, digits);
    }
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "-";
    return parsed.toLocaleString("ko-KR", { maximumFractionDigits: digits });
  }

  function formatKRW(value, options = {}) {
    if (typeof options.fmtKRW === "function") {
      return options.fmtKRW(value);
    }
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "-";
    return Math.round(parsed).toLocaleString("ko-KR");
  }

  function formatKST(value, withDate, options = {}) {
    if (typeof options.fmtKST === "function") {
      return options.fmtKST(value, withDate);
    }
    return value ? String(value) : "--";
  }

  function selectedSessionIds(root = document) {
    const checks = [...root.querySelectorAll(".bt-session-check:checked")];
    return checks.map((item) => String(item.value || "").trim()).filter(Boolean);
  }

  function renderCurve(curve, options = {}) {
    const line = node(options.root, "btCurveLine");
    if (!line) return;
    const rows = Array.isArray(curve) ? curve : [];
    if (rows.length < 2) {
      line.setAttribute("points", "");
      return;
    }

    const width = 1000;
    const height = 260;
    const values = rows.map((row) => numberValue(row.net_pnl_krw, 0, options));
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (min === max) {
      min -= 1;
      max += 1;
    }
    const span = max - min;
    const points = rows
      .map((row, idx) => {
        const x = (idx / (rows.length - 1)) * width;
        const y = height
          - ((numberValue(row.net_pnl_krw, 0, options) - min) / span)
            * (height - 12)
          - 6;
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
    line.setAttribute("points", points);

    const last = numberValue(values[values.length - 1], 0, options);
    line.style.stroke = last >= 0 ? "var(--status-ok)" : "var(--status-bad)";
  }

  function renderStatus(payload, options = {}) {
    const status = payload || {};
    const job = status.job || {};
    const progress = status.progress || {};
    const aggregate = progress.aggregate || {};
    const curve = progress.equity_curve || [];

    const statusEl = node(options.root, "btStatusText");
    if (statusEl) {
      statusEl.textContent = job.status || "idle";
    }

    const total = numberValue(progress.total_cycles, 0, options);
    const done = numberValue(progress.cycle, 0, options);
    const pct = numberValue(progress.progress_pct, 0, options);
    const progressText = node(options.root, "btProgressText");
    if (progressText) {
      progressText.textContent = `${done} / ${total} (${formatNumber(pct, 2, options)}%)`;
    }
    const bar = node(options.root, "btProgressBar");
    if (bar) {
      bar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    }

    const net = numberValue(aggregate.net_pnl_krw, 0, options);
    const realized = numberValue(aggregate.realized_pnl_krw, 0, options);
    const unrealized = numberValue(aggregate.unrealized_pnl_krw, 0, options);
    const fees = numberValue(aggregate.fees_krw, 0, options);

    const netEl = node(options.root, "btNetPnl");
    const realizedEl = node(options.root, "btRealized");
    const unrealizedEl = node(options.root, "btUnrealized");
    const feesEl = node(options.root, "btFees");
    if (netEl) {
      netEl.textContent = `${formatKRW(net, options)} KRW`;
      netEl.className = net >= 0 ? "gain" : "loss";
    }
    if (realizedEl) realizedEl.textContent = `${formatKRW(realized, options)} KRW`;
    if (unrealizedEl) unrealizedEl.textContent = `${formatKRW(unrealized, options)} KRW`;
    if (feesEl) feesEl.textContent = `${formatKRW(fees, options)} KRW`;

    renderCurve(curve, options);

    const rows = Array.isArray(progress.sessions) ? progress.sessions : [];
    const body = node(options.root, "btSessionBody");
    if (body) {
      body.innerHTML = rows
        .map((row) => {
          const netPnl = numberValue(row.net_pnl_krw, 0, options);
          return `
            <tr>
              <td>${escapeValue(row.session_id || "-", options)}</td>
              <td>${escapeValue(row.symbol || "-", options)}</td>
              <td>${escapeValue(row.signals ?? 0, options)}</td>
              <td>${escapeValue(row.fills ?? 0, options)}</td>
              <td>${escapeValue(row.trades ?? 0, options)}</td>
              <td class="${netPnl >= 0 ? "gain" : "loss"}">${escapeValue(formatKRW(netPnl, options), options)}</td>
            </tr>
          `;
        })
        .join("");
    }

    const scenario = job.scenario || "-";
    const source = job.session_source || "-";
    const updated = progress.updated_at ? formatKST(progress.updated_at, true, options) : "--";
    const meta = node(options.root, "btMeta");
    if (meta) {
      meta.textContent = `scenario=${scenario} | session_source=${source} | updated=${updated}`;
    }
    return status;
  }

  function renderScenarios(rows, options = {}) {
    const select = node(options.root, "btScenario");
    const items = Array.isArray(rows) ? rows : [];
    if (!select) return items;
    select.innerHTML = items
      .map((row) => {
        const key = String(row.key || "");
        const label = String(row.label || key || "-");
        const desc = String(row.description || "");
        return `<option value="${escapeValue(key, options)}">${escapeValue(`${label} - ${desc}`, options)}</option>`;
      })
      .join("");
    if (!items.length) {
      select.innerHTML = '<option value="baseline">baseline</option>';
    }
    return items;
  }

  function renderDataStatus(payload, options = {}) {
    const status = payload || {};
    const statusText = `data cache: ${status.symbol_count || 0} symbols`;
    const target = node(options.root, "btDataStatus");
    if (target) {
      target.textContent = statusText;
    }
    return status;
  }

  function buildStartPayload(readValue, options = {}) {
    const value = typeof readValue === "function" ? readValue : () => "";
    const root = options.root || document;
    return {
      scenario: value("btScenario") || "baseline",
      cycles: numberValue(value("btCycles"), 720, options),
      step_sec: numberValue(value("btStepSec"), 60, options),
      speed: numberValue(value("btSpeed"), 120, options),
      fee_rate: numberValue(value("btFeeRate"), 0.0005, options),
      slippage_bps: numberValue(value("btSlippage"), 1, options),
      session_ids: selectedSessionIds(root),
    };
  }

  window.HERMES_BACKTEST_TAB = Object.freeze({
    selectedSessionIds,
    renderCurve,
    renderStatus,
    renderScenarios,
    renderDataStatus,
    buildStartPayload,
  });
})();
