import { expect, test } from "@playwright/test";

test.skip(process.env.NEXT_PUBLIC_USE_MOCKS !== "false", "real API coverage requires NEXT_PUBLIC_USE_MOCKS=false");

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:4010/api/v1";
const timestamp = "2026-05-22T10:00:00Z";

test("loads and deletes existing identity rules through real API routes", async ({ page }) => {
  const identityRequests: string[] = [];
  const deleteRequests: string[] = [];
  let wbRules = [
    {
      id: "identity_wb_block_001",
      platformId: "wb",
      listType: "block",
      userId: "blocked_existing_001",
      label: "既有屏蔽用户",
      createdAt: timestamp
    },
    {
      id: "identity_wb_allow_001",
      platformId: "wb",
      listType: "allow",
      userId: "allowed_existing_002",
      label: "既有白名单用户",
      createdAt: timestamp
    }
  ];

  await routeJson(page, "/system/components", {
    success: true,
    components: [
      { id: "query", name: "Query Engine", status: "running", port: 9001, lastHeartbeatAt: timestamp },
      { id: "media", name: "Media Engine", status: "running", port: 9002, lastHeartbeatAt: timestamp },
      { id: "insight", name: "Insight Engine", status: "degraded", port: 9003, lastHeartbeatAt: timestamp },
      { id: "report", name: "Report Engine", status: "running" },
      { id: "mindspider", name: "MindSpider", status: "running" }
    ]
  });
  await routeJson(page, "/report-templates", {
    success: true,
    templates: [{ id: "daily-monitoring", name: "日报", filename: "daily.md", description: "", sizeBytes: 10 }]
  });
  await routeJson(page, "/report-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-strategies", {
    success: true,
    strategies: [
      {
        id: "strategy_daily",
        workspaceId: "workspace_demo",
        name: "每日采集",
        runMode: "deep_sentiment",
        platformPolicies: [],
        createdAt: timestamp,
        updatedAt: timestamp
      }
    ]
  });
  await routeJson(page, "/crawler-accounts", {
    success: true,
    accounts: [
      {
        id: "acct_wb_ops",
        workspaceId: "workspace_demo",
        platformId: "wb",
        accountId: "wb_1088",
        status: "active",
        username: "bettafish_ops",
        displayName: "BettaFish 运营号",
        loginType: "qrcode",
        lastCheckedAt: timestamp,
        createdAt: timestamp,
        updatedAt: timestamp,
        details: {
          message: "账号可用于搜索和评论采集。"
        }
      }
    ]
  });
  await routeJson(page, "/platforms", {
    success: true,
    platforms: [
      platform("wb", "微博", { allow: 1, block: 1 }),
      platform("xhs", "小红书", { allow: 0, block: 0 })
    ]
  });
  await routeJson(page, "/system/config", {
    success: true,
    fields: [
      {
        key: "REPORT_ENGINE_API_KEY",
        label: "Report API Key",
        group: "llm",
        type: "secret",
        value: "********",
        editable: true,
        sensitive: true
      }
    ]
  });
  await routeJson(page, "/logs?tail=300", { success: true, lines: [] });

  await page.route(`${apiBase}/platforms/*/identity-lists`, async (route) => {
    const requestUrl = new URL(route.request().url());
    const parts = requestUrl.pathname.split("/");
    const platformId = parts[parts.indexOf("platforms") + 1];
    identityRequests.push(platformId);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        rules: platformId === "wb" ? wbRules : []
      })
    });
  });
  await page.route(`${apiBase}/platforms/wb/identity-lists/identity_wb_block_001`, async (route) => {
    expect(route.request().method()).toBe("DELETE");
    deleteRequests.push(route.request().url());
    wbRules = wbRules.filter((rule) => rule.id !== "identity_wb_block_001");
    await route.fulfill({ status: 204 });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("API connected")).toBeVisible();
  await expect(page.getByText("Query Engine")).toBeVisible();
  await expect(page.getByText(/:900[123]/)).toHaveCount(0);
  await page.getByRole("button", { name: "平台规则" }).click();

  expect(identityRequests).toEqual(["wb", "xhs"]);
  await expect(page.getByText("blocked_existing_001")).toBeVisible();
  await expect(page.getByText("既有屏蔽用户")).toBeVisible();
  await expect(page.getByText("allowed_existing_002")).toBeVisible();

  await page.locator(".rule-row", { hasText: "blocked_existing_001" }).getByTitle("删除名单规则").click();
  await expect(page.getByText("名单规则已删除")).toBeVisible();
  await expect(page.getByText("blocked_existing_001")).toHaveCount(0);
  expect(deleteRequests).toHaveLength(1);
});

