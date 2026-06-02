import { expect, type Locator, type Page, test } from "@playwright/test";

test.skip(process.env.NEXT_PUBLIC_USE_MOCKS === "false", "mock adapter coverage runs only in mock mode");

const crawlerDataPlatformTabs = ["全部平台", "微博", "小红书", "知乎", "抖音", "Bilibili", "贴吧", "快手"];

async function openConsole(page: Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "舆情 SaaS 控制台" })).toBeVisible();
}

async function expectCrawlerDataTitleOrder(row: Locator, title: string, keyword: string) {
  const items = await row.locator(".crawler-data-title").evaluate((titleNode) =>
    Array.from(titleNode.children).map((element) => ({
      className: String((element as HTMLElement).className),
      text: (element.textContent ?? "").trim()
    }))
  );

  expect(items[0]?.text).toBe(title);
  expect(items[1]).toMatchObject({
    className: expect.stringContaining("crawler-data-keyword-marker"),
    text: keyword
  });
  expect(items[2]?.text).toBe("内容");
  expect(items[3]?.text).toContain("情绪：");
}

async function expectCrawlerDataRowsWithinLayout(page: Page) {
  const layoutIssues = await page.locator(".crawler-data-row").evaluateAll((rows) =>
    rows.filter((row) => {
      const rowRect = row.getBoundingClientRect();
      const parentRect = row.parentElement?.getBoundingClientRect();
      const extendsPastContainer =
        Boolean(parentRect) && (rowRect.left < parentRect!.left - 1 || rowRect.right > parentRect!.right + 1);
      const extendsPastViewport = rowRect.left < -1 || rowRect.right > window.innerWidth + 1;
      const childRects = Array.from(row.children).map((child) => child.getBoundingClientRect());
      const hasOverlap = childRects.some((first, firstIndex) =>
        childRects.slice(firstIndex + 1).some(
          (second) =>
            first.left < second.right - 1 &&
            first.right > second.left + 1 &&
            first.top < second.bottom - 1 &&
            first.bottom > second.top + 1
        )
      );

      return extendsPastContainer || extendsPastViewport || hasOverlap;
    }).length
  );

  expect(layoutIssues).toBe(0);
}

test("opens every primary SaaS console section", async ({ page }) => {
  await openConsole(page);

  await expect(page.getByText("Mock adapter")).toBeVisible();
  await expect(page.getByRole("heading", { name: "运行总览" })).toBeVisible();
  await expect(page.getByText("问潮")).toBeVisible();
  await expect(page.getByText(":5432")).toHaveCount(0);

  for (const [nav, heading] of [
    ["报告", "报告任务"],
    ["爬虫", "爬虫任务"],
    ["爬取数据", "爬取数据库"],
    ["平台规则", "平台用户名单"],
    ["系统配置", "系统配置"],
    ["运行日志", "运行日志"]
  ] as const) {
    await page.getByRole("button", { name: nav }).click();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
  }
});

test("validates and creates a report task", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "报告" }).click();

  await page.getByRole("button", { name: "创建报告" }).click();
  await expect(page.getByText("报告主题不能为空")).toBeVisible();

  await expect(page.getByLabel("模板")).toHaveValue("auto");
  await expect(page.getByRole("option", { name: "自动选择" })).toBeAttached();
  await expect(page.getByLabel("问潮")).toBeChecked();
  await expect(page.getByLabel("听潮")).toBeChecked();
  await expect(page.getByLabel("知微")).toBeChecked();
  await expect(page.getByRole("radio", { name: "Normal" })).toBeChecked();
  await page.getByRole("radio", { name: "Deep" }).check();

  await page.getByPlaceholder("输入报告主题").fill("BET-5 前端报告任务");
  await page.getByLabel("问潮").uncheck();
  await page.getByLabel("听潮").uncheck();
  await page.getByLabel("知微").uncheck();
  await expect(page.getByRole("radio", { name: "Deep" })).toBeDisabled();
  await page.getByRole("button", { name: "创建报告" }).click();
  await expect(page.getByText("至少选择一个分析引擎")).toBeVisible();

  await page.getByLabel("知微").check();
  await expect(page.getByRole("radio", { name: "Deep" })).toBeEnabled();
  await expect(page.getByRole("radio", { name: "Deep" })).toBeChecked();
  await page.getByRole("button", { name: "创建报告" }).click();

  await expect(page.getByText("报告任务已创建")).toBeVisible();
  await expect(page.getByText("BET-5 前端报告任务")).toBeVisible();
  await expect(page.locator(".task-row", { hasText: "BET-5 前端报告任务" })).toContainText("知微");
  await expect(page.locator(".task-row", { hasText: "BET-5 前端报告任务" })).toContainText("Insight Deep");
});

