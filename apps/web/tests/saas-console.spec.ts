import { expect, type Page, test } from "@playwright/test";

test.skip(process.env.NEXT_PUBLIC_USE_MOCKS === "false", "mock adapter coverage runs only in mock mode");

async function openConsole(page: Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "舆情 SaaS 控制台" })).toBeVisible();
}

test("opens every primary SaaS console section", async ({ page }) => {
  await openConsole(page);

  await expect(page.getByText("Mock adapter")).toBeVisible();
  await expect(page.getByRole("heading", { name: "运行总览" })).toBeVisible();
  await expect(page.getByText("Query Engine")).toBeVisible();
  await expect(page.getByText(":5432")).toHaveCount(0);

  for (const [nav, heading] of [
    ["报告", "报告任务"],
    ["爬虫", "爬虫任务"],
    ["爬取数据", "爬取数据库"],
    ["平台规则", "平台策略"],
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
  await expect(page.getByLabel("Query Engine")).toBeChecked();
  await expect(page.getByLabel("Media Engine")).toBeChecked();
  await expect(page.getByLabel("Insight Engine")).toBeChecked();

  await page.getByPlaceholder("输入报告主题").fill("BET-5 前端报告任务");
  await page.getByLabel("Query Engine").uncheck();
  await page.getByLabel("Media Engine").uncheck();
  await page.getByLabel("Insight Engine").uncheck();
  await page.getByRole("button", { name: "创建报告" }).click();
  await expect(page.getByText("至少选择一个分析引擎")).toBeVisible();

  await page.getByLabel("Insight Engine").check();
  await page.getByRole("button", { name: "创建报告" }).click();

  await expect(page.getByText("报告任务已创建")).toBeVisible();
  await expect(page.getByText("BET-5 前端报告任务")).toBeVisible();
  await expect(page.locator(".task-row", { hasText: "BET-5 前端报告任务" })).toContainText("Insight Engine");
});

test("validates crawler platform selection and creates a crawler task", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬虫" }).click();

  await expect(page.getByRole("heading", { name: "爬虫账号" })).toBeVisible();
  await expect(page.getByText("BettaFish 运营号")).toBeVisible();
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

test("filters crawler accounts by platform and status with empty state", async ({ page }) => {
  await openConsole(page);
  await page.getByRole("button", { name: "爬虫" }).click();

  await expect(page.getByText("3 / 3 个账号")).toBeVisible();
  await page.getByLabel("账号平台筛选").selectOption("xhs");
  await expect(page.getByText("1 / 3 个账号")).toBeVisible();
  await expect(page.getByText("研究采集号")).toBeVisible();
  await expect(page.getByText("BettaFish 运营号")).toHaveCount(0);

  await page.getByLabel("账号状态筛选").selectOption("active");
  await expect(page.getByText("暂无爬虫账号")).toBeVisible();

  await page.getByLabel("账号平台筛选").selectOption("all");
  await page.getByLabel("账号状态筛选").selectOption("expired");
  await expect(page.getByText("短视频监测")).toBeVisible();
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