test("creates crawler tasks with explicit keywords and selected platforms", async ({ page }) => {
  let crawlerPayload: Record<string, unknown> | undefined;

  await routeJson(page, "/system/components", {
    success: true,
    components: [{ id: "mindspider", name: "MindSpider", status: "running" }]
  });
  await routeJson(page, "/report-templates", { success: true, templates: [] });
  await routeJson(page, "/report-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-strategies", { success: true, strategies: [] });
  await routeJson(page, "/crawler-accounts", { success: true, accounts: [] });
  await routeJson(page, "/platforms", {
    success: true,
    platforms: [
      platform("wb", "微博", { allow: 0, block: 0 }),
      platform("xhs", "小红书", { allow: 0, block: 0 })
    ]
  });
  await routeJson(page, "/system/config", { success: true, fields: [] });
  await routeJson(page, "/logs?tail=300", { success: true, lines: [] });
  await page.route(`${apiBase}/platforms/*/identity-lists`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, rules: [] })
    });
  });
  await page.route(`${apiBase}/crawler-tasks`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true, tasks: [] })
      });
      return;
    }

    expect(route.request().method()).toBe("POST");
    crawlerPayload = route.request().postDataJSON();
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        task: {
          id: "crawler_new",
          workspaceId: "workspace_demo",
          runMode: crawlerPayload?.runMode,
          status: "queued",
          progress: 0,
          targetDate: crawlerPayload?.targetDate ?? crawlerPayload?.startDate,
          startDate: crawlerPayload?.startDate,
          endDate: crawlerPayload?.endDate,
          schedule: crawlerPayload?.schedule,
          crawlDepth: crawlerPayload?.crawlDepth,
          platforms: crawlerPayload?.platforms,
          keywords: crawlerPayload?.keywords,
          keywordSource: crawlerPayload?.keywordSource,
          stats: {
            totalKeywords: 2,
            totalPlatforms: 2,
            totalTasks: 4,
            successfulTasks: 0,
            failedTasks: 0,
            totalNotes: 0,
            totalComments: 0
          },
          createdAt: timestamp,
          updatedAt: timestamp
        }
      })
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "爬虫" }).click();
  await page.getByLabel("小红书").check();
  await page.getByPlaceholder("每行一个关键词").fill(" 养老服务 \n医保支付\n养老服务");
  await page.getByRole("button", { name: "创建任务" }).click();

  expect(crawlerPayload).toMatchObject({
    runMode: "deep_sentiment",
    startDate: "2026-05-22",
    endDate: "2026-05-25",
    schedule: { mode: "manual", timezone: "Asia/Shanghai" },
    platforms: ["wb", "xhs"],
    keywords: ["养老服务", "医保支付"],
    keywordSource: "manual",
    crawlDepth: 3
  });
  await expect(page.getByText("爬虫任务已创建")).toBeVisible();
});

