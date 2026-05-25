"use client";

import {
  configFields,
  crawlerAccounts,
  crawlerDataRecords,
  crawlerTasks,
  getMockSnapshot,
  identityRules,
  logs,
  platforms,
  reportTasks,
  WORKSPACE_ID
} from "./mock-data";
import type {
  ConfigField,
  ConsoleSnapshot,
  CreateCrawlerAccountLoginSessionInput,
  CreateCrawlerTaskInput,
  CreateReportTaskInput,
  CrawlerAccountLoginSession,
  CrawlerDataRecord,
  CrawlerDataPage,
  CrawlerTask,
  IdentityRule,
  IdentityRuleInput,
  PlatformId,
  PlatformPolicy,
  RerunReportTaskInput,
  ReportTask,
  SystemComponent,
  TaskLogEvent,
  TaskLogPage,
  TaskLogType
} from "./types";

export const OPENAPI_PATHS = {
  health: "/health",
  components: "/system/components",
  componentStart: (id: string) => `/system/components/${id}:start`,
  componentStop: (id: string) => `/system/components/${id}:stop`,
  systemConfig: "/system/config",
  logs: "/logs",
  reportTemplates: "/report-templates",
  reportTasks: "/report-tasks",
  reportTask: (id: string) => `/report-tasks/${id}`,
  reportTaskDelete: (id: string) => `/report-tasks/${id}`,
  reportTaskCancel: (id: string) => `/report-tasks/${id}:cancel`,
  reportTaskRerun: (id: string) => `/report-tasks/${id}:rerun`,
  reportTaskEvents: (id: string) => `/report-tasks/${id}/events`,
  reportTaskLogs: (id: string) => `/report-tasks/${id}/logs`,
  crawlerStrategies: "/crawler-strategies",
  crawlerAccounts: "/crawler-accounts",
  crawlerAccountLoginSessions: "/crawler-accounts/login-sessions",
  crawlerAccountLoginSession: (id: string) => `/crawler-accounts/login-sessions/${id}`,
  crawlerData: "/crawler-data",
  crawlerTasks: "/crawler-tasks",
  crawlerTask: (id: string) => `/crawler-tasks/${id}`,
  crawlerTaskDelete: (id: string) => `/crawler-tasks/${id}`,
  crawlerTaskLogs: (id: string) => `/crawler-tasks/${id}/logs`,
  crawlerTaskStop: (id: string) => `/crawler-tasks/${id}:stop`,
  crawlerTaskRetry: (id: string) => `/crawler-tasks/${id}:retry`,
  platforms: "/platforms",
  platformPolicy: (id: string) => `/platforms/${id}/policy`,
  platformIdentityRules: (id: string) => `/platforms/${id}/identity-lists`,
  platformIdentityRule: (platformId: string, ruleId: string) =>
    `/platforms/${platformId}/identity-lists/${ruleId}`
} as const;

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";
const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS === "true";
const configuredWorkspaceId = process.env.NEXT_PUBLIC_WORKSPACE_ID?.trim();

function resolveWorkspaceId(): string {
  if (configuredWorkspaceId) return configuredWorkspaceId;
  return WORKSPACE_ID;
}

const workspaceId = resolveWorkspaceId();

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (USE_MOCKS || !API_BASE_URL) {
    throw new Error(USE_MOCKS ? "Mock mode is active" : "API base URL is not configured");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Workspace-Id": workspaceId,
      ...(init.headers ?? {})
    }
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const message =
      body?.error?.message ?? body?.message ?? `Request failed with HTTP ${response.status}`;
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

function taskTimestamp(): string {
  return new Date().toISOString();
}

function nextId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}`;
}

function addLog(source: "system" | "report" | "crawler", message: string, taskId?: string): void {
  logs.unshift({
    id: nextId("log"),
    source,
    level: "info",
    timestamp: taskTimestamp(),
    message,
    taskId
  });
}

function appendWorkspaceParam(url: string): string {
  try {
    const parsed = new URL(url, "http://bettafish.local");
    if (!parsed.searchParams.has("workspaceId")) {
      parsed.searchParams.set("workspaceId", workspaceId);
    }
    if (/^https?:\/\//i.test(url)) return parsed.toString();
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}workspaceId=${encodeURIComponent(workspaceId)}`;
  }
}