test("validates crawler platform selection and creates a crawler task", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬虫" }).click();

  await expect(page.getByRole("heading", { name: "爬虫账号" })).toBeVisible();
  await expect(page.getByText("知潮 运营号")).toBeVisible();
  await expect(page.getByText("研究采集号")).toBeVisible();

  await page.getByLabel("微博").uncheck();

  await page.getByRole("button", { name: "创建任务" }).click();
  await expect(page.getByText("至少选择一个平台")).toBeVisible();

  await page.getByLabel("微博").check();
  await page.getByPlaceholder("每行一个关键词").fill("");
  await page.getByRole("button", { name: "创建任务" }).click();
  await expect(page.getByText("至少输入一个关键词")).toBeVisible();

  await page.getByPlaceholder("每行一个关键词").fill("养老服务\n医保支付");
  await expect(page.getByLabel("开始日期")).toHaveValue("2026-05-22");
  await expect(page.getByLabel("结束日期")).toHaveValue("2026-05-25");
  await expect(page.getByLabel("定时")).toHaveValue("manual");
  await page.getByRole("button", { name: "创建任务" }).click();
  await expect(page.getByText("爬虫任务已创建")).toBeVisible();
  await expect(page.getByText("养老服务 / 医保支付", { exact: true })).toBeVisible();
});

test("deletes report and crawler tasks", async ({ page }) => {
  await openConsole(page);

  await page.getByRole("button", { name: "报告" }).click();
  await page.locator(".task-row", { hasText: "养老服务发展趋势" }).getByTitle("删除报告任务").click();
  await expect(page.getByText("报告任务已删除")).toBeVisible();
  await expect(page.locator(".task-row", { hasText: "养老服务发展趋势" })).toHaveCount(0);

  await page.getByRole("button", { name: "爬虫" }).click();
  await page.locator(".task-row", { hasText: "养老服务 / 医保支付 / 养老院" }).getByTitle("删除爬虫任务").click();
  await expect(page.getByText("爬虫任务已删除")).toBeVisible();
  await expect(page.locator(".task-row", { hasText: "养老服务 / 医保支付 / 养老院" })).toHaveCount(0);
});

test("shows mock task logs newest first for reports and crawlers", async ({ page }) => {
  await openConsole(page);

  await page.getByRole("button", { name: "报告" }).click();
  await page.locator(".task-row", { hasText: "AI 教育硬件口碑变化" }).getByTitle("查看任务日志").click();
  const reportLogs = page.locator(".task-log-row");
  await expect(reportLogs.first()).toContainText("Report task report_20260522_002 generated outline");
  await expect(reportLogs.nth(1)).toContainText("Report task report_20260522_002 entered agent_running stage");

  await page.getByTitle("关闭").click();
  await page.getByRole("button", { name: "爬虫" }).click();
  await page.locator(".crawler-row", { hasText: "AI 教育硬件 / 学习机口碑" }).getByTitle("查看任务日志").click();
  const crawlerLogs = page.locator(".task-log-row");
  await expect(crawlerLogs.first()).toContainText("dy platform crawler finished comment enrichment");
  await expect(crawlerLogs.nth(1)).toContainText("dy platform crawler saved 312 notes and 1480 comments");
});

test("filters crawler accounts by platform and status with empty state", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬虫" }).click();

  await expect(page.getByText("3 / 3 个账号")).toBeVisible();
  await page.getByRole("tab", { name: /小红书/ }).click();
  await expect(page.getByText("1 / 3 个账号")).toBeVisible();
  await expect(page.getByText("研究采集号")).toBeVisible();
  await expect(page.getByText("知潮 运营号")).toHaveCount(0);

  await page.getByLabel("账号状态筛选").selectOption("active");
  await expect(page.getByText("暂无爬虫账号")).toBeVisible();

  await page.getByRole("tab", { name: /全部/ }).click();
  await page.getByLabel("账号状态筛选").selectOption("expired");
  await expect(page.getByText("短视频监测")).toBeVisible();
});

test("deletes crawler accounts", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬虫" }).click();

  await page.locator(".account-row", { hasText: "知潮 运营号" }).getByTitle("删除爬虫账号").click();
  await expect(page.getByText("爬虫账号已删除")).toBeVisible();
  await expect(page.locator(".account-row", { hasText: "知潮 运营号" })).toHaveCount(0);
  await expect(page.getByText("2 / 2 个账号")).toBeVisible();
});

test("adds a crawler account through the login modal", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬虫" }).click();

  await page.getByRole("button", { name: "增加账号" }).click();
  await expect(page.getByRole("dialog", { name: "增加账号" })).toBeVisible();
  await page.getByRole("button", { name: "打开登录页" }).click();

  await expect(page.getByText("登录状态已保存", { exact: true })).toBeVisible();
  await expect(page.getByText(/已登记：WB 采集号/)).toBeVisible();
});