test("polls active crawler tasks until backend completion", async ({ page }) => {
  let completeCrawlerTask: () => void;
  const completionReady = new Promise<void>((resolve) => {
    completeCrawlerTask = resolve;
  });
  let crawlerTaskRequests = 0;
  const runningTask = {
    id: "crawler_running",
    workspaceId: "workspace_demo",
    runMode: "deep_sentiment",
    status: "running",
    progress: 75,
    targetDate: "2026-05-22",
    startDate: "2026-05-22",
    endDate: "2026-05-25",
    schedule: { mode: "manual", timezone: "Asia/Shanghai" },
    platforms: ["wb"],
    keywords: ["养老服务"],
    keywordSource: "manual",
    stats: {
      totalKeywords: 1,
      totalPlatforms: 1,
      totalTasks: 1,
      successfulTasks: 0,
      failedTasks: 0,
      totalNotes: 12,
      totalComments: 34
    },
    createdAt: timestamp,
    updatedAt: timestamp
  };
  const completedTask = {
    ...runningTask,
    status: "succeeded",
    progress: 100,
    stats: {
      ...runningTask.stats,
      successfulTasks: 1
    },
    updatedAt: "2026-05-22T10:05:00Z"
  };

  await routeJson(page, "/system/components", {
    success: true,
    components: [{ id: "mindspider", name: "MindSpider", status: "running" }]
  });
  await routeJson(page, "/report-templates", { success: true, templates: [] });
  await routeJson(page, "/report-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-strategies", { success: true, strategies: [] });
  await routeJson(page, "/crawler-accounts", { success: true, accounts: [] });
  await routeJson(page, "/platforms", {
    success: true,
    platforms: [platform("wb", "微博", { allow: 0, block: 0 })]
  });
  await routeJson(page, "/system/config", { success: true, fields: [] });
  await routeJson(page, "/logs?tail=300", { success: true, lines: [] });
  await page.route(`${apiBase}/platforms/*/identity-lists`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, rules: [] })
    });
  });
  await page.route(`${apiBase}/crawler-tasks`, async (route) => {
    expect(route.request().method()).toBe("GET");
    crawlerTaskRequests += 1;
    if (crawlerTaskRequests > 1) {
      await completionReady;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        tasks: [crawlerTaskRequests === 1 ? runningTask : completedTask]
      })
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "爬虫" }).click();
  const crawlerRow = page.locator(".crawler-row", { hasText: "养老服务" });
  await expect(crawlerRow).toContainText("running");

  completeCrawlerTask!();
  await expect(crawlerRow).toContainText("succeeded");
  expect(crawlerTaskRequests).toBeGreaterThan(1);
});

test("creates report tasks with selected orchestration engines", async ({ page }) => {
  let reportPayload: Record<string, unknown> | undefined;
  let reportTasks: Record<string, unknown>[] = [];

  await routeJson(page, "/system/components", {
    success: true,
    components: [
      { id: "query", name: "Query Engine", status: "running" },
      { id: "media", name: "Media Engine", status: "running" },
      { id: "insight", name: "Insight Engine", status: "running" },
      { id: "report", name: "Report Engine", status: "running" }
    ]
  });
  await routeJson(page, "/report-templates", { success: true, templates: [] });
  await page.route(`${apiBase}/report-tasks`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true, tasks: reportTasks })
      });
      return;
    }

    expect(route.request().method()).toBe("POST");
    reportPayload = route.request().postDataJSON();
    const task = {
      id: `report_new_${reportTasks.length + 1}`,
      workspaceId: "workspace_demo",
      topic: reportPayload?.topic,
      status: "queued",
      progress: 0,
      stage: "queued",
      templateId: reportPayload?.templateId,
      sourceScope: reportPayload?.sourceScope,
      artifacts: [{ format: "html", ready: false }],
      createdAt: timestamp,
      updatedAt: timestamp
    };
    reportTasks = [task, ...reportTasks];
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        task
      })
    });
  });
  await routeJson(page, "/crawler-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-strategies", { success: true, strategies: [] });
  await routeJson(page, "/crawler-accounts", { success: true, accounts: [] });
  await routeJson(page, "/platforms", {
    success: true,
    platforms: [platform("wb", "微博", { allow: 0, block: 0 })]
  });
  await routeJson(page, "/system/config", { success: true, fields: [] });
  await routeJson(page, "/logs?tail=300", { success: true, lines: [] });
  await page.route(`${apiBase}/platforms/*/identity-lists`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, rules: [] })
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "报告" }).click();
  await page.getByPlaceholder("输入报告主题").fill("多引擎调度验证");
  await page.getByRole("radio", { name: "Fast" }).check();
  await page.getByLabel("Query Engine").uncheck();
  await page.getByRole("button", { name: "创建报告" }).click();

  expect(reportPayload).toMatchObject({
    topic: "多引擎调度验证",
    templateId: "auto",
    sourceScope: {
      orchestration: {
        enabled: true,
        insightMode: "fast",
        engines: ["media", "insight"]
      }
    },
    outputFormats: ["html", "pdf"]
  });
  await expect(page.getByText("报告任务已创建")).toBeVisible();
  await expect(page.locator(".task-row", { hasText: "多引擎调度验证" })).toContainText(
    "Media Engine / Insight Engine"
  );
  await expect(page.locator(".task-row", { hasText: "多引擎调度验证" })).toContainText("Insight Fast");

  reportPayload = undefined;
  await page.getByPlaceholder("输入报告主题").fill("非 Insight 调度验证");
  await page.getByLabel("Query Engine").check();
  await page.getByLabel("Insight Engine").uncheck();
  await expect(page.getByRole("radio", { name: "Fast" })).toBeDisabled();
  await page.getByRole("button", { name: "创建报告" }).click();

  expect(reportPayload).toMatchObject({
    topic: "非 Insight 调度验证",
    sourceScope: {
      orchestration: {
        enabled: true,
        insightMode: "fast",
        engines: ["query", "media"]
      }
    }
  });
  await expect(page.locator(".task-row", { hasText: "非 Insight 调度验证" })).toContainText(
    "Query Engine / Media Engine"
  );
  await expect(page.locator(".task-row", { hasText: "非 Insight 调度验证" })).toContainText(
    "Insight Off"
  );
});

