(function () {
  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => {
      if (ch === "&") return "&amp;";
      if (ch === "<") return "&lt;";
      if (ch === ">") return "&gt;";
      if (ch === '"') return "&quot;";
      return "&#39;";
    });
  }

  function fmtKRW(value) {
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: 0,
    }).format(Number(value || 0));
  }

  function fmtNum(value, maxFractionDigits = 4) {
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: maxFractionDigits,
    }).format(Number(value || 0));
  }

  function formatLiveMultiplier(value) {
    if (value === null || value === undefined || value === "") return "-";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return "-";
    return `${fmtNum(numeric, 2)}x`;
  }

  function fmtUSDT(value, maxFractionDigits = 4) {
    const numeric = Number(value || 0);
    const sign = numeric > 0 ? "+" : "";
    return `${sign}${fmtNum(numeric, maxFractionDigits)} USDT`;
  }

  function fmtPercent(value, maxFractionDigits = 1) {
    return `${fmtNum(value, maxFractionDigits)}%`;
  }

  function fmtMaybeKRW(value) {
    if (value === null || value === undefined) return "-";
    return fmtKRW(value);
  }

  function asNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function fmtKST(isoString, withDate = false) {
    if (!isoString) return "--";
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return "--";
    const parts = new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).formatToParts(date);
    const pick = (type) => parts.find((part) => part.type === type)?.value || "";
    const stamp = `${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}:${pick("second")}`;
    if (withDate) return stamp;
    return `${pick("hour")}:${pick("minute")}:${pick("second")}`;
  }

  function fmtDurationSec(value) {
    const seconds = Math.max(0, Math.round(Number(value || 0)));
    if (seconds < 60) return `${seconds}초`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}분`;
    const hours = Math.floor(minutes / 60);
    const restMinutes = minutes % 60;
    if (hours < 24) {
      return restMinutes ? `${hours}시간 ${restMinutes}분` : `${hours}시간`;
    }
    const days = Math.floor(hours / 24);
    const restHours = hours % 24;
    return restHours ? `${days}일 ${restHours}시간` : `${days}일`;
  }

  function fmtBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = bytes;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }
    const digits = unitIndex >= 3 ? 1 : 0;
    return `${size.toFixed(digits)} ${units[unitIndex]}`;
  }

  function truncateWithEllipsis(value, maxChars = 180) {
    const compact = String(value ?? "").replace(/\s+/g, " ").trim();
    if (!compact) return "-";
    const limit = Math.max(Number(maxChars) || 0, 8);
    if (compact.length <= limit) return compact;
    return `${compact.slice(0, limit - 3)}...`;
  }

  window.HERMES_UI_FORMATTERS = Object.freeze({
    asNumber,
    escapeHTML,
    fmtBytes,
    fmtDurationSec,
    fmtKRW,
    fmtKST,
    fmtMaybeKRW,
    fmtNum,
    fmtPercent,
    fmtUSDT,
    formatLiveMultiplier,
    truncateWithEllipsis,
  });
})();
