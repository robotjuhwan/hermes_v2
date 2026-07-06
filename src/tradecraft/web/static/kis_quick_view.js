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

  function helpers(options = {}) {
    const escapeHTML = typeof options.escapeHTML === "function"
      ? options.escapeHTML
      : typeof UI_FORMATTERS.escapeHTML === "function"
        ? UI_FORMATTERS.escapeHTML
        : fallbackEscape;
    return {
      escapeHTML,
      fmtKRW: typeof options.fmtKRW === "function"
        ? options.fmtKRW
        : typeof UI_FORMATTERS.fmtKRW === "function"
          ? UI_FORMATTERS.fmtKRW
          : (value) => String(Math.round(Number(value || 0))),
      fmtNum: typeof options.fmtNum === "function"
        ? options.fmtNum
        : typeof UI_FORMATTERS.fmtNum === "function"
          ? UI_FORMATTERS.fmtNum
          : (value) => String(Number(value || 0)),
      orderedVenuesForDisplay: typeof options.orderedVenuesForDisplay === "function"
        ? options.orderedVenuesForDisplay
        : (venues) => (Array.isArray(venues) ? venues : []),
    };
  }

  function numberFrom(...values) {
    for (const value of values) {
      const numeric = Number(value);
      if (Number.isFinite(numeric)) return numeric;
    }
    return 0;
  }

  function accountPositionAsset(position) {
    const row = position && typeof position === "object" ? position : {};
    const symbol = String(row.symbol || row.asset || "").trim();
    const name = String(row.name || row.asset_name || symbol || "-").trim();
    const qty = numberFrom(row.qty, row.quantity, row.available_qty);
    const value = numberFrom(row.value_krw, row.market_value_krw, row.evaluation_amount_krw);
    return {
      asset: symbol,
      symbol,
      asset_name: name,
      kind: "stock",
      qty,
      available: numberFrom(row.available_qty, qty),
      locked: 0,
      avg_price: numberFrom(row.avg_price, row.average_price),
      mark_price: numberFrom(row.mark_price, row.current_price),
      value_krw: value,
      pnl_krw: numberFrom(row.unrealized_pnl_krw, row.pnl_krw),
    };
  }

  function kisVenueFromAccount(account) {
    if (!account || String(account.status || "").toLowerCase() !== "ok") return null;
    const cash = numberFrom(account.cash_krw, account.orderable_cash_krw);
    const orderableCash = numberFrom(account.orderable_cash_krw, cash);
    const rawPositions = Array.isArray(account.positions) ? account.positions : [];
    const positionAssets = rawPositions.map(accountPositionAsset).filter((asset) => asset.asset || asset.asset_name);
    const positionValue = numberFrom(
      account.position_value_krw,
      positionAssets.reduce((sum, asset) => sum + numberFrom(asset.value_krw), 0)
    );
    const total = numberFrom(account.total_value_krw, account.total_asset_krw, cash + positionValue);
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
          locked: 0,
          avg_price: 1,
          mark_price: 1,
          value_krw: cash,
          pnl_krw: 0,
        },
        ...positionAssets,
      ],
      cash_krw: cash,
      invested_krw: positionValue,
      unrealized_pnl_krw: numberFrom(account.unrealized_pnl_krw),
      total_krw: total,
      computed_total_krw: total,
      broker_total_krw: total,
      total_value_basis: "broker_net_asset",
      cache_status: "kis_blocks_account_fallback",
      position_count: numberFrom(account.position_count, positionAssets.length),
    };
  }

  function isGenericKisPositionName(asset) {
    const row = asset && typeof asset === "object" ? asset : {};
    const symbol = String(row.symbol || row.asset || "").trim();
    const name = String(row.asset_name || row.name || row.symbol || row.asset || "").trim();
    if (!name) return true;
    if (symbol && name === symbol) return true;
    return /^\d{5,6}$/.test(name);
  }

  function hasNamedKisPositions(venue) {
    const assets = Array.isArray(venue?.assets) ? venue.assets : [];
    return assets.some((asset) => asset?.kind !== "cash" && !isGenericKisPositionName(asset));
  }

  function hasOnlyGenericKisPositionNames(venue) {
    const assets = Array.isArray(venue?.assets) ? venue.assets : [];
    const positions = assets.filter((asset) => asset?.kind !== "cash");
    return positions.length > 0 && positions.every(isGenericKisPositionName);
  }

  function cacheAgeText(cachedAt, options = {}) {
    const raw = String(cachedAt || "").trim();
    if (!raw) return "";
    const cachedMs = Date.parse(raw);
    if (!Number.isFinite(cachedMs)) return "";
    const nowMs = Number.isFinite(Number(options.nowMs)) ? Number(options.nowMs) : Date.now();
    const ageSec = Math.max(0, Math.floor((nowMs - cachedMs) / 1000));
    if (ageSec < 90) return "방금 갱신";
    if (ageSec < 3600) return `${Math.round(ageSec / 60)}분 전`;
    if (ageSec < 86400) return `${Math.round(ageSec / 3600)}시간 전`;
    return `${Math.round(ageSec / 86400)}일 전`;
  }

  function kisCacheSourceLabel(venue, options = {}) {
    const cacheStatus = String(venue?.cache_status || venue?.cacheStatus || "").toLowerCase();
    const age = cacheAgeText(venue?.cached_at || venue?.cachedAt, options);
    if (cacheStatus === "kis_blocks_account_fallback") return "KIS 블록 계좌 스냅샷";
    if (cacheStatus === "stale") {
      return ["최근 성공 잔고", age].filter(Boolean).join(" · ");
    }
    if (cacheStatus === "fresh") {
      return ["대시보드 잔고", age || "최근 갱신"].filter(Boolean).join(" · ");
    }
    return age ? `잔고 ${age}` : "";
  }

  function shouldPreferKisAccountVenue(dashboardVenue, accountVenue) {
    if (!accountVenue) return false;
    const venue = dashboardVenue && typeof dashboardVenue === "object" ? dashboardVenue : {};
    const status = String(venue.status || "").toLowerCase();
    const cacheStatus = String(venue.cache_status || venue.cacheStatus || "").toLowerCase();
    const assets = Array.isArray(venue.assets) ? venue.assets : [];
    const dashboardPositions = assets.filter((asset) => asset && asset.kind !== "cash").length;
    const accountPositions = Number(accountVenue.position_count || 0);
    const dashboardTotal = Number(venue.total_krw || venue.total_value_krw || 0);
    const accountTotal = Number(accountVenue.total_krw || 0);
    return (
      status === "stale"
      || cacheStatus === "stale"
      || (accountPositions > 0 && dashboardPositions === 0)
      || (hasNamedKisPositions(accountVenue) && hasOnlyGenericKisPositionNames(venue))
      || (accountTotal > 0 && dashboardTotal <= 0)
    );
  }

  function kisQuickVenuesForDisplay(dashboardVenues, kisBlockStatus, options = {}) {
    const { orderedVenuesForDisplay } = helpers(options);
    const venues = orderedVenuesForDisplay(dashboardVenues || []).filter((item) =>
      ["kr_stock", "kr_stock_2"].includes(String(item.id || ""))
    );
    const account = kisBlockStatus?.account && typeof kisBlockStatus.account === "object"
      ? kisBlockStatus.account
      : null;
    const accountVenue = kisVenueFromAccount(account);
    if (venues.length) {
      return venues.map((venue) => {
        if (String(venue?.id || "") === "kr_stock" && shouldPreferKisAccountVenue(venue, accountVenue)) {
          return {
            ...venue,
            ...accountVenue,
            label: venue.label || accountVenue.label,
            market: venue.market || accountVenue.market,
          };
        }
        return venue;
      });
    }

    return accountVenue ? [accountVenue] : [];
  }

  function renderKisQuickStripHtml({
    authRequired = false,
    hasAdminToken = false,
    authMessage = "운영 토큰이 필요한 요청입니다.",
    dashboardVenues = [],
    kisBlockStatus = {},
  } = {}, options = {}) {
    const { escapeHTML, fmtKRW, fmtNum } = helpers(options);
    if (!hasAdminToken) {
      const reason = authRequired
        ? authMessage
        : "국장/블록 API는 보호 API라 이 브라우저 세션의 운영 토큰이 필요합니다.";
      return `
        <article class="kis-quick-card muted auth-gated">
          <div>
            <span class="section-kicker">KIS</span>
            <strong>국장 계좌 인증 대기</strong>
          </div>
          <p>KIS 장애나 국장 데이터 공백이 아니라 보호 API 인증 대기입니다. 브라우저 세션에 운영 토큰이 없어 국장 현금, 보유종목, 블록 상태를 숨겼습니다. 토큰은 이 브라우저 세션에만 저장되므로 재부팅, 새 브라우저, 세션 삭제 후에는 다시 입력해야 합니다. ${escapeHTML(reason)}</p>
          <button class="btn small warm" type="button" data-auth-focus="true">운영 토큰 입력</button>
        </article>
      `;
    }

    const venues = kisQuickVenuesForDisplay(dashboardVenues, kisBlockStatus, options);
    if (!venues.length) {
      return `
        <article class="kis-quick-card muted">
          <div>
            <span class="section-kicker">KIS</span>
            <strong>국장 계좌 대기</strong>
          </div>
          <p>운영 토큰 또는 KIS 연결 상태를 확인해 주세요.</p>
        </article>
      `;
    }

    return venues.map((venue) => {
      const assets = Array.isArray(venue.assets) ? venue.assets : [];
      const positions = assets.filter((asset) => asset.kind !== "cash");
      const positionCount = Number(venue.position_count || positions.length || 0);
      const positionText = positions.length
        ? positions
            .slice(0, 4)
            .map((asset) => {
              const name = asset.asset_name || asset.asset || asset.symbol || "-";
              const qty = Number(asset.qty || 0);
              return `${name} ${fmtNum(qty)}주`;
            })
            .join(" · ")
        : positionCount > 0
          ? `보유 ${fmtNum(positionCount, 0)}종목`
          : "보유 종목 없음";
      const moreText = positions.length > 4 ? ` · 외 ${positions.length - 4}개` : "";
      const pnl = Number(venue.unrealized_pnl_krw || 0);
      const basis = String(venue.total_value_basis || "") === "broker_net_asset" ? "공식 총평가" : "현금+보유";
      const sourceLabel = kisCacheSourceLabel(venue, options);
      const basisLabel = [basis, sourceLabel].filter(Boolean).join(" · ");
      const staleClass = String(venue.cache_status || venue.cacheStatus || "").toLowerCase() === "stale"
        ? " stale-cache"
        : "";
      return `
        <button class="kis-quick-card${staleClass}" type="button" data-venue="${escapeHTML(venue.id)}">
          <div class="kis-quick-title">
            <span class="section-kicker">KIS</span>
            <strong>${escapeHTML(venue.label || "국장")}</strong>
            <em>${escapeHTML(basisLabel)}</em>
          </div>
          <div class="kis-quick-values">
            <span>총 ${escapeHTML(fmtKRW(venue.total_krw))}</span>
            <span>현금 ${escapeHTML(fmtKRW(venue.cash_krw))}</span>
            <span>투자 ${escapeHTML(fmtKRW(venue.invested_krw))}</span>
            <span class="${pnl >= 0 ? "gain" : "loss"}">손익 ${escapeHTML(fmtKRW(pnl))}</span>
          </div>
          <p>${escapeHTML(positionText + moreText)}</p>
        </button>
      `;
    }).join("");
  }

  function renderKisAccountHoldingsPanel({
    dashboardVenues = [],
    payload = {},
  } = {}, options = {}) {
    const { escapeHTML, fmtKRW, fmtNum } = helpers(options);
    const venues = kisQuickVenuesForDisplay(dashboardVenues, payload, options);
    if (!venues.length) return "";
    const rows = venues.map((venue) => {
      const assets = Array.isArray(venue.assets) ? venue.assets : [];
      const positions = assets.filter((asset) => asset.kind !== "cash");
      const positionCount = Number(venue.position_count || positions.length || 0);
      const positionRows = positions.slice(0, 8).map((asset) => {
        const name = asset.asset_name || asset.asset || asset.symbol || "-";
        const symbol = asset.asset || asset.symbol || "";
        const qty = Number(asset.qty || 0);
        const value = Number(asset.value_krw || 0);
        const pnl = Number(asset.pnl_krw || 0);
        return `
          <li>
            <div>
              <strong>${escapeHTML(name)}</strong>
              <span class="mono">${escapeHTML(symbol)}</span>
            </div>
            <div class="runtime-kis-values">
              <span>${escapeHTML(fmtNum(qty))}주</span>
              <span>${escapeHTML(fmtKRW(value))}원</span>
              <span class="${pnl >= 0 ? "gain" : "loss"}">${escapeHTML(fmtKRW(pnl))}원</span>
            </div>
          </li>
        `;
      }).join("");
      const hiddenCount = Math.max(positions.length - 8, 0);
      const positionText = positionRows
        || (positionCount > 0 ? `<li><span>보유 ${escapeHTML(fmtNum(positionCount, 0))}종목 · 상세 종목 payload 대기</span></li>` : "<li><span>보유 종목 없음</span></li>");
      const sourceLabel = kisCacheSourceLabel(venue, options);
      return `
        <article class="helper-card helper-card-wide runtime-kis-snapshot">
          <div class="panel-head compact">
            <div>
              <h4>${escapeHTML(venue.label || "국장")} 계좌</h4>
              <p>총 ${escapeHTML(fmtKRW(venue.total_krw))}원 · 현금 ${escapeHTML(fmtKRW(venue.cash_krw))}원 · 투자 ${escapeHTML(fmtKRW(venue.invested_krw))}원${sourceLabel ? ` · ${escapeHTML(sourceLabel)}` : ""}</p>
            </div>
            <span class="strategy-data-chip ${Number(venue.unrealized_pnl_krw || 0) >= 0 ? "good" : "warn"}">손익 ${escapeHTML(fmtKRW(venue.unrealized_pnl_krw))}원</span>
          </div>
          <ul class="runtime-kis-list">
            ${positionText}
            ${hiddenCount > 0 ? `<li><span>외 ${escapeHTML(fmtNum(hiddenCount, 0))}개 보유</span></li>` : ""}
          </ul>
        </article>
      `;
    }).join("");
    return `
      <section class="memory-section">
        <div class="panel-head compact">
          <div>
            <h3>국장 계좌/보유 종목</h3>
            <p>활성 블록이 없어도 계좌 보유분은 계속 표시됩니다.</p>
          </div>
        </div>
        <div class="helper-grid">
          ${rows}
        </div>
      </section>
    `;
  }

  window.HERMES_KIS_QUICK_VIEW = Object.freeze({
    kisQuickVenuesForDisplay,
    renderKisAccountHoldingsPanel,
    renderKisQuickStripHtml,
  });
})();
