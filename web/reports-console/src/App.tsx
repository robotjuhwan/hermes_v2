import { useEffect, useMemo, useState } from "react";

type CategoryCountMap = Record<string, number>;

type SearchFilters = {
  query: string;
  symbol: string;
  category: string;
  broker: string;
  analyst: string;
  date_from: string;
  date_to: string;
};

type ReportsStatus = {
  total_reports: number;
  last_updated_at: string;
  last_published_at: string;
  total_symbols: number;
  symbol_last_updated_at: string;
  category_counts: CategoryCountMap;
};

type RagStatus = {
  available: boolean;
  reason?: string;
  count?: number;
};

type QualityIssue = {
  level: string;
  code: string;
  count: number;
  detail: string;
};

type QualityStatus = {
  status: string;
  issues: QualityIssue[];
  metrics: Record<string, number | string>;
};

type OverviewResponse = {
  status: string;
  updated_at: string;
  service: {
    name: string;
    version: string;
    ui_refresh_sec: number;
  };
  crawler: {
    enabled: boolean;
    interval_sec: number;
    since_date: string;
    seed_urls: string[];
  };
  reports: ReportsStatus;
  quality: QualityStatus;
  rag: RagStatus;
};

type RecentItem = {
  report_id: number;
  category: string;
  title: string;
  company_name: string;
  broker: string;
  analyst: string;
  symbol: string;
  published_at: string;
  updated_at: string;
  snippet: string;
  detail_url?: string;
};

type RecentResponse = {
  status: string;
  count: number;
  items: RecentItem[];
};

type ReportChunk = {
  chunk_id: number;
  chunk_index: number;
  page_start?: number;
  page_end?: number;
  section_title?: string;
  content: string;
};

type ReportDetail = {
  report_id: number;
  title: string;
  company_name?: string;
  symbol?: string;
  broker?: string;
  analyst?: string;
  category?: string;
  published_at?: string;
  updated_at?: string;
  detail_url?: string;
  pdf_url?: string;
  content?: string;
};

type ReportDetailResponse = {
  status: string;
  report: ReportDetail;
  facts: Record<string, unknown> | null;
  chunks: ReportChunk[];
};

type SavedView = {
  view_id: string;
  name: string;
  filters: SearchFilters & { limit: number };
  alert: {
    enabled: boolean;
    channel: string;
    target: string;
  };
  created_at: string;
  updated_at: string;
};

type SavedViewsResponse = {
  status: string;
  count: number;
  items: SavedView[];
};

type AlertPreviewResponse = {
  status: string;
  count: number;
  message: string;
};

type Toast = {
  id: number;
  tone: "ok" | "error";
  message: string;
};

const AUTO_REFRESH_MS = 10_000;
const RESULT_LIMIT = 20;
const EMPTY_FILTERS: SearchFilters = {
  query: "",
  symbol: "",
  category: "",
  broker: "",
  analyst: "",
  date_from: "",
  date_to: "",
};

async function requestJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
  });
  const payload = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const detail = String(payload.detail || "request failed");
    throw new Error(detail);
  }
  return payload as T;
}