function reportDownloadUrl(downloadUrl?: string): string | undefined {
  if (!downloadUrl) return undefined;

  if (!API_BASE_URL) return appendWorkspaceParam(downloadUrl);

  try {
    const apiBase = new URL(API_BASE_URL, "http://bettafish.local");
    const apiBasePath = apiBase.pathname.replace(/\/$/, "");
    const apiOrigin = /^https?:\/\//i.test(API_BASE_URL) ? apiBase.origin : "";
    if (/^https?:\/\//i.test(downloadUrl)) {
      return appendWorkspaceParam(downloadUrl);
    }
    if (downloadUrl === apiBasePath || downloadUrl.startsWith(`${apiBasePath}/`)) {
      return appendWorkspaceParam(`${apiOrigin}${downloadUrl}`);
    }
    if (downloadUrl.startsWith("/")) {
      return appendWorkspaceParam(`${API_BASE_URL}${downloadUrl}`);
    }
    return appendWorkspaceParam(`${API_BASE_URL}/${downloadUrl}`);
  } catch {
    return appendWorkspaceParam(downloadUrl);
  }
}

function normalizeReportTask(task: ReportTask): ReportTask {
  return {
    ...task,
    artifacts: task.artifacts.map((artifact) => ({
      ...artifact,
      downloadUrl: reportDownloadUrl(artifact.downloadUrl)
    }))
  };
}

export async function loadConsoleSnapshot(): Promise<ConsoleSnapshot> {
  if (USE_MOCKS) {
    return getMockSnapshot();
  }

  const [
    components,
    templates,
    reportTaskPage,
    crawlerTaskPage,
    strategies,
    accountPage,
    platformPage,
    config,
    logPage
  ] = await Promise.all([
    requestJson<{ components: SystemComponent[] }>(OPENAPI_PATHS.components),
    requestJson<{ templates: ConsoleSnapshot["reportTemplates"] }>(OPENAPI_PATHS.reportTemplates),
    requestJson<{ tasks: ReportTask[] }>(OPENAPI_PATHS.reportTasks),
    requestJson<{ tasks: CrawlerTask[] }>(OPENAPI_PATHS.crawlerTasks),
    requestJson<{ strategies: ConsoleSnapshot["crawlerStrategies"] }>(OPENAPI_PATHS.crawlerStrategies),
    requestJson<{ accounts: ConsoleSnapshot["crawlerAccounts"] }>(OPENAPI_PATHS.crawlerAccounts),
    requestJson<{ platforms: ConsoleSnapshot["platforms"] }>(OPENAPI_PATHS.platforms),
    requestJson<{ fields: ConfigField[] }>(OPENAPI_PATHS.systemConfig),
    requestJson<{ lines: ConsoleSnapshot["logs"] }>(`${OPENAPI_PATHS.logs}?tail=300`)
  ]);
  const identityRulePages = await Promise.all(
    platformPage.platforms.map((platform) =>
      requestJson<{ rules: IdentityRule[] }>(OPENAPI_PATHS.platformIdentityRules(platform.id))
    )
  );

  return {
    workspaceId,
    generatedAt: taskTimestamp(),
    mock: false,
    components: components.components,
    reportTemplates: templates.templates,
    reportTasks: reportTaskPage.tasks.map(normalizeReportTask),
    crawlerTasks: crawlerTaskPage.tasks,
    crawlerStrategies: strategies.strategies,
    crawlerAccounts: accountPage.accounts,
    platforms: platformPage.platforms,
    identityRules: identityRulePages.flatMap((page) => page.rules),
    configFields: config.fields,
    logs: logPage.lines
  };
}

export async function createReportTask(input: CreateReportTaskInput): Promise<ReportTask> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ task: ReportTask }>(OPENAPI_PATHS.reportTasks, {
      method: "POST",
      body: JSON.stringify(input)
    });
    return normalizeReportTask(response.task);
  }

  const timestamp = taskTimestamp();
  const task: ReportTask = {
    id: nextId("report"),
    workspaceId,
    topic: input.topic,
    status: "queued",
    progress: 0,
    stage: "queued",
    templateId: input.templateId,
    sourceScope: input.sourceScope,
    artifacts: input.outputFormats.map((format) => ({
      format,
      ready: false
    })),
    owner: input.owner,
    createdAt: timestamp,
    updatedAt: timestamp
  };
  reportTasks.unshift(task);
  addLog("report", `Report task ${task.id} queued`, task.id);
  return task;
}