test("polls active report tasks until backend completion", async ({ page }) => {
  let completeReportTask: () => void;
  const completionReady = new Promise<void>((resolve) => {
    completeReportTask = resolve;
  });
  let reportTaskRequests = 0;
  const runningTask = {
    id: "report_running",
    workspaceId: "workspace_demo",
    topic: "报告轮询验证",
    status: "running",
    progress: 80,
    stage: "reporting",
    templateId: "auto",
    sourceScope: {
      orchestration: {
        enabled: true,
        engines: ["media", "insight"]
      }
    },
    artifacts: [
      {
        format: "html",
        ready: false,
        filename: "report.html",
        downloadUrl: "/api/v1/report-tasks/report_running/exports/html"
      }
    ],
    createdAt: timestamp,
    updatedAt: timestamp
  };
  const completedTask = {
    ...runningTask,
    status: "succeeded",
    progress: 100,
    stage: "completed",
    artifacts: runningTask.artifacts.map((artifact) => ({ ...artifact, ready: true })),
    updatedAt: "2026-05-22T10:05:00Z"
  };

  await routeJson(page, "/system/components", {
    success: true,
    components: [{ id: "report", name: "Report Engine", status: "running" }]
  });
  await routeJson(page, "/report-templates", { success: true, templates: [] });
  await page.route(`${apiBase}/report-tasks`, async (route) => {
    expect(route.request().method()).toBe("GET");
    reportTaskRequests += 1;
    if (reportTaskRequests > 1) {
      await completionReady;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        tasks: [reportTaskRequests === 1 ? runningTask : completedTask]
      })
    });
  });
  await routeJson(page, "/crawler-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-strategies", { success: true, strategies: [] });
  await routeJson(page, "/crawler-accounts", { success: true, accounts: [] });
  await routeJson(page, "/platforms", {
    success: true,
    platforms: [platform("wb", "微博", { allow: 0, block: 0 })]
  });
  await routeJson(page, "/system/config", { success: true, fields: [] });
  await routeJson(page, "/logs?tail=300", { success: true, lines: [] });
  await page.route(`${apiBase}/platforms/*/identity-lists`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, rules: [] })
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "报告" }).click();
  const reportRow = page.locator(".task-row", { hasText: "报告轮询验证" });
  await expect(reportRow).toContainText("running");

  completeReportTask!();
  await expect(reportRow).toContainText("succeeded");
  expect(reportTaskRequests).toBeGreaterThan(1);
});

