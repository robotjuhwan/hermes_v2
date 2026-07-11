(function () {
  const navigationGroups = Object.freeze([
    Object.freeze({
      id: "operations",
      label: "운용",
      items: Object.freeze([
        Object.freeze({ page: "main", tab: "", label: "자산 대시보드", eyebrow: "Dashboard" }),
        Object.freeze({ page: "helper", tab: "kis_trader", label: "국장 블록", eyebrow: "KIS" }),
        Object.freeze({ page: "helper", tab: "binance_trader", label: "크립토 블록", eyebrow: "Binance" }),
      ]),
    }),
    Object.freeze({
      id: "intelligence",
      label: "판단·리서치",
      items: Object.freeze([
        Object.freeze({ page: "helper", tab: "ask", label: "AI 질문", eyebrow: "Ask" }),
        Object.freeze({ page: "helper", tab: "strategy_intel", label: "전략·리서치", eyebrow: "Research" }),
        Object.freeze({ page: "helper", tab: "crypto_research", label: "크립토 리서치", eyebrow: "Crypto Lab" }),
      ]),
    }),
    Object.freeze({
      id: "learning",
      label: "학습",
      items: Object.freeze([
        Object.freeze({ page: "helper", tab: "kis_memory", label: "국장 쥬 메모리", eyebrow: "KIS Memory" }),
        Object.freeze({ page: "helper", tab: "binance_memory", label: "크립토 쥬 메모리", eyebrow: "Binance Memory" }),
        Object.freeze({ page: "helper", tab: "jue_wiki", label: "쥬 위키", eyebrow: "Jue Wiki" }),
      ]),
    }),
    Object.freeze({
      id: "system",
      label: "시스템",
      items: Object.freeze([
        Object.freeze({ page: "helper", tab: "runtime", label: "데이터·실행", eyebrow: "System" }),
        Object.freeze({ page: "helper", tab: "settings", label: "운영 설정", eyebrow: "Settings" }),
      ]),
    }),
  ]);
  const mobileNavItems = Object.freeze([
    Object.freeze({ page: "main", tab: "", label: "홈" }),
    Object.freeze({ page: "helper", tab: "kis_trader", label: "국장" }),
    Object.freeze({ page: "helper", tab: "binance_trader", label: "크립토" }),
    Object.freeze({ page: "helper", tab: "strategy_intel", label: "리서치" }),
    Object.freeze({ page: "menu", tab: "", label: "더보기" }),
  ]);

  window.HERMES_UI_TABS = Object.freeze({
    defaultHelperTab: "ask",
    activeBlockTabs: Object.freeze(["kis_trader", "binance_trader"]),
    navigationGroups,
    mobileNavItems,
    helperTabs: Object.freeze([
      "research",
      "strategy_intel",
      "kis_memory",
      "binance_memory",
      "jue_wiki",
      "market_judge",
      "ask",
      "runtime",
      "settings",
      "rebalance",
      "kis_trader",
      "binance_trader",
      "crypto_research",
      "reports",
    ]),
  });
})();