export async function cancelReportTask(taskId: string): Promise<ReportTask> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ task: ReportTask }>(OPENAPI_PATHS.reportTaskCancel(taskId), {
      method: "POST"
    });
    return normalizeReportTask(response.task);
  }

  const task = reportTasks.find((item) => item.id === taskId);
  if (!task) throw new Error("Task not found");
  task.status = "cancelled";
  task.progress = Math.max(task.progress, 0);
  task.updatedAt = taskTimestamp();
  addLog("report", `Report task ${task.id} cancelled`, task.id);
  return task;
}

export async function rerunReportTask(taskId: string, input: RerunReportTaskInput): Promise<ReportTask> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ task: ReportTask }>(OPENAPI_PATHS.reportTaskRerun(taskId), {
      method: "POST",
      body: JSON.stringify(input)
    });
    return normalizeReportTask(response.task);
  }

  const task = reportTasks.find((item) => item.id === taskId);
  if (!task) throw new Error("Task not found");
  task.status = "queued";
  task.progress = 0;
  task.stage = "queued";
  task.sourceScope = {
    ...(task.sourceScope ?? {}),
    orchestration: {
      enabled: true,
      engines: input.engines
    }
  };
  task.artifacts = task.artifacts.map((artifact) => ({ ...artifact, ready: false }));
  task.updatedAt = taskTimestamp();
  addLog("report", `Report task ${task.id} rerun queued`, task.id);
  return task;
}

export async function deleteReportTask(taskId: string): Promise<void> {
  if (!USE_MOCKS) {
    await requestJson<void>(OPENAPI_PATHS.reportTaskDelete(taskId), {
      method: "DELETE"
    });
    return;
  }

  const index = reportTasks.findIndex((item) => item.id === taskId);
  if (index >= 0) reportTasks.splice(index, 1);
  addLog("report", `Report task ${taskId} deleted`, taskId);
}

export async function createCrawlerTask(input: CreateCrawlerTaskInput): Promise<CrawlerTask> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ task: CrawlerTask }>(OPENAPI_PATHS.crawlerTasks, {
      method: "POST",
      body: JSON.stringify(input)
    });
    return response.task;
  }

  const timestamp = taskTimestamp();
  const schedule = input.schedule ?? { mode: "manual" as const, timezone: "Asia/Shanghai" };
  const startDate = input.startDate ?? input.targetDate;
  const endDate = input.endDate ?? input.targetDate;
  const task: CrawlerTask = {
    id: nextId("crawler"),
    workspaceId,
    runMode: input.runMode,
    status: schedule.mode === "manual" ? "queued" : "pending",
    progress: 0,
    strategyId: input.strategyId,
    targetDate: input.targetDate ?? startDate,
    startDate,
    endDate,
    schedule,
    platforms: input.platforms,
    keywords: input.keywords,
    keywordSource: input.keywordSource,
    stats: {
      totalKeywords: input.keywords.length,
      totalPlatforms: input.platforms.length,
      totalTasks: input.keywords.length * input.platforms.length,
      successfulTasks: 0,
      failedTasks: 0,
      totalNotes: 0,
      totalComments: 0
    },
    owner: input.owner,
    createdAt: timestamp,
    updatedAt: timestamp
  };
  crawlerTasks.unshift(task);
  addLog("crawler", `Crawler task ${task.id} queued`, task.id);
  return task;
}

export async function deleteCrawlerTask(taskId: string): Promise<void> {
  if (!USE_MOCKS) {
    await requestJson<void>(OPENAPI_PATHS.crawlerTaskDelete(taskId), {
      method: "DELETE"
    });
    return;
  }

  const index = crawlerTasks.findIndex((item) => item.id === taskId);
  if (index >= 0) crawlerTasks.splice(index, 1);
  addLog("crawler", `Crawler task ${taskId} deleted`, taskId);
}

export async function stopCrawlerTask(taskId: string): Promise<CrawlerTask> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ task: CrawlerTask }>(OPENAPI_PATHS.crawlerTaskStop(taskId), {
      method: "POST"
    });
    return response.task;
  }

  const task = crawlerTasks.find((item) => item.id === taskId);
  if (!task) throw new Error("Task not found");
  task.status = "stopping";
  task.updatedAt = taskTimestamp();
  addLog("crawler", `Crawler task ${task.id} stopping`, task.id);
  return task;
}