test("points report downloads at the API origin with workspace fallback", async ({ page }) => {
  await routeJson(page, "/system/components", {
    success: true,
    components: [{ id: "report", name: "Report Engine", status: "running" }]
  });
  await routeJson(page, "/report-templates", {
    success: true,
    templates: [{ id: "daily-monitoring", name: "日报", filename: "daily.md", description: "", sizeBytes: 10 }]
  });
  await routeJson(page, "/report-tasks", {
    success: true,
    tasks: [
      {
        id: "report_ready",
        workspaceId: "workspace_demo",
        topic: "下载修复验证",
        status: "succeeded",
        progress: 100,
        stage: "completed",
        artifacts: [
          {
            format: "html",
            ready: true,
            filename: "report.html",
            downloadUrl: "/api/v1/report-tasks/report_ready/exports/html"
          },
          {
            format: "pdf",
            ready: true,
            filename: "report.pdf",
            downloadUrl: "/api/v1/report-tasks/report_ready/exports/pdf"
          }
        ],
        createdAt: timestamp,
        updatedAt: timestamp
      }
    ]
  });
  await routeJson(page, "/crawler-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-strategies", { success: true, strategies: [] });
  await routeJson(page, "/crawler-accounts", { success: true, accounts: [] });
  await routeJson(page, "/platforms", {
    success: true,
    platforms: [platform("wb", "微博", { allow: 0, block: 0 })]
  });
  await routeJson(page, "/system/config", { success: true, fields: [] });
  await routeJson(page, "/logs?tail=300", { success: true, lines: [] });
  await page.route(`${apiBase}/platforms/*/identity-lists`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, rules: [] })
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "报告" }).click();

  await expect(page.locator(".artifact-chip", { hasText: "html" })).toHaveAttribute(
    "href",
    `${apiBase}/report-tasks/report_ready/exports/html?workspaceId=workspace_demo`
  );
  await expect(page.locator(".artifact-chip", { hasText: "pdf" })).toHaveAttribute(
    "href",
    `${apiBase}/report-tasks/report_ready/exports/pdf?workspaceId=workspace_demo`
  );
});

