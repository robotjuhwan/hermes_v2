(() => {
  function htmlEscape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function helpers(options = {}) {
    return {
      escapeHTML: typeof options.escapeHTML === "function" ? options.escapeHTML : htmlEscape,
      fmtNum: typeof options.fmtNum === "function" ? options.fmtNum : (value, digits = 0) => Number(value || 0).toFixed(digits),
      fmtKRW: typeof options.fmtKRW === "function" ? options.fmtKRW : (value) => String(Math.round(Number(value || 0))),
      fmtKST: typeof options.fmtKST === "function" ? options.fmtKST : (value) => String(value || "--"),
      asNumber:
        typeof options.asNumber === "function"
          ? options.asNumber
          : (value, fallback = 0) => {
              const parsed = Number(value);
              return Number.isFinite(parsed) ? parsed : fallback;
            },
      helperStateChip:
        typeof options.helperStateChip === "function"
          ? options.helperStateChip
          : (value) => ({ cls: "neutral", text: String(value ?? "-") }),
    };
  }

  function buildCodeNameMap(targetRows, currentRows) {
    const codeNameMap = new Map();
    [...targetRows, ...currentRows].forEach((row) => {
      const ticker = String(row?.ticker || "").trim();
      const name = String(row?.name || "").trim();
      if (/^\d{6}$/.test(ticker) && name && name !== ticker) {
        codeNameMap.set(ticker, name);
      }
    });
    return codeNameMap;
  }

  function formatSymbolLabel(row, codeNameMap = new Map()) {
    const ticker = String(row?.ticker || "-").trim();
    const name = String(row?.name || "").trim();
    if (/^\d{6}$/.test(ticker)) {
      const resolvedName = name && name !== ticker ? name : codeNameMap.get(ticker) || "미상종목";
      return `${resolvedName} (${ticker})`;
    }
    if (ticker.toUpperCase() === "KRW") {
      return "현금 (KRW)";
    }
    if (name && name !== ticker) {
      return `${name} (${ticker})`;
    }
    return ticker;
  }

  function openPairLabel(pair, codeNameMap = new Map()) {
    const text = String(pair || "").trim();
    const ticker = text.split("/")[0].trim();
    if (/^\d{6}$/.test(ticker)) {
      return `${codeNameMap.get(ticker) || "미상종목"} (${ticker})`;
    }
    if (ticker.toUpperCase() === "KRW") {
      return "현금 (KRW)";
    }
    return text;
  }

  function renderRows(rows, options = {}) {
    const { escapeHTML, helperStateChip } = helpers(options);
    return rows
      .map((row) => {
        const chip = helperStateChip(row.value);
        return `
          <li>
            <span>${escapeHTML(row.label)}</span>
            <strong class="helper-runtime-chip ${chip.cls}">${escapeHTML(row.value)}</strong>
          </li>
        `;
      })
      .join("");
  }

  function renderWeightTable(rows, codeNameMap, emptyText, options = {}) {
    const { escapeHTML, fmtNum, asNumber } = helpers(options);
    if (!rows.length) {
      return `<div class="notice">${escapeHTML(emptyText)}</div>`;
    }
    return `
      <div class="target-table-wrap">
        <table class="target-table">
          <thead>
            <tr>
              <th>종목</th>
              <th>${emptyText.includes("현재") ? "현재 비중" : "목표 비중"}</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .slice(0, 12)
              .map(
                (row) => `
                  <tr>
                    <td>${escapeHTML(formatSymbolLabel(row, codeNameMap))}</td>
                    <td>${escapeHTML(fmtNum(asNumber(row.weight, 0) * 100, 2))}%</td>
                  </tr>
                `
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderTab(payload, errorMessage, options = {}) {
    const { escapeHTML, fmtNum, fmtKRW, fmtKST, asNumber } = helpers(options);
    if (errorMessage) {
      return `<div class="notice">리밸런싱 상태 조회 실패: ${escapeHTML(errorMessage)}</div>`;
    }
    if (!payload || typeof payload !== "object") {
      return '<div class="notice">리밸런싱 상태를 불러오는 중입니다.</div>';
    }

    const target = payload.target || {};
    const current = payload.current || {};
    const execution = payload.execution || {};
    const strategyConfig = payload.strategy_config || {};
    const strategyShowConfig = strategyConfig.show_config || {};
    const strategyOverride = strategyConfig.override || {};
    const targetRows = Array.isArray(target.rows) ? target.rows : [];
    const currentRows = Array.isArray(current.rows) ? current.rows : [];
    const currentRowsForTable = (() => {
      const rows = [...currentRows];
      const cashIndex = rows.findIndex((row) => String(row?.ticker || "").toUpperCase() === "KRW");
      if (cashIndex > 0) {
        const [cashRow] = rows.splice(cashIndex, 1);
        rows.unshift(cashRow);
      }
      return rows;
    })();
    const openPairs = Array.isArray(execution.open_pairs) ? execution.open_pairs : [];
    const codeNameMap = buildCodeNameMap(targetRows, currentRows);
    const targetInvested = asNumber(target.target_invested_ratio, 0);
    const actualInvested = asNumber(execution.actual_invested_ratio, 0);
    const investedGap = actualInvested - targetInvested;
    const openPairLabels = openPairs.map((pair) => openPairLabel(pair, codeNameMap));

    const headRows = [
      { label: "타깃 업데이트", value: target.updated_at ? fmtKST(target.updated_at, true) : "--" },
      { label: "현금 비중(목표)", value: `${fmtNum(asNumber(target.target_cash_weight, 0) * 100, 1)}%` },
      { label: "투자 비중(목표)", value: `${fmtNum(targetInvested * 100, 1)}%` },
      { label: "타깃 종목 수", value: String(targetRows.length) },
    ];

    const execRows = [
      { label: "오픈 트레이드", value: String(asNumber(execution.open_trade_count, 0)) },
      { label: "투자 비중(실제)", value: `${fmtNum(actualInvested * 100, 1)}%` },
      { label: "목표 대비 편차", value: `${fmtNum(investedGap * 100, 1)}%p` },
      { label: "오픈 스테이크", value: `${fmtKRW(asNumber(execution.open_stake_total_krw, 0))} KRW` },
      { label: "기준 총자산", value: `${fmtKRW(asNumber(execution.total_value_krw, 0))} KRW` },
    ];

    const strategyRows = [
      { label: "API 연결", value: strategyConfig.api_connected ? "connected" : "disconnected" },
      { label: "봇 상태", value: String(strategyShowConfig.state || "-") },
      { label: "전략", value: String(strategyShowConfig.strategy || "-") },
      { label: "타임프레임", value: String(strategyShowConfig.timeframe || "-") },
      { label: "거래 모드", value: String(strategyShowConfig.trading_mode || "-") },
      { label: "최대 오픈 트레이드", value: String(strategyShowConfig.max_open_trades ?? "-") },
      {
        label: "스테이크",
        value: `${String(strategyShowConfig.stake_amount || "-")} ${String(strategyShowConfig.stake_currency || "")}`.trim(),
      },
      {
        label: "강제진입 허용",
        value: strategyShowConfig.force_entry_enable ? "enabled" : "disabled",
      },
      {
        label: "리밸런싱 타깃 시각",
        value: strategyOverride.target_weights_updated_at
          ? fmtKST(strategyOverride.target_weights_updated_at, true)
          : "--",
      },
      {
        label: "타깃 종목 수",
        value: String(strategyOverride.pair_whitelist_count ?? "-"),
      },
      {
        label: "목표 현금 비중",
        value: `${fmtNum(asNumber(strategyOverride.target_cash_weight, 0) * 100, 1)}%`,
      },
    ];

    const targetTable = renderWeightTable(
      targetRows,
      codeNameMap,
      "리밸런싱 타깃 데이터가 없습니다.",
      options,
    );
    const currentTable = renderWeightTable(
      currentRowsForTable,
      codeNameMap,
      "현재 비중 데이터가 없습니다.",
      options,
    );

    return `
      <div class="helper-grid helper-runtime-grid">
        <article class="helper-card">
          <h4>리밸런싱 타깃</h4>
          <ul class="helper-runtime-list">
            ${renderRows(headRows, options)}
          </ul>
        </article>
        <article class="helper-card">
          <h4>실행 상태</h4>
          <ul class="helper-runtime-list">
            ${renderRows(execRows, options)}
          </ul>
        </article>
        <article class="helper-card">
          <h4>자동매매 전략 설정</h4>
          <ul class="helper-runtime-list">
            ${renderRows(strategyRows, options)}
          </ul>
        </article>
        <article class="helper-card helper-card-wide">
          <h4>목표 비중 테이블</h4>
          ${targetTable}
        </article>
        <article class="helper-card helper-card-wide">
          <h4>현재 비중 테이블</h4>
          ${currentTable}
        </article>
        <article class="helper-card helper-card-wide">
          <h4>현재 오픈 포지션 종목</h4>
          <p class="helper-text mono">${escapeHTML(openPairLabels.length ? openPairLabels.join(", ") : "-")}</p>
        </article>
      </div>
    `;
  }

  window.HERMES_REBALANCE_TAB = {
    formatSymbolLabel,
    renderTab,
  };
})();