export async function listTaskLogs(taskType: TaskLogType, taskId: string): Promise<TaskLogPage> {
  if (!USE_MOCKS) {
    const path =
      taskType === "report" ? OPENAPI_PATHS.reportTaskLogs(taskId) : OPENAPI_PATHS.crawlerTaskLogs(taskId);
    const response = await requestJson<TaskLogPage & { success: boolean }>(path);
    return {
      taskId: response.taskId,
      taskType: response.taskType,
      events: response.events
    };
  }

  const task =
    taskType === "report"
      ? reportTasks.find((item) => item.id === taskId)
      : crawlerTasks.find((item) => item.id === taskId);
  if (!task) throw new Error("Task not found");

  const taskLines = logs.filter((line) => line.taskId === taskId);
  const events: TaskLogEvent[] = taskLines.map((line) => ({
    id: line.id,
    type: line.level === "error" ? "failed" : "status",
    taskId,
    timestamp: line.timestamp,
    payload: {
      source: line.source,
      level: line.level,
      message: line.message
    } as Record<string, unknown>
  }));
  if (events.length === 0) {
    events.push({
      id: `mock_${taskId}`,
      type: "status",
      taskId,
      timestamp: task.updatedAt,
      payload: {
        task,
        message: `${taskType} task ${task.status}`
      }
    });
  }
  return { taskId, taskType, events };
}

export async function createCrawlerAccountLoginSession(
  input: CreateCrawlerAccountLoginSessionInput
): Promise<CrawlerAccountLoginSession> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ session: CrawlerAccountLoginSession }>(
      OPENAPI_PATHS.crawlerAccountLoginSessions,
      {
        method: "POST",
        body: JSON.stringify(input)
      }
    );
    return response.session;
  }

  const timestamp = taskTimestamp();
  const accountId = `${input.platformId}_${Date.now().toString(36)}`;
  const account = {
    id: nextId("account"),
    workspaceId,
    platformId: input.platformId,
    accountId,
    status: "active" as const,
    displayName: `${input.platformId.toUpperCase()} 采集号`,
    loginType: input.loginType,
    lastLoginAt: timestamp,
    lastCheckedAt: timestamp,
    createdAt: timestamp,
    updatedAt: timestamp,
    details: {
      scopes: ["search", "detail"],
      message: "登录状态已保存，可用于后续采集。",
      stateNames: ["session", "profile"],
      stateCount: 2
    }
  };
  const existingIndex = crawlerAccounts.findIndex(
    (item) => item.platformId === input.platformId && item.accountId === accountId
  );
  if (existingIndex >= 0) {
    crawlerAccounts[existingIndex] = account;
  } else {
    crawlerAccounts.unshift(account);
  }
  addLog("crawler", `Crawler account login completed for ${input.platformId}`);
  return {
    id: nextId("login"),
    workspaceId,
    platformId: input.platformId,
    loginType: input.loginType,
    status: "completed",
    loginUrl: "#",
    message: "登录状态已保存",
    account,
    createdAt: timestamp,
    updatedAt: timestamp
  };
}

export async function getCrawlerAccountLoginSession(sessionId: string): Promise<CrawlerAccountLoginSession> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ session: CrawlerAccountLoginSession }>(
      OPENAPI_PATHS.crawlerAccountLoginSession(sessionId)
    );
    return response.session;
  }
  throw new Error("Mock login sessions complete immediately");
}

export async function listCrawlerData(params: {
  platform?: PlatformId | "all";
  contentType?: "content" | "comment" | "all";
  q?: string;
  pageSize?: number;
} = {}): Promise<CrawlerDataPage> {
  if (!USE_MOCKS) {
    const query = new URLSearchParams();
    if (params.platform && params.platform !== "all") query.set("platform", params.platform);
    if (params.contentType && params.contentType !== "all") query.set("contentType", params.contentType);
    if (params.q) query.set("q", params.q);
    if (params.pageSize) query.set("pageSize", String(params.pageSize));
    const path = `${OPENAPI_PATHS.crawlerData}${query.toString() ? `?${query.toString()}` : ""}`;
    const response = await requestJson<CrawlerDataPage & { success: boolean }>(path);
    return {
      records: response.records,
      summary: response.summary,
      source: response.source,
      message: response.message
    };
  }

  const needle = params.q?.trim().toLowerCase();
  const records = crawlerDataRecords.filter((record) => {
    const platformMatches = !params.platform || params.platform === "all" || record.platformId === params.platform;
    const typeMatches =
      !params.contentType || params.contentType === "all" || record.contentType === params.contentType;
    const text = `${record.title} ${record.textSnippet} ${record.author ?? ""} ${record.keyword ?? ""}`.toLowerCase();
    const queryMatches = !needle || text.includes(needle);
    return platformMatches && typeMatches && queryMatches;
  });
  return {
    records,
    summary: {
      totalRecords: records.length,
      byPlatform: records.reduce<Record<string, number>>((acc, record) => {
        acc[record.platformId] = (acc[record.platformId] ?? 0) + 1;
        return acc;
      }, {}),
      byType: records.reduce<Record<string, number>>((acc, record) => {
        acc[record.contentType] = (acc[record.contentType] ?? 0) + 1;
        return acc;
      }, {})
    }
  };
}