test("queries crawler data platform filters pagination and empty keywords through API routes", async ({ page }) => {
  const crawlerDataRequests: URL[] = [];
  const crawlerRecords = [
    crawlerDataRecord({
      id: "xhs_note:xhs_001",
      platformId: "xhs",
      tableName: "xhs_note",
      sourceId: "xhs_001",
      title: "社区养老服务体验",
      textSnippet: "家附近新开的社区养老服务中心开始提供康复护理和日间照护。",
      author: "研究采集号",
      keyword: "养老服务",
      url: "https://www.xiaohongshu.com/explore/xhs_001",
      sentiment: "positive"
    }),
    crawlerDataRecord({
      id: "weibo_note:wb_002",
      platformId: "wb",
      tableName: "weibo_note",
      sourceId: "wb_002",
      title: "医保支付改革讨论",
      textSnippet: "多地试点长期护理保险与医保支付衔接。",
      author: "BettaFish 运营号",
      keyword: "医保支付",
      url: "https://weibo.com/detail/wb_002",
      sentiment: "neutral"
    }),
    crawlerDataRecord({
      id: "xhs_comment:xhsc_003",
      platformId: "xhs",
      contentType: "comment",
      tableName: "xhs_note_comment",
      sourceId: "xhsc_003",
      title: "未带关键词评论",
      textSnippet: "这条评论没有原始关键词字段。",
      author: "xhs_reader",
      sentiment: "unknown"
    }),
    ...Array.from({ length: 10 }, (_, index) =>
      crawlerDataRecord({
        id: `weibo_note:page_${index + 1}`,
        platformId: "wb",
        tableName: "weibo_note",
        sourceId: `page_${index + 1}`,
        title: `分页样本 ${index + 1}`,
        textSnippet: `分页和页大小回归样本 ${index + 1}`,
        author: "分页账号",
        keyword: `分页词${index + 1}`,
        sentiment: index % 2 === 0 ? "positive" : "neutral"
      })
    )
  ];

  await routeJson(page, "/system/components", {
    success: true,
    components: [{ id: "mindspider", name: "MindSpider", status: "running" }]
  });
  await routeJson(page, "/report-templates", { success: true, templates: [] });
  await routeJson(page, "/report-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-tasks", { success: true, tasks: [] });
  await routeJson(page, "/crawler-strategies", { success: true, strategies: [] });
  await routeJson(page, "/crawler-accounts", { success: true, accounts: [] });
  await routeJson(page, "/platforms", {
    success: true,
    platforms: [
      platform("wb", "微博", { allow: 0, block: 0 }),
      platform("xhs", "小红书", { allow: 0, block: 0 })
    ]
  });
  await routeJson(page, "/system/config", { success: true, fields: [] });
  await routeJson(page, "/logs?tail=300", { success: true, lines: [] });
  await page.route(`${apiBase}/platforms/*/identity-lists`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, rules: [] })
    });
  });
  await page.route(`${apiBase}/crawler-data*`, async (route) => {
    expect(route.request().method()).toBe("GET");
    const requestUrl = new URL(route.request().url());
    crawlerDataRequests.push(requestUrl);

    const platformFilter = requestUrl.searchParams.get("platform");
    const typeFilter = requestUrl.searchParams.get("contentType");
    const query = requestUrl.searchParams.get("q")?.trim().toLowerCase();
    const pageNumber = Number(requestUrl.searchParams.get("page") ?? 1);
    const pageSize = Number(requestUrl.searchParams.get("pageSize") ?? 20);
    const filteredRecords = crawlerRecords.filter((record) => {
      const platformMatches = !platformFilter || record.platformId === platformFilter;
      const typeMatches = !typeFilter || record.contentType === typeFilter;
      const haystack = `${record.title} ${record.textSnippet} ${record.author ?? ""} ${record.keyword ?? ""}`.toLowerCase();
      return platformMatches && typeMatches && (!query || haystack.includes(query));
    });

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(crawlerDataPage(filteredRecords, pageNumber, pageSize))
    });
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "爬取数据" }).click();

  const tablist = page.getByRole("tablist", { name: "爬取数据平台筛选" });
  await expect(tablist.getByRole("tab", { name: "全部平台" })).toBeVisible();
  await expect(tablist.getByRole("tab", { name: "微博" })).toBeVisible();
  await expect(tablist.getByRole("tab", { name: "小红书" })).toBeVisible();
  await expect(page.locator(".crawler-data-row", { hasText: "社区养老服务体验" })).toBeVisible();
  await expect(page.locator(".crawler-data-row", { hasText: "医保支付改革讨论" })).toBeVisible();

  await tablist.getByRole("tab", { name: "小红书" }).click();
  await expect(tablist.getByRole("tab", { name: "小红书" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".crawler-data-row", { hasText: "社区养老服务体验" })).toBeVisible();
  await expect(page.locator(".crawler-data-row", { hasText: "医保支付改革讨论" })).toHaveCount(0);

  await page.getByLabel("类型").selectOption("comment");
  await page.getByRole("button", { name: "检索" }).click();
  const emptyKeywordRow = page.locator(".crawler-data-row", { hasText: "未带关键词评论" });
  await expect(emptyKeywordRow).toBeVisible();
  await expect(emptyKeywordRow.locator(".crawler-data-keyword-marker")).toHaveCount(0);

  await tablist.getByRole("tab", { name: "全部平台" }).click();
  await page.getByLabel("类型").selectOption("all");
  await page.getByPlaceholder("标题、正文、作者、关键词").fill("医保");
  await page.getByRole("button", { name: "检索" }).click();
  await expect(page.locator(".crawler-data-row", { hasText: "医保支付改革讨论" })).toBeVisible();
  await expect(page.locator(".crawler-data-row", { hasText: "社区养老服务体验" })).toHaveCount(0);

  await page.getByPlaceholder("标题、正文、作者、关键词").fill("");
  await page.getByRole("button", { name: "检索" }).click();
  await page.getByLabel("爬取数据每页条数").selectOption("10");
  await expect(page.getByText("1 / 2")).toBeVisible();
  await expect(page.locator(".crawler-data-row")).toHaveCount(10);
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("2 / 2")).toBeVisible();
  await expect(page.locator(".crawler-data-row")).toHaveCount(3);
  await page.getByRole("button", { name: "上一页" }).click();
  await expect(page.getByText("1 / 2")).toBeVisible();

  expect(crawlerDataRequests.some((url) => url.searchParams.get("platform") === "xhs")).toBe(true);
  expect(crawlerDataRequests.some((url) => url.searchParams.get("contentType") === "comment")).toBe(true);
  expect(crawlerDataRequests.some((url) => url.searchParams.get("q") === "医保")).toBe(true);
  expect(
    crawlerDataRequests.some(
      (url) => url.searchParams.get("page") === "2" && url.searchParams.get("pageSize") === "10"
    )
  ).toBe(true);
});

