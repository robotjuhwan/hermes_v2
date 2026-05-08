import { expect, test } from "@playwright/test";

test("reports console renders and runs action with confirm", async ({ page }) => {
  await page.route("**/ui-api/overview", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        updated_at: "2026-02-26T01:00:00+00:00",
        service: { name: "reports_api", version: "0.1.0", ui_refresh_sec: 10 },
        crawler: {
          enabled: true,
          interval_sec: 21600,
          since_date: "2025-06-04",
          seed_urls: ["https://finance.naver.com/research/company_list.naver"],
        },
        reports: {
          total_reports: 120,
          last_updated_at: "2026-02-26T01:00:00+00:00",
          last_published_at: "2026-02-25",
          total_symbols: 80,
          symbol_last_updated_at: "2026-02-26T00:00:00+00:00",
          category_counts: { company_analysis: 88, market_info: 32 },
        },
        rag: { available: true, count: 9500 },
      }),
    });
  });

  await page.route("**/ui-api/reports/recent**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        count: 1,
        items: [
          {
            report_id: 1,
            category: "company_analysis",
            title: "삼성전자 리포트",
            company_name: "삼성전자",
            broker: "테스트증권",
            analyst: "홍길동",
            symbol: "005930",
            published_at: "2026-02-25",
            updated_at: "2026-02-26T01:00:00+00:00",
            snippet: "실적과 밸류에이션 체크",
          },
        ],
      }),
    });
  });

  await page.route("**/ui-api/actions/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    });
  });

  page.on("dialog", async (dialog) => {
    expect(dialog.message()).toContain("실행");
    await dialog.accept();
  });

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "네이버 리서치 운영 콘솔" })).toBeVisible();
  await expect(page.getByText("리포트 총량")).toBeVisible();
  await expect(page.getByText("삼성전자 리포트")).toBeVisible();

  await page.getByRole("button", { name: "수집 1회 실행" }).click();
  await expect(page.getByText("실행 완료")).toBeVisible();
});