export async function deleteCrawlerDataRecord(
  record: Pick<CrawlerDataRecord, "tableName" | "sourceId" | "platformId" | "contentType">
): Promise<void> {
  if (!USE_MOCKS) {
    const query = new URLSearchParams({
      tableName: record.tableName,
      sourceId: record.sourceId,
      platform: record.platformId,
      contentType: record.contentType
    });
    const response = await requestJson<{ success: boolean; deleted: number }>(
      `${OPENAPI_PATHS.crawlerData}?${query.toString()}`,
      {
        method: "DELETE"
      }
    );
    if (!response.success || response.deleted < 1) {
      throw new Error("Crawler data record not deleted");
    }
    return;
  }

  const index = crawlerDataRecords.findIndex(
    (item) => item.tableName === record.tableName && item.sourceId === record.sourceId
  );
  if (index < 0) throw new Error("Crawler data record not found");
  crawlerDataRecords.splice(index, 1);
  addLog("crawler", `Crawler data ${record.tableName}:${record.sourceId} deleted`);
}

export async function updatePlatformPolicy(
  platformId: PlatformId,
  policy: PlatformPolicy
): Promise<PlatformPolicy> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ policy: PlatformPolicy }>(OPENAPI_PATHS.platformPolicy(platformId), {
      method: "PUT",
      body: JSON.stringify(policy)
    });
    return response.policy;
  }

  const platform = platforms.find((item) => item.id === platformId);
  if (!platform) throw new Error("Platform not found");
  const updated = {
    ...policy,
    platformId,
    updatedAt: taskTimestamp()
  };
  platform.policy = updated;
  platform.enabled = updated.enabled;
  addLog("system", `Platform policy updated for ${platformId}`);
  return updated;
}

export async function createIdentityRule(input: IdentityRuleInput): Promise<IdentityRule> {
  if (!USE_MOCKS) {
    const response = await requestJson<{ rule: IdentityRule }>(
      OPENAPI_PATHS.platformIdentityRules(input.platformId),
      {
        method: "POST",
        body: JSON.stringify(input)
      }
    );
    return response.rule;
  }

  const rule: IdentityRule = {
    id: nextId("rule"),
    platformId: input.platformId,
    listType: input.listType,
    userId: input.userId,
    label: input.label,
    reason: input.reason,
    createdAt: taskTimestamp(),
    createdBy: input.createdBy
  };
  identityRules.unshift(rule);
  const platform = platforms.find((item) => item.id === input.platformId);
  if (platform) {
    platform.identityRuleCounts[input.listType] += 1;
  }
  addLog("system", `${input.listType} rule added for ${input.platformId}`);
  return rule;
}

export async function deleteIdentityRule(platformId: PlatformId, ruleId: string): Promise<void> {
  if (!USE_MOCKS) {
    await requestJson<void>(OPENAPI_PATHS.platformIdentityRule(platformId, ruleId), {
      method: "DELETE"
    });
    return;
  }

  const index = identityRules.findIndex((item) => item.id === ruleId);
  if (index >= 0) {
    const [removed] = identityRules.splice(index, 1);
    const platform = platforms.find((item) => item.id === platformId);
    if (platform) {
      platform.identityRuleCounts[removed.listType] = Math.max(
        0,
        platform.identityRuleCounts[removed.listType] - 1
      );
    }
  }
  addLog("system", `Identity rule ${ruleId} deleted`);
}

export async function updateSystemConfig(values: Record<string, string>): Promise<ConfigField[]> {
  const cleanedEntries = Object.entries(values).filter(([, value]) => value !== "");

  if (!USE_MOCKS) {
    const response = await requestJson<{ fields: ConfigField[] }>(OPENAPI_PATHS.systemConfig, {
      method: "PATCH",
      body: JSON.stringify({
        values: Object.fromEntries(cleanedEntries)
      })
    });
    return response.fields;
  }

  for (const [key, value] of cleanedEntries) {
    const field = configFields.find((item) => item.key === key);
    if (field && !field.sensitive) {
      field.value = value;
    }
  }
  addLog("system", "System config updated");
  return configFields.map((field) => ({ ...field }));
}