async function routeJson(page: import("@playwright/test").Page, path: string, body: unknown) {
  await page.route(`${apiBase}${path}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body)
    });
  });
}

type TestPlatformId = "wb" | "xhs";

function platform(
  id: TestPlatformId,
  name: string,
  identityRuleCounts: { allow: number; block: number }
) {
  return {
    id,
    name,
    enabled: true,
    crawlerType: "search",
    identityRuleCounts,
    policy: {
      platformId: id,
      enabled: true,
      crawlDepth: 3,
      maxKeywords: 100,
      maxNotesPerKeyword: 50,
      maxCommentsPerNote: 100,
      keywords: [],
      keywordSource: "manual",
      frequency: { mode: "manual", timezone: "Asia/Shanghai" },
      loginType: "qrcode",
      headless: true,
      updatedAt: timestamp
    }
  };
}

type CrawlerDataFixture = {
  id: string;
  platformId: TestPlatformId;
  contentType: "content" | "comment";
  tableName: string;
  sourceId: string;
  title: string;
  textSnippet: string;
  author?: string;
  keyword?: string;
  url?: string;
  createdAt: string;
  scrapedAt: string;
  sentiment: "positive" | "neutral" | "negative" | "unknown";
  metrics: {
    likes: number;
    comments: number;
  };
};

function crawlerDataRecord(
  overrides: Partial<CrawlerDataFixture> & Pick<CrawlerDataFixture, "id" | "platformId" | "title" | "textSnippet">
): CrawlerDataFixture {
  return {
    contentType: "content",
    tableName: `${overrides.platformId}_note`,
    sourceId: overrides.id,
    author: "测试账号",
    createdAt: timestamp,
    scrapedAt: timestamp,
    sentiment: "neutral",
    metrics: {
      likes: 10,
      comments: 1
    },
    ...overrides
  };
}

function crawlerDataPage(records: CrawlerDataFixture[], page: number, pageSize: number) {
  const totalPages = records.length === 0 ? 0 : Math.ceil(records.length / pageSize);
  const pageRecords = records.slice((page - 1) * pageSize, page * pageSize);

  return {
    success: true,
    records: pageRecords,
    summary: {
      totalRecords: records.length,
      byPlatform: records.reduce<Partial<Record<TestPlatformId, number>>>((acc, record) => {
        acc[record.platformId] = (acc[record.platformId] ?? 0) + 1;
        return acc;
      }, {}),
      byType: records.reduce<Partial<Record<"content" | "comment", number>>>((acc, record) => {
        acc[record.contentType] = (acc[record.contentType] ?? 0) + 1;
        return acc;
      }, {})
    },
    pageInfo: {
      page,
      pageSize,
      totalRecords: records.length,
      totalPages,
      hasPreviousPage: page > 1 && totalPages > 0,
      hasNextPage: totalPages > 0 && page < totalPages
    }
  };
}