function formatKST(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function compactText(value: string, max = 180): string {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "-";
  if (normalized.length <= max) return normalized;
  return `${normalized.slice(0, max - 3)}...`;
}

function buildSearchParams(filters: SearchFilters, limit = RESULT_LIMIT): URLSearchParams {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  Object.entries(filters).forEach(([key, value]) => {
    const text = String(value || "").trim();
    if (text) params.set(key, text);
  });
  return params;
}

function StatusCard({
  title,
  value,
  tone = "default",
}: {
  title: string;
  value: string;
  tone?: "default" | "ok" | "warn";
}) {
  return (
    <article className={`status-card ${tone}`}>
      <p>{title}</p>
      <strong>{value}</strong>
    </article>
  );
}

export default function App() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [recent, setRecent] = useState<RecentItem[]>([]);
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [draftFilters, setDraftFilters] = useState<SearchFilters>(EMPTY_FILTERS);
  const [activeFilters, setActiveFilters] = useState<SearchFilters>(EMPTY_FILTERS);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [savedViewsLoading, setSavedViewsLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [runningAction, setRunningAction] = useState("");
  const [runningSavedViewAction, setRunningSavedViewAction] = useState("");
  const [error, setError] = useState("");
  const [selectedReport, setSelectedReport] = useState<ReportDetailResponse | null>(null);
  const [selectedReportId, setSelectedReportId] = useState<number | null>(null);
  const [savedViewName, setSavedViewName] = useState("");
  const [savedViewAlertTarget, setSavedViewAlertTarget] = useState("");
  const [previewMessage, setPreviewMessage] = useState("");
  const [toasts, setToasts] = useState<Toast[]>([]);

  const pushToast = (tone: Toast["tone"], message: string) => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((prev) => [...prev, { id, tone, message }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((item) => item.id !== id));
    }, 3600);
  };

  const loadSavedViews = async (silent = false) => {
    if (!silent) setSavedViewsLoading(true);
    try {
      const response = await requestJSON<SavedViewsResponse>("/ui-api/saved-views");
      setSavedViews(Array.isArray(response.items) ? response.items : []);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "saved views load failed";
      pushToast("error", message);
    } finally {
      setSavedViewsLoading(false);
    }
  };

  const loadDashboard = async (filters: SearchFilters, silent = false) => {
    if (silent) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const params = buildSearchParams(filters, RESULT_LIMIT);
      const [overviewRes, recentRes] = await Promise.all([
        requestJSON<OverviewResponse>("/ui-api/overview"),
        requestJSON<RecentResponse>(`/ui-api/reports/recent?${params.toString()}`),
      ]);
      setOverview(overviewRes);
      setRecent(Array.isArray(recentRes.items) ? recentRes.items : []);
      setError("");
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "요청 실패";
      setError(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  const openReportDetail = async (reportId: number) => {
    setSelectedReportId(reportId);
    setDetailLoading(true);
    try {
      const response = await requestJSON<ReportDetailResponse>(
        `/ui-api/reports/${reportId}?include_chunks=true&chunk_limit=40`
      );
      setSelectedReport(response);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "리포트 조회 실패";
      pushToast("error", message);
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    void loadDashboard(activeFilters, false);
    const timer = window.setInterval(() => {
      void loadDashboard(activeFilters, true);
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [activeFilters]);

  useEffect(() => {
    void loadSavedViews(false);
  }, []);

  const runAction = async (kind: "crawl" | "rag" | "symbol") => {
    const guideMap = {
      crawl: "수집 1회를 실행하시겠습니까?",
      rag: "RAG 동기화를 실행하시겠습니까?",
      symbol: "심볼 사전 갱신을 실행하시겠습니까?",
    };
    if (!window.confirm(guideMap[kind])) {
      return;
    }

    const pathMap = {
      crawl: "/ui-api/actions/crawl-once",
      rag: "/ui-api/actions/rag-sync",
      symbol: "/ui-api/actions/symbol-refresh",
    };
    setRunningAction(kind);
    try {
      await requestJSON(pathMap[kind], {
        method: "POST",
        body: JSON.stringify({}),
      });
      pushToast("ok", "실행 완료");
      await loadDashboard(activeFilters, true);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "실행 실패";
      pushToast("error", message);
    } finally {
      setRunningAction("");
    }
  };

  const applyFilters = () => {
    setActiveFilters({ ...draftFilters });
  };

  const resetFilters = () => {
    setDraftFilters(EMPTY_FILTERS);
    setActiveFilters(EMPTY_FILTERS);
    setPreviewMessage("");
  };

  const saveCurrentView = async () => {
    const name = savedViewName.trim();
    if (!name) {
      pushToast("error", "저장할 뷰 이름을 입력하세요.");
      return;
    }
    setRunningSavedViewAction("save");
    try {
      await requestJSON("/ui-api/saved-views", {
        method: "POST",
        body: JSON.stringify({
          name,
          filters: { ...draftFilters, limit: RESULT_LIMIT },
          alert: {
            enabled: Boolean(savedViewAlertTarget.trim()),
            channel: "telegram",
            target: savedViewAlertTarget.trim(),
          },
        }),
      });
      setSavedViewName("");
      setSavedViewAlertTarget("");
      pushToast("ok", "저장된 뷰를 추가했습니다.");
      await loadSavedViews(true);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "저장 실패";
      pushToast("error", message);
    } finally {
      setRunningSavedViewAction("");
    }
  };

  const applySavedView = (view: SavedView) => {
    const nextFilters: SearchFilters = {
      query: view.filters.query || "",
      symbol: view.filters.symbol || "",
      category: view.filters.category || "",
      broker: view.filters.broker || "",
      analyst: view.filters.analyst || "",
      date_from: view.filters.date_from || "",
      date_to: view.filters.date_to || "",
    };
    setDraftFilters(nextFilters);
    setActiveFilters(nextFilters);
    setSavedViewAlertTarget(view.alert.target || "");
    setSavedViewName(view.name || "");
    setPreviewMessage("");
  };

  const deleteSavedView = async (view: SavedView) => {
    if (!window.confirm(`저장된 뷰 "${view.name}"를 삭제하시겠습니까?`)) {
      return;
    }
    setRunningSavedViewAction(`delete:${view.view_id}`);
    try {
      await requestJSON(`/ui-api/saved-views/${view.view_id}`, { method: "DELETE" });
      pushToast("ok", "저장된 뷰를 삭제했습니다.");
      await loadSavedViews(true);
      setPreviewMessage("");
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "삭제 실패";
      pushToast("error", message);
    } finally {
      setRunningSavedViewAction("");
    }
  };

  const previewSavedViewAlert = async (view: SavedView) => {
    setRunningSavedViewAction(`preview:${view.view_id}`);
    try {
      const response = await requestJSON<AlertPreviewResponse>(
        `/ui-api/saved-views/${view.view_id}/alert-preview`,
        {
          method: "POST",
          body: JSON.stringify({ limit: 5 }),
        }
      );
      setPreviewMessage(response.message || "");
      pushToast("ok", "알림 미리보기를 갱신했습니다.");
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "미리보기 실패";
      pushToast("error", message);
    } finally {
      setRunningSavedViewAction("");
    }
  };

  const sendTestAlert = async (view: SavedView) => {
    setRunningSavedViewAction(`alert:${view.view_id}`);
    try {
      await requestJSON(`/ui-api/saved-views/${view.view_id}/alert-test`, {
        method: "POST",
        body: JSON.stringify({ limit: 5 }),
      });
      pushToast("ok", "텔레그램 테스트 알림을 보냈습니다.");
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : "테스트 알림 실패";
      pushToast("error", message);
    } finally {
      setRunningSavedViewAction("");
    }
  };

  const categoryRows = useMemo(() => {
    const rows = Object.entries(overview?.reports?.category_counts || {});
    rows.sort((a, b) => b[1] - a[1]);
    return rows.slice(0, 8);
  }, [overview]);

  const factsRows = useMemo(() => {
    if (!selectedReport?.facts || typeof selectedReport.facts !== "object") {
      return [];
    }
    return Object.entries(selectedReport.facts).filter(([, value]) => value !== null && value !== "");
  }, [selectedReport]);

  const ragTone = overview?.rag?.available ? "ok" : "warn";
  const qualityTone =
    overview?.quality?.status === "error"
      ? "warn"
      : overview?.quality?.status === "warn"
        ? "warn"
        : "ok";

  return (
    <div className="console-root">
      <div className="ambient-grid" />

      <header className="hero-panel">
        <div>
          <p className="eyebrow">HERMES REPORT OPS</p>
          <h1>네이버 리서치 운영 콘솔</h1>
          <p className="lead">
            리포트 탐색, 드릴다운, 저장된 뷰, 테스트 알림까지 한 화면에서 운영합니다.
          </p>
        </div>
        <div className="hero-actions">
          <span className="chip">자동 갱신 10초</span>
          <span className="chip mono">{refreshing ? "동기화 중" : "대기"}</span>
          <button
            type="button"
            className="btn secondary"
            onClick={() => void loadDashboard(activeFilters, true)}
            disabled={refreshing || loading}
          >
            수동 새로고침
          </button>
        </div>
      </header>

      {error ? <div className="notice error">API 오류: {error}</div> : null}

      <section className="status-grid">
        <StatusCard title="리포트 총량" value={String(overview?.reports?.total_reports || 0)} />
        <StatusCard
          title="최근 업데이트"
          value={formatKST(String(overview?.reports?.last_updated_at || ""))}
        />
        <StatusCard
          title="최근 발행일"
          value={String(overview?.reports?.last_published_at || "-")}
        />
        <StatusCard
          title="심볼 사전"
          value={String(overview?.reports?.total_symbols || 0)}
          tone="ok"
        />
        <StatusCard
          title="RAG 상태"
          value={overview?.rag?.available ? "available" : String(overview?.rag?.reason || "unavailable")}
          tone={ragTone}
        />
        <StatusCard
          title="데이터 품질"
          value={String(overview?.quality?.status || "unknown")}
          tone={qualityTone}
        />
        <StatusCard
          title="수집 주기"
          value={`${Number(overview?.crawler?.interval_sec || 0)}초`}
          tone="default"
        />
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>데이터 가드레일</h2>
            <p className="mini-note">
              파일럿 운영에서 바로 봐야 하는 신선도와 메타데이터 품질 이슈를 표시합니다.
            </p>
          </div>
          <span className="chip mono">{overview?.quality?.issues?.length || 0}개 이슈</span>
        </div>
        {overview?.quality?.issues?.length ? (
          <div className="quality-list">
            {overview.quality.issues.map((issue) => (
              <article key={`${issue.code}-${issue.count}`} className={`quality-item ${issue.level}`}>
                <strong>{issue.code}</strong>
                <span>{issue.detail}</span>
                <em>count {issue.count}</em>
              </article>
            ))}
          </div>
        ) : (
          <div className="detail-empty">현재 감지된 데이터 품질 이슈가 없습니다.</div>
        )}
      </section>

      <section className="ops-grid">
        <article className="panel action-panel">
          <h2>운영 버튼</h2>
          <div className="action-stack">
            <button
              type="button"
              className="btn primary"
              onClick={() => void runAction("crawl")}
              disabled={runningAction.length > 0}
            >
              {runningAction === "crawl" ? "실행 중..." : "수집 1회 실행"}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => void runAction("rag")}
              disabled={runningAction.length > 0}
            >
              {runningAction === "rag" ? "실행 중..." : "RAG 동기화"}
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => void runAction("symbol")}
              disabled={runningAction.length > 0}
            >
              {runningAction === "symbol" ? "실행 중..." : "심볼 사전 갱신"}
            </button>
          </div>
        </article>

        <article className="panel">
          <h2>수집 설정 스냅샷</h2>
          <ul className="meta-list">
            <li>
              <span>수집 활성화</span>
              <strong>{overview?.crawler?.enabled ? "enabled" : "disabled"}</strong>
            </li>
            <li>
              <span>기준 시작일</span>
              <strong>{overview?.crawler?.since_date || "-"}</strong>
            </li>
            <li>
              <span>서비스 버전</span>
              <strong>{overview?.service?.version || "-"}</strong>
            </li>
            <li>
              <span>마지막 응답</span>
              <strong>{formatKST(String(overview?.updated_at || ""))}</strong>
            </li>
          </ul>
          <div className="seed-badges">
            {(overview?.crawler?.seed_urls || []).map((url) => (
              <span key={url} className="seed-chip mono">
                {url}
              </span>
            ))}
          </div>
        </article>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>리서치 워크플로우</h2>
            <p className="mini-note">
              드릴다운, 저장된 뷰, 테스트 알림으로 외부 파일럿 운영 흐름을 미리 검증합니다.
            </p>
          </div>
          <div className="panel-actions">
            <button type="button" className="btn secondary" onClick={applyFilters}>
              검색 적용
            </button>
            <button type="button" className="btn" onClick={resetFilters}>
              초기화
            </button>
          </div>
        </div>

        <div className="filters-grid">
          <label className="field">
            <span>키워드</span>
            <input
              value={draftFilters.query}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, query: event.target.value }))
              }
              placeholder="반도체, 수출, 실적"
            />
          </label>
          <label className="field">
            <span>종목코드</span>
            <input
              value={draftFilters.symbol}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, symbol: event.target.value }))
              }
              placeholder="005930"
            />
          </label>
          <label className="field">
            <span>카테고리</span>
            <input
              value={draftFilters.category}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, category: event.target.value }))
              }
              placeholder="company_analysis"
            />
          </label>
          <label className="field">
            <span>증권사</span>
            <input
              value={draftFilters.broker}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, broker: event.target.value }))
              }
              placeholder="미래에셋증권"
            />
          </label>
          <label className="field">
            <span>애널리스트</span>
            <input
              value={draftFilters.analyst}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, analyst: event.target.value }))
              }
              placeholder="홍길동"
            />
          </label>
          <label className="field">
            <span>발행 시작일</span>
            <input
              type="date"
              value={draftFilters.date_from}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, date_from: event.target.value }))
              }
            />
          </label>
          <label className="field">
            <span>발행 종료일</span>
            <input
              type="date"
              value={draftFilters.date_to}
              onChange={(event) =>
                setDraftFilters((prev) => ({ ...prev, date_to: event.target.value }))
              }
            />
          </label>
        </div>
      </section>

      <section className="workflow-grid">
        <article className="panel saved-views-panel">
          <div className="panel-head">
            <div>
              <h2>저장된 뷰</h2>
              <p className="mini-note">반복 검색을 저장하고, 텔레그램 테스트 알림까지 바로 보냅니다.</p>
            </div>
            <span className="chip mono">{savedViewsLoading ? "불러오는 중" : `${savedViews.length}개`}</span>
          </div>

          <div className="saved-view-form">
            <label className="field">
              <span>뷰 이름</span>
              <input
                value={savedViewName}
                onChange={(event) => setSavedViewName(event.target.value)}
                placeholder="반도체 모니터"
              />
            </label>
            <label className="field">
              <span>Telegram chat_id</span>
              <input
                value={savedViewAlertTarget}
                onChange={(event) => setSavedViewAlertTarget(event.target.value)}
                placeholder="비워두면 기본 chat_id 사용"
              />
            </label>
            <button
              type="button"
              className="btn primary"
              onClick={() => void saveCurrentView()}
              disabled={runningSavedViewAction === "save"}
            >
              {runningSavedViewAction === "save" ? "저장 중..." : "현재 검색 저장"}
            </button>
          </div>

          <div className="saved-view-list">
            {savedViews.length === 0 ? (
              <div className="detail-empty">저장된 뷰가 없습니다.</div>
            ) : (
              savedViews.map((view) => (
                <article key={view.view_id} className="saved-view-card">
                  <div className="saved-view-header">
                    <strong>{view.name}</strong>
                    <span className="chip mono">{view.alert.target ? "telegram" : "default-alert"}</span>
                  </div>
                  <div className="saved-view-meta">
                    <span>{compactText(Object.entries(view.filters)
                      .filter(([, value]) => String(value || "").trim())
                      .map(([key, value]) => `${key}:${value}`)
                      .join(" | "), 120)}</span>
                    <span>갱신 {formatKST(view.updated_at)}</span>
                  </div>
                  <div className="saved-view-actions">
                    <button type="button" className="btn secondary" onClick={() => applySavedView(view)}>
                      적용
                    </button>
                    <button
                      type="button"
                      className="btn"
                      onClick={() => void previewSavedViewAlert(view)}
                      disabled={runningSavedViewAction === `preview:${view.view_id}`}
                    >
                      {runningSavedViewAction === `preview:${view.view_id}` ? "생성 중..." : "알림 미리보기"}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      onClick={() => void sendTestAlert(view)}
                      disabled={runningSavedViewAction === `alert:${view.view_id}`}
                    >
                      {runningSavedViewAction === `alert:${view.view_id}` ? "전송 중..." : "텔레그램 테스트"}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      onClick={() => void deleteSavedView(view)}
                      disabled={runningSavedViewAction === `delete:${view.view_id}`}
                    >
                      삭제
                    </button>
                  </div>
                </article>
              ))
            )}
          </div>

          {previewMessage ? (
            <div className="preview-box">
              <h3>알림 미리보기</h3>
              <pre>{previewMessage}</pre>
            </div>
          ) : null}
        </article>

        <article className="panel detail-panel">
          <div className="panel-head">
            <div>
              <h2>리포트 드릴다운</h2>
              <p className="mini-note">
                최근 리포트에서 바로 상세 내용과 구조화 팩트를 확인합니다.
              </p>
            </div>
            {selectedReport?.report?.detail_url ? (
              <a
                className="link-btn"
                href={selectedReport.report.detail_url}
                target="_blank"
                rel="noreferrer"
              >
                원문 열기
              </a>
            ) : null}
          </div>

          {detailLoading ? (
            <div className="detail-empty">리포트를 불러오는 중...</div>
          ) : selectedReport ? (
            <div className="detail-shell">
              <div className="detail-headline">
                <strong>{selectedReport.report.title || "제목 없음"}</strong>
                <span>
                  {selectedReport.report.company_name || "-"} / {selectedReport.report.symbol || "-"}
                </span>
                <span>
                  {selectedReport.report.broker || "-"} / {selectedReport.report.analyst || "-"}
                </span>
              </div>

              {factsRows.length ? (
                <div className="facts-grid">
                  {factsRows.map(([key, value]) => (
                    <article key={key} className="fact-card">
                      <span>{key}</span>
                      <strong>{String(value)}</strong>
                    </article>
                  ))}
                </div>
              ) : null}

              <div className="detail-block">
                <h3>본문 요약</h3>
                <p>{compactText(selectedReport.report.content || "", 800)}</p>
              </div>

              <div className="chunk-list">
                {selectedReport.chunks.length ? (
                  selectedReport.chunks.map((chunk) => (
                    <article key={`${chunk.chunk_id}-${chunk.chunk_index}`} className="chunk-card">
                      <div className="chunk-head">
                        <strong>{chunk.section_title || `chunk ${chunk.chunk_index}`}</strong>
                        <span>
                          #{chunk.chunk_index}
                          {chunk.page_start || chunk.page_end
                            ? ` / p.${chunk.page_start || 0}-${chunk.page_end || 0}`
                            : ""}
                        </span>
                      </div>
                      <p>{chunk.content}</p>
                    </article>
                  ))
                ) : (
                  <div className="detail-empty">청크 데이터가 없습니다.</div>
                )}
              </div>
            </div>
          ) : (
            <div className="detail-empty">
              최근 수집 리포트에서 <strong>드릴다운</strong>을 눌러 상세를 확인하세요.
            </div>
          )}
        </article>
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>최근 수집 리포트 (최대 20건)</h2>
            <p className="mini-note">
              현재 검색 조건에 맞는 최신 리포트를 확인하고 바로 상세 화면으로 내려갑니다.
            </p>
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>발행일</th>
                <th>카테고리</th>
                <th>제목</th>
                <th>종목</th>
                <th>증권사 / 애널리스트</th>
                <th>업데이트</th>
                <th>액션</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={7}>불러오는 중...</td>
                </tr>
              ) : recent.length === 0 ? (
                <tr>
                  <td colSpan={7}>표시할 리포트가 없습니다.</td>
                </tr>
              ) : (
                recent.map((row) => (
                  <tr key={`${row.report_id}-${row.updated_at}`}>
                    <td>{row.published_at || "-"}</td>
                    <td>{row.category || "unknown"}</td>
                    <td>
                      <div className="title-cell">
                        <strong>{row.title || "제목 없음"}</strong>
                        <span>{compactText(row.snippet || "")}</span>
                      </div>
                    </td>
                    <td>{`${row.company_name || "-"} / ${row.symbol || "-"}`}</td>
                    <td>{`${row.broker || "-"} / ${row.analyst || "-"}`}</td>
                    <td>{formatKST(row.updated_at)}</td>
                    <td>
                      <div className="table-actions">
                        <button
                          type="button"
                          className="btn secondary"
                          onClick={() => void openReportDetail(row.report_id)}
                          disabled={detailLoading && selectedReportId === row.report_id}
                        >
                          {detailLoading && selectedReportId === row.report_id ? "불러오는 중..." : "드릴다운"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>카테고리 분포</h2>
        <div className="category-chips">
          {categoryRows.length ? (
            categoryRows.map(([label, count]) => (
              <span key={label} className="category-chip">
                {label} · {count}
              </span>
            ))
          ) : (
            <span className="category-chip">데이터 없음</span>
          )}
        </div>
      </section>

      <div className="toast-layer" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.tone}`}>
            {toast.message}
          </div>
        ))}
      </div>
    </div>
  );
}