test("searches crawler database records", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬取数据" }).click();

  await expect(page.getByRole("heading", { name: "爬取数据库" })).toBeVisible();
  await expect(page.getByTitle("刷新爬取数据")).toBeVisible();
  await expect(page.getByText("社区养老服务体验")).toBeVisible();
  await expect(page.getByText("正向").first()).toBeVisible();
  await expect(page.getByText("xhs_note", { exact: true })).toHaveCount(0);
  const contentRow = page.locator(".crawler-data-row", { hasText: "社区养老服务体验" });
  await expect(contentRow.locator(".crawler-data-keyword-marker")).toHaveText("养老服务");
  await expectCrawlerDataTitleOrder(contentRow, "社区养老服务体验", "养老服务");
  await expect(contentRow.getByTitle("打开原文")).toBeVisible();
  await expect(contentRow.getByTitle("删除爬取数据")).toBeVisible();
  await contentRow.getByTitle("删除爬取数据").click();
  await expect(page.getByText("爬取数据已删除")).toBeVisible();
  await expect(page.locator(".crawler-data-row", { hasText: "社区养老服务体验" })).toHaveCount(0);
  await page.getByLabel("类型").selectOption("comment");
  await page.getByPlaceholder("标题、正文、作者、关键词").fill("助餐");
  await page.getByRole("button", { name: "检索" }).click();

  await expect(page.getByText("希望社区能把助餐和康复服务放在一起")).toBeVisible();
  await expect(page.getByText("正向")).toHaveCount(1);
  await expect(page.getByText("社区养老服务体验")).toHaveCount(0);
});

test("filters crawler database records with platform tabs", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬取数据" }).click();

  const tablist = page.getByRole("tablist", { name: "爬取数据平台筛选" });
  for (const tabName of crawlerDataPlatformTabs) {
    await expect(tablist.getByRole("tab", { name: tabName })).toBeVisible();
  }

  await expect(tablist.getByRole("tab", { name: "全部平台" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".crawler-data-row", { hasText: "社区养老服务体验" })).toBeVisible();
  await expect(page.locator(".crawler-data-row", { hasText: "医保支付改革讨论" })).toBeVisible();

  await tablist.getByRole("tab", { name: "微博" }).click();
  await expect(tablist.getByRole("tab", { name: "微博" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".crawler-data-row", { hasText: "医保支付改革讨论" })).toBeVisible();
  await expect(page.locator(".crawler-data-row", { hasText: "社区养老服务体验" })).toHaveCount(0);
  const weiboRow = page.locator(".crawler-data-row", { hasText: "医保支付改革讨论" });
  await expect(weiboRow).not.toContainText("微博");
  await expect(weiboRow).not.toContainText("weibo_note");

  await tablist.getByRole("tab", { name: "小红书" }).click();
  await expect(tablist.getByRole("tab", { name: "小红书" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".crawler-data-row", { hasText: "社区养老服务体验" })).toBeVisible();
  await expect(page.locator(".crawler-data-row", { hasText: "医保支付改革讨论" })).toHaveCount(0);
});

test("selects current crawler data page and batch deletes records", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬取数据" }).click();

  await expect(page.locator(".crawler-data-row").first()).toBeVisible();
  await page.getByLabel("全选当前页爬取数据").check();
  await expect(page.getByText(/条已选/)).toBeVisible();
  await page.getByRole("button", { name: "删除选中" }).click();

  await expect(page.getByText("已删除选中爬取数据")).toBeVisible();
  await expect(page.locator(".crawler-data-row")).toHaveCount(0);
});

test("keeps crawler data rows compact on desktop and mobile", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await openConsole(page);
  await page.getByRole("button", { name: "爬取数据" }).click();

  await expect(page.locator(".crawler-data-row").first()).toBeVisible();
  await expectCrawlerDataRowsWithinLayout(page);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".crawler-data-row").first()).toBeVisible();
  await expectCrawlerDataRowsWithinLayout(page);
});

test("validates identity list input and adds a platform rule", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "平台规则" }).click();

  await page.getByRole("button", { name: "添加" }).click();
  await expect(page.getByText("用户 ID 不能为空")).toBeVisible();

  await page.getByPlaceholder("平台用户 ID").fill("blocked_user_005");
  await page.getByPlaceholder("标签").fill("测试屏蔽用户");
  await page.getByRole("button", { name: "添加" }).click();

  await expect(page.getByText("名单规则已添加")).toBeVisible();
  await expect(page.getByText("blocked_user_005")).toBeVisible();
  await expect(page.getByText("测试屏蔽用户")).toBeVisible();
});

test("keeps sensitive system configuration fields masked in the UI", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "系统配置" }).click();

  const reportApiKey = page.getByLabel("Report API Key");
  await expect(reportApiKey).toHaveAttribute("type", "password");
  await expect(reportApiKey).toHaveAttribute("placeholder", "********");
  await expect(reportApiKey).toHaveValue("");

  await reportApiKey.fill("sk-ui-secret");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByText("系统配置已保存")).toBeVisible();
});
