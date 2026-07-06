(() => {
  const ADMIN_TOKEN_KEY = "hermes_admin_token_v1";
  const protectedPrefixes = [
    "/backtest",
    "/dashboard",
    "/binance/",
    "/crypto/",
    "/discovery/",
    "/etf/",
    "/helper/",
    "/kis/",
    "/live/",
    "/llm/",
    "/market/",
    "/memory/",
    "/ops/",
    "/rebalance/",
    "/reports/",
    "/runtime/",
    "/settings/",
    "/strategy/",
    "/symbols/",
    "/telegram/",
  ];

  function readAdminToken() {
    try {
      return window.sessionStorage.getItem(ADMIN_TOKEN_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  function writeAdminToken(token) {
    try {
      if (token) {
        window.sessionStorage.setItem(ADMIN_TOKEN_KEY, token);
      } else {
        window.sessionStorage.removeItem(ADMIN_TOKEN_KEY);
      }
    } catch (_) {
      // sessionStorage can be unavailable in restricted browser modes.
    }
  }

  function adminAuthHeaders(token) {
    const cleanToken = String(token || readAdminToken() || "").trim();
    if (!cleanToken) return {};
    return {
      Authorization: `Bearer ${cleanToken}`,
      "X-TradeCraft-Admin-Token": cleanToken,
    };
  }

  function requestHasAdminToken(headers) {
    return Object.entries(headers || {}).some(([key, value]) => {
      const name = String(key || "").toLowerCase();
      return Boolean(value) && (name === "authorization" || name === "x-tradecraft-admin-token");
    });
  }

  function isProtectedApiPath(path) {
    const cleanPath = String(path || "").split("?")[0];
    return protectedPrefixes.some((prefix) => (
      cleanPath === prefix.slice(0, -1) || cleanPath.startsWith(prefix)
    ));
  }

  window.HERMES_UI_AUTH = Object.freeze({
    adminAuthHeaders,
    isProtectedApiPath,
    readAdminToken,
    requestHasAdminToken,
    writeAdminToken,
  });
})();
