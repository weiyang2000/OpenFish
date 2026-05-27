"use client";

import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bot,
  CheckCircle2,
  CircleStop,
  ClipboardList,
  Database,
  Download,
  ExternalLink,
  FilePlus2,
  Filter,
  Gauge,
  LayoutDashboard,
  ListChecks,
  Loader2,
  LockKeyhole,
  Play,
  Plus,
  RefreshCcw,
  Save,
  Search,
  Settings,
  Shield,
  SlidersHorizontal,
  Square,
  StopCircle,
  TerminalSquare,
  Trash2,
  XCircle
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  cancelReportTask,
  createCrawlerAccountLoginSession,
  createCrawlerTask,
  createIdentityRule,
  createReportTask,
  deleteCrawlerAccount,
  deleteCrawlerTask,
  deleteCrawlerDataRecord,
  deleteCrawlerDataRecords,
  deleteIdentityRule,
  deleteReportTask,
  getCrawlerAccountLoginSession,
  listCrawlerData,
  listCrawlerTasks,
  listReportTasks,
  listTaskLogs,
  loadConsoleSnapshot,
  rerunReportTask,
  stopCrawlerTask,
  updateSystemConfig
} from "@/lib/openapi-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type {
  ComponentStatus,
  ConfigField,
  ConsoleSnapshot,
  CrawlFrequency,
  CrawlerAccount,
  CrawlerAccountLoginSession,
  CrawlerAccountStatus,
  CrawlerDataRecord,
  CrawlerDataPage,
  CrawlerSentiment,
  CrawlerTask,
  IdentityListType,
  IdentityRule,
  Platform,
  PlatformId,
  PlatformPolicy,
  ReportEngineId,
  ReportFormat,
  ReportInsightMode,
  ReportTemplate,
  ReportTask,
  TaskLogPage,
  TaskLogType,
  TaskStatus
} from "@/lib/types";

type Section = "dashboard" | "reports" | "crawlers" | "crawlerData" | "platforms" | "config" | "logs";

const currentUser = {
  userId: "user_demo",
  displayName: "Demo Operator",
  role: "operator" as const
};

const AUTO_REPORT_TEMPLATE_ID = "auto";
const autoReportTemplate: ReportTemplate = {
  id: AUTO_REPORT_TEMPLATE_ID,
  name: "自动选择",
  filename: "",
  description: "根据报告主题和输入材料自动选择最合适的报告模板。",
  sizeBytes: 0
};

const navItems: Array<{ id: Section; label: string; icon: React.ComponentType<{ size?: number }> }> = [
  { id: "dashboard", label: "总览", icon: LayoutDashboard },
  { id: "reports", label: "报告", icon: ClipboardList },
  { id: "crawlers", label: "爬虫", icon: Bot },
  { id: "crawlerData", label: "爬取数据", icon: Database },
  { id: "platforms", label: "平台规则", icon: Shield },
  { id: "config", label: "系统配置", icon: Settings },
  { id: "logs", label: "运行日志", icon: TerminalSquare }
];

const platformNames: Record<PlatformId, string> = {
  wb: "微博",
  xhs: "小红书",
  zhihu: "知乎",
  dy: "抖音",
  bili: "Bilibili",
  tieba: "贴吧",
  ks: "快手"
};

const reportEngineLabels: Record<ReportEngineId, string> = {
  query: "Query Engine",
  media: "Media Engine",
  insight: "Insight Engine"
};

const reportEngineOptions = Object.entries(reportEngineLabels) as Array<[ReportEngineId, string]>;

const reportInsightModeLabels: Record<ReportInsightMode, string> = {
  fast: "Fast",
  normal: "Normal",
  deep: "Deep"
};

const reportInsightModeOptions = Object.entries(reportInsightModeLabels) as Array<[ReportInsightMode, string]>;

const loginTypeLabels: Record<PlatformPolicy["loginType"], string> = {
  qrcode: "扫码",
  phone: "手机号",
  cookie: "Cookie"
};

const scheduleLabels: Record<CrawlFrequency["mode"], string> = {
  manual: "手动执行",
  hourly: "每小时",
  daily: "每日",
  weekly: "每周",
  cron: "Cron"
};

const sentimentLabels: Record<CrawlerSentiment, string> = {
  positive: "正向",
  neutral: "中性",
  negative: "负向",
  unknown: "未知"
};

const crawlerDataTypeLabels: Record<CrawlerDataRecord["contentType"], string> = {
  content: "内容",
  comment: "评论"
};

const activeTaskStatuses: ReadonlySet<TaskStatus> = new Set([
  "queued",
  "pending",
  "running",
  "stopping"
]);

function classNames(...values: Array<string | false | undefined>) {
  return values.filter(Boolean).join(" ");
}

function statusTone(status: TaskStatus | ComponentStatus | CrawlerAccountStatus): "good" | "warn" | "bad" | "idle" {
  if (status === "running" || status === "succeeded" || status === "active") return "good";
  if (
    status === "queued" ||
    status === "pending" ||
    status === "starting" ||
    status === "degraded" ||
    status === "login_required" ||
    status === "unknown"
  ) {
    return "warn";
  }
  if (status === "failed" || status === "expired" || status === "disabled" || status === "error") return "bad";
  return "idle";
}

function StatusBadge({ value }: { value: TaskStatus | ComponentStatus | CrawlerAccountStatus }) {
  return (
    <Badge className="status-badge" variant={statusTone(value)}>
      {value}
    </Badge>
  );
}

function SentimentBadge({ value = "unknown" }: { value?: CrawlerSentiment }) {
  const variant = value === "positive" ? "good" : value === "negative" ? "bad" : value === "unknown" ? "warn" : "idle";
  return (
    <Badge className={classNames("sentiment-badge", `sentiment-${value}`)} variant={variant}>
      情绪：{sentimentLabels[value]}
    </Badge>
  );
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatCrawlerDateRange(task: CrawlerTask) {
  const start = task.startDate ?? task.targetDate;
  const end = task.endDate ?? task.targetDate;
  if (!start && !end) return "未设日期";
  if (!end || start === end) return start ?? end;
  return `${start} 至 ${end}`;
}

function formatCrawlerSchedule(schedule?: CrawlFrequency) {
  if (!schedule) return scheduleLabels.manual;
  if (schedule.mode === "cron") return schedule.cron ? `Cron ${schedule.cron}` : scheduleLabels.cron;
  return scheduleLabels[schedule.mode];
}

function crawlerDataRecordKey(
  record: Pick<CrawlerDataRecord, "platformId" | "contentType" | "tableName" | "sourceId">
) {
  return `${record.platformId}:${record.contentType}:${record.tableName}:${record.sourceId}`;
}

function summarizeCrawlerData(records: CrawlerDataRecord[]): CrawlerDataPage["summary"] {
  return {
    totalRecords: records.length,
    byPlatform: records.reduce<Partial<Record<PlatformId, number>>>((acc, record) => {
      acc[record.platformId] = (acc[record.platformId] ?? 0) + 1;
      return acc;
    }, {}),
    byType: records.reduce<Partial<Record<"content" | "comment", number>>>(
      (acc, record) => {
        acc[record.contentType] = (acc[record.contentType] ?? 0) + 1;
        return acc;
      },
      { content: 0, comment: 0 }
    )
  };
}

function removeCrawlerDataRecordFromPage(
  page: CrawlerDataPage,
  record: Pick<CrawlerDataRecord, "platformId" | "contentType" | "tableName" | "sourceId">
): CrawlerDataPage {
  return removeCrawlerDataRecordsFromPage(page, [record]);
}

function removeCrawlerDataRecordsFromPage(
  page: CrawlerDataPage,
  deletedRecords: Array<Pick<CrawlerDataRecord, "platformId" | "contentType" | "tableName" | "sourceId">>
): CrawlerDataPage {
  const deletedKeys = new Set(deletedRecords.map(crawlerDataRecordKey));
  const records = page.records.filter((item) => !deletedKeys.has(crawlerDataRecordKey(item)));
  const totalRecords = Math.max(0, page.pageInfo.totalRecords - deletedKeys.size);
  const totalPages = totalRecords === 0 ? 0 : Math.ceil(totalRecords / page.pageInfo.pageSize);
  return {
    ...page,
    records,
    summary: summarizeCrawlerData(records),
    pageInfo: {
      ...page.pageInfo,
      totalRecords,
      totalPages,
      hasPreviousPage: page.pageInfo.page > 1 && totalPages > 0,
      hasNextPage: totalPages > 0 && page.pageInfo.page < totalPages
    }
  };
}

function selectedReportEngines(task: ReportTask): ReportEngineId[] {
  const engines = task.sourceScope?.orchestration?.engines;
  if (Array.isArray(engines)) return engines.filter((engine) => engine in reportEngineLabels);
  if (engines && typeof engines === "object") {
    return (Object.keys(engines) as ReportEngineId[]).filter((engine) => engine in reportEngineLabels);
  }
  return reportEngineOptions.map(([engine]) => engine);
}

function formatReportEngines(task: ReportTask) {
  return selectedReportEngines(task).map((engine) => reportEngineLabels[engine]).join(" / ");
}

function getReportInsightMode(task: ReportTask): ReportInsightMode {
  const mode = task.sourceScope?.orchestration?.insightMode;
  return mode && mode in reportInsightModeLabels ? mode : "normal";
}

function formatReportInsightMode(task: ReportTask) {
  if (!selectedReportEngines(task).includes("insight")) return "Insight Off";
  return `Insight ${reportInsightModeLabels[getReportInsightMode(task)]}`;
}

function formatPayload(payload: Record<string, unknown>) {
  return JSON.stringify(payload, null, 2);
}

function taskLogStatus(eventType: string): TaskStatus {
  if (eventType === "completed") return "succeeded";
  if (eventType === "failed") return "failed";
  if (eventType === "cancelled") return "cancelled";
  if (eventType === "stopped") return "stopped";
  return "running";
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="progress-track" aria-label={`progress ${value}%`}>
      <span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

function SectionHeader({
  title,
  subtitle,
  action
}: {
  title: string;
  subtitle: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="section-header">
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
      {action}
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <ListChecks size={28} />
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function MetricTile({
  label,
  value,
  detail,
  icon: Icon
}: {
  label: string;
  value: string;
  detail: string;
  icon: React.ComponentType<{ size?: number }>;
}) {
  return (
    <Card className="metric-tile">
      <CardContent className="metric-content">
        <div className="metric-icon">
          <Icon size={20} />
        </div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </CardContent>
    </Card>
  );
}

function replaceReportTask(tasks: ReportTask[], updated: ReportTask) {
  return tasks.map((task) => (task.id === updated.id ? updated : task));
}

function removeReportTask(tasks: ReportTask[], taskId: string) {
  return tasks.filter((task) => task.id !== taskId);
}

function replaceCrawlerTask(tasks: CrawlerTask[], updated: CrawlerTask) {
  return tasks.map((task) => (task.id === updated.id ? updated : task));
}

function removeCrawlerTask(tasks: CrawlerTask[], taskId: string) {
  return tasks.filter((task) => task.id !== taskId);
}

function mergeCrawlerAccount(accounts: CrawlerAccount[], account: CrawlerAccount) {
  const exists = accounts.some((item) => item.id === account.id);
  if (!exists) return [account, ...accounts];
  return accounts.map((item) => (item.id === account.id ? account : item));
}

function removeCrawlerAccount(accounts: CrawlerAccount[], accountId: string) {
  return accounts.filter((account) => account.id !== accountId);
}

export function ConsoleShell() {
  const [activeSection, setActiveSection] = useState<Section>("dashboard");
  const [snapshot, setSnapshot] = useState<ConsoleSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [selectedPlatformId, setSelectedPlatformId] = useState<PlatformId>("wb");
  const [accountPlatformFilter, setAccountPlatformFilter] = useState<PlatformId | "all">("all");
  const [accountStatusFilter, setAccountStatusFilter] = useState<CrawlerAccountStatus | "all">("all");
  const [showAccountModal, setShowAccountModal] = useState(false);
  const [accountForm, setAccountForm] = useState<{
    platformId: PlatformId;
    loginType: PlatformPolicy["loginType"];
  }>({
    platformId: "wb",
    loginType: "qrcode"
  });
  const [accountLoginSession, setAccountLoginSession] = useState<CrawlerAccountLoginSession | null>(null);
  const [crawlerData, setCrawlerData] = useState<CrawlerDataPage | null>(null);
  const [crawlerDataLoading, setCrawlerDataLoading] = useState(false);
  const [crawlerDataPage, setCrawlerDataPage] = useState(1);
  const [crawlerDataPageSize, setCrawlerDataPageSize] = useState(20);
  const [selectedCrawlerDataRecords, setSelectedCrawlerDataRecords] = useState<Record<string, CrawlerDataRecord>>({});
  const [crawlerDataFilters, setCrawlerDataFilters] = useState<{
    platform: PlatformId | "all";
    contentType: "content" | "comment" | "all";
    q: string;
  }>({
    platform: "all",
    contentType: "all",
    q: ""
  });
  const [taskLogModal, setTaskLogModal] = useState<{
    taskType: TaskLogType;
    taskId: string;
    title: string;
    page: TaskLogPage | null;
    loading: boolean;
    error: string | null;
  } | null>(null);
  const [reportRerunModal, setReportRerunModal] = useState<{
    task: ReportTask;
    engines: Record<ReportEngineId, boolean>;
  } | null>(null);
  const [logSource, setLogSource] = useState<string>("all");
  const [configDraft, setConfigDraft] = useState<Record<string, string>>({});
  const [reportForm, setReportForm] = useState({
    topic: "",
    templateId: AUTO_REPORT_TEMPLATE_ID,
    formats: {
      html: true,
      md: false,
      pdf: true,
      json: false
    } satisfies Record<ReportFormat, boolean>,
    engines: {
      query: true,
      media: true,
      insight: true
    } satisfies Record<ReportEngineId, boolean>,
    insightMode: "normal" as ReportInsightMode
  });
  const [crawlerForm, setCrawlerForm] = useState<{
    startDate: string;
    endDate: string;
    scheduleMode: CrawlFrequency["mode"];
    scheduleCron: string;
    platforms: PlatformId[];
    keywords: string;
    crawlDepth: number;
    maxNotesPerKeyword: number;
    maxCommentsPerNote: number;
    headless: boolean;
  }>({
    startDate: "2026-05-22",
    endDate: "2026-05-25",
    scheduleMode: "manual",
    scheduleCron: "",
    platforms: ["wb"] as PlatformId[],
    keywords: "养老服务\n医保支付",
    crawlDepth: 3,
    maxNotesPerKeyword: 50,
    maxCommentsPerNote: 100,
    headless: true
  });
  const [identityForm, setIdentityForm] = useState({
    listType: "block" as IdentityListType,
    userId: "",
    label: "",
    reason: ""
  });

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await loadConsoleSnapshot();
      setSnapshot(data);
      const firstPlatform = data.platforms[0]?.id ?? "wb";
      setSelectedPlatformId(firstPlatform);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!accountLoginSession || ["completed", "failed"].includes(accountLoginSession.status)) return;
    const timer = window.setInterval(() => {
      void getCrawlerAccountLoginSession(accountLoginSession.id)
        .then((session) => {
          setAccountLoginSession(session);
          if (session.account) {
            setSnapshot((current) =>
              current
                ? {
                    ...current,
                    crawlerAccounts: mergeCrawlerAccount(current.crawlerAccounts, session.account!)
                  }
                : current
            );
          }
        })
        .catch((err) => setError(err instanceof Error ? err.message : "登录状态查询失败"));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [accountLoginSession]);

  useEffect(() => {
    if (activeSection !== "crawlerData") return;
    void loadCrawlerData();
  }, [activeSection]);

  const shouldPollReportTasks = useMemo(
    () => snapshot?.reportTasks.some((task) => activeTaskStatuses.has(task.status)) ?? false,
    [snapshot?.reportTasks]
  );

  useEffect(() => {
    if (!shouldPollReportTasks) return;

    let cancelled = false;
    const refreshReportTasks = async () => {
      try {
        const tasks = await listReportTasks();
        if (cancelled) return;
        setSnapshot((current) =>
          current
            ? {
                ...current,
                reportTasks: tasks
              }
            : current
        );
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "报告任务状态刷新失败");
        }
      }
    };

    void refreshReportTasks();
    const timer = window.setInterval(() => {
      void refreshReportTasks();
    }, 3000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [shouldPollReportTasks]);

  const shouldPollCrawlerTasks = useMemo(
    () => snapshot?.crawlerTasks.some((task) => activeTaskStatuses.has(task.status)) ?? false,
    [snapshot?.crawlerTasks]
  );

  useEffect(() => {
    if (!shouldPollCrawlerTasks) return;

    let cancelled = false;
    const refreshCrawlerTasks = async () => {
      try {
        const tasks = await listCrawlerTasks();
        if (cancelled) return;
        setSnapshot((current) =>
          current
            ? {
                ...current,
                crawlerTasks: tasks
              }
            : current
        );
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "爬虫任务状态刷新失败");
        }
      }
    };

    void refreshCrawlerTasks();
    const timer = window.setInterval(() => {
      void refreshCrawlerTasks();
    }, 3000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [shouldPollCrawlerTasks]);

  const metrics = useMemo(() => {
    if (!snapshot) return null;
    const runningComponents = snapshot.components.filter((item) => item.status === "running").length;
    const runningReports = snapshot.reportTasks.filter((task) => task.status === "running").length;
    const crawlerNotes = snapshot.crawlerTasks.reduce((sum, task) => sum + task.stats.totalNotes, 0);
    const blockedUsers = snapshot.identityRules.filter((rule) => rule.listType === "block").length;
    return { runningComponents, runningReports, crawlerNotes, blockedUsers };
  }, [snapshot]);

  const selectedPlatform = snapshot?.platforms.find((item) => item.id === selectedPlatformId) ?? null;
  const selectedRules =
    snapshot?.identityRules.filter((rule) => rule.platformId === selectedPlatformId) ?? [];
  const filteredCrawlerAccounts =
    snapshot?.crawlerAccounts.filter((account) => {
      const platformMatches = accountPlatformFilter === "all" || account.platformId === accountPlatformFilter;
      const statusMatches = accountStatusFilter === "all" || account.status === accountStatusFilter;
      return platformMatches && statusMatches;
    }) ?? [];
  const filteredLogs =
    snapshot?.logs.filter((line) => logSource === "all" || line.source === logSource) ?? [];
  const selectedCrawlerDataKeys = Object.keys(selectedCrawlerDataRecords);
  const currentCrawlerDataKeys = crawlerData?.records.map(crawlerDataRecordKey) ?? [];
  const currentCrawlerDataSelectedCount = currentCrawlerDataKeys.filter(
    (key) => selectedCrawlerDataRecords[key]
  ).length;
  const allCurrentCrawlerDataSelected =
    currentCrawlerDataKeys.length > 0 && currentCrawlerDataSelectedCount === currentCrawlerDataKeys.length;
  const accountLoginInProgress =
    accountLoginSession?.status === "opening" || accountLoginSession?.status === "waiting";
  const reportTemplateOptions = useMemo(() => {
    const templates = snapshot?.reportTemplates ?? [];
    if (templates.some((template) => template.id === AUTO_REPORT_TEMPLATE_ID)) {
      return templates;
    }
    return [autoReportTemplate, ...templates];
  }, [snapshot?.reportTemplates]);

  async function runAction(label: string, action: () => Promise<void>) {
    setBusyAction(label);
    setNotice(null);
    try {
      await action();
      setNotice(label);
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyAction(null);
    }
  }

  const startAccountLogin = () =>
    runAction("账号扫码登录已启动", async () => {
      if (!snapshot) return;
      const session = await createCrawlerAccountLoginSession({
        platformId: accountForm.platformId,
        loginType: accountForm.loginType,
        headless: true
      });
      setAccountLoginSession(session);
      if (session.account) {
        setSnapshot({
          ...snapshot,
          crawlerAccounts: mergeCrawlerAccount(snapshot.crawlerAccounts, session.account)
        });
      }
    });

  async function loadCrawlerData(page = crawlerDataPage, pageSize = crawlerDataPageSize) {
    setCrawlerDataLoading(true);
    try {
      const dataPage = await listCrawlerData({
        ...crawlerDataFilters,
        q: crawlerDataFilters.q.trim(),
        page,
        pageSize
      });
      setCrawlerData(dataPage);
      setCrawlerDataPage(dataPage.pageInfo.page);
      setCrawlerDataPageSize(dataPage.pageInfo.pageSize);
    } catch (err) {
      setError(err instanceof Error ? err.message : "爬取数据加载失败");
    } finally {
      setCrawlerDataLoading(false);
    }
  }

  const searchCrawlerData = () => {
    setSelectedCrawlerDataRecords({});
    setCrawlerDataPage(1);
    return loadCrawlerData(1, crawlerDataPageSize);
  };

  const removeCrawlerDataRecord = (record: CrawlerDataRecord) =>
    runAction("爬取数据已删除", async () => {
      await deleteCrawlerDataRecord(record);
      setSelectedCrawlerDataRecords((current) => {
        const next = { ...current };
        delete next[crawlerDataRecordKey(record)];
        return next;
      });
      setCrawlerData((current) =>
        current ? removeCrawlerDataRecordFromPage(current, record) : current
      );
      await loadCrawlerData(crawlerDataPage, crawlerDataPageSize);
    });

  const toggleCrawlerDataRecord = (record: CrawlerDataRecord, selected: boolean) => {
    const key = crawlerDataRecordKey(record);
    setSelectedCrawlerDataRecords((current) => {
      const next = { ...current };
      if (selected) {
        next[key] = record;
      } else {
        delete next[key];
      }
      return next;
    });
  };

  const toggleCurrentCrawlerDataPage = (selected: boolean) => {
    if (!crawlerData) return;
    setSelectedCrawlerDataRecords((current) => {
      const next = { ...current };
      for (const record of crawlerData.records) {
        const key = crawlerDataRecordKey(record);
        if (selected) {
          next[key] = record;
        } else {
          delete next[key];
        }
      }
      return next;
    });
  };

  const removeSelectedCrawlerDataRecords = () =>
    runAction("已删除选中爬取数据", async () => {
      const selectedRecords = Object.values(selectedCrawlerDataRecords);
      if (selectedRecords.length === 0) return;
      await deleteCrawlerDataRecords(selectedRecords);
      setSelectedCrawlerDataRecords({});
      setCrawlerData((current) =>
        current ? removeCrawlerDataRecordsFromPage(current, selectedRecords) : current
      );
      const nextPage =
        crawlerData && selectedRecords.length >= crawlerData.records.length && crawlerDataPage > 1
          ? crawlerDataPage - 1
          : crawlerDataPage;
      await loadCrawlerData(nextPage, crawlerDataPageSize);
    });

  const goToCrawlerDataPage = (page: number) => {
    const totalPages = crawlerData?.pageInfo.totalPages ?? 0;
    const nextPage = Math.max(1, totalPages ? Math.min(page, totalPages) : page);
    setCrawlerDataPage(nextPage);
    setSelectedCrawlerDataRecords({});
    void loadCrawlerData(nextPage, crawlerDataPageSize);
  };

  const removeCrawlerAccountRow = (account: CrawlerAccount) =>
    runAction("爬虫账号已删除", async () => {
      await deleteCrawlerAccount(account.id);
      setSnapshot((current) =>
        current
          ? {
              ...current,
              crawlerAccounts: removeCrawlerAccount(current.crawlerAccounts, account.id)
            }
          : current
      );
    });

  async function openTaskLogs(taskType: TaskLogType, taskId: string, title: string) {
    setTaskLogModal({ taskType, taskId, title, page: null, loading: true, error: null });
    try {
      const page = await listTaskLogs(taskType, taskId);
      setTaskLogModal((current) =>
        current && current.taskType === taskType && current.taskId === taskId
          ? { ...current, page, loading: false, error: null }
          : current
      );
    } catch (err) {
      setTaskLogModal((current) =>
        current && current.taskType === taskType && current.taskId === taskId
          ? {
              ...current,
              loading: false,
              error: err instanceof Error ? err.message : "任务日志加载失败"
            }
          : current
      );
    }
  }

  const createReport = () =>
    runAction("报告任务已创建", async () => {
      if (!snapshot) return;
      const topic = reportForm.topic.trim();
      if (!topic) throw new Error("报告主题不能为空");
      const outputFormats = Object.entries(reportForm.formats)
        .filter(([, enabled]) => enabled)
        .map(([format]) => format as ReportFormat);
      if (outputFormats.length === 0) throw new Error("至少选择一种导出格式");
      const engines = Object.entries(reportForm.engines)
        .filter(([, enabled]) => enabled)
        .map(([engine]) => engine as ReportEngineId);
      if (engines.length === 0) throw new Error("至少选择一个分析引擎");

      const task = await createReportTask({
        topic,
        templateId: reportForm.templateId,
        sourceScope: {
          orchestration: {
            enabled: true,
            engines,
            insightMode: reportForm.insightMode
          }
        },
        outputFormats,
        owner: currentUser
      });
      setSnapshot({
        ...snapshot,
        reportTasks: [task, ...snapshot.reportTasks]
      });
      setReportForm((current) => ({ ...current, topic: "" }));
    });

  function openReportRerun(task: ReportTask) {
    const engines = task.sourceScope?.orchestration?.rerunEngines?.length
      ? task.sourceScope.orchestration.rerunEngines
      : selectedReportEngines(task);
    setReportRerunModal({
      task,
      engines: {
        query: engines.includes("query"),
        media: engines.includes("media"),
        insight: engines.includes("insight")
      }
    });
  }

  const rerunReport = () =>
    runAction("报告任务已重新排队", async () => {
      if (!snapshot || !reportRerunModal) return;
      const engines = Object.entries(reportRerunModal.engines)
        .filter(([, enabled]) => enabled)
        .map(([engine]) => engine as ReportEngineId);
      if (engines.length === 0) throw new Error("至少选择一个分析引擎");
      const updated = await rerunReportTask(reportRerunModal.task.id, { engines });
      setSnapshot({
        ...snapshot,
        reportTasks: replaceReportTask(snapshot.reportTasks, updated)
      });
      setReportRerunModal(null);
    });

  const createCrawler = () =>
    runAction("爬虫任务已创建", async () => {
      if (!snapshot) return;
      if (crawlerForm.platforms.length === 0) throw new Error("至少选择一个平台");
      const keywords = Array.from(
        new Set(
          crawlerForm.keywords
            .split(/\r?\n|,/)
            .map((item) => item.trim())
            .filter(Boolean)
        )
      );
      if (keywords.length === 0) throw new Error("至少输入一个关键词");
      if (!crawlerForm.startDate || !crawlerForm.endDate) throw new Error("请选择爬取时间区间");
      if (crawlerForm.startDate > crawlerForm.endDate) throw new Error("结束日期不能早于开始日期");
      if (crawlerForm.scheduleMode === "cron" && !crawlerForm.scheduleCron.trim()) {
        throw new Error("Cron 表达式不能为空");
      }
      const task = await createCrawlerTask({
        runMode: "deep_sentiment",
        startDate: crawlerForm.startDate,
        endDate: crawlerForm.endDate,
        schedule: {
          mode: crawlerForm.scheduleMode,
          timezone: "Asia/Shanghai",
          ...(crawlerForm.scheduleMode === "cron" ? { cron: crawlerForm.scheduleCron.trim() } : {})
        },
        platforms: crawlerForm.platforms,
        keywords,
        keywordSource: "manual",
        crawlDepth: crawlerForm.crawlDepth,
        maxNotesPerKeyword: crawlerForm.maxNotesPerKeyword,
        maxCommentsPerNote: crawlerForm.maxCommentsPerNote,
        headless: crawlerForm.headless,
        owner: currentUser
      });
      setSnapshot({
        ...snapshot,
        crawlerTasks: [task, ...snapshot.crawlerTasks]
      });
    });

  const addIdentity = () =>
    runAction("名单规则已添加", async () => {
      if (!snapshot) return;
      const userId = identityForm.userId.trim();
      if (!userId) throw new Error("用户 ID 不能为空");
      const rule = await createIdentityRule({
        platformId: selectedPlatformId,
        listType: identityForm.listType,
        userId,
        label: identityForm.label.trim(),
        reason: identityForm.reason.trim(),
        createdBy: currentUser
      });
      setSnapshot({
        ...snapshot,
        identityRules: [rule, ...snapshot.identityRules],
        platforms: snapshot.platforms.map((platform) =>
          platform.id === selectedPlatformId
            ? {
                ...platform,
                identityRuleCounts: {
                  ...platform.identityRuleCounts,
                  [rule.listType]: platform.identityRuleCounts[rule.listType] + 1
                }
              }
            : platform
        )
      });
      setIdentityForm((current) => ({ ...current, userId: "", label: "", reason: "" }));
    });

  const removeIdentity = (rule: IdentityRule) =>
    runAction("名单规则已删除", async () => {
      if (!snapshot) return;
      await deleteIdentityRule(rule.platformId, rule.id);
      setSnapshot({
        ...snapshot,
        identityRules: snapshot.identityRules.filter((item) => item.id !== rule.id),
        platforms: snapshot.platforms.map((platform) =>
          platform.id === rule.platformId
            ? {
                ...platform,
                identityRuleCounts: {
                  ...platform.identityRuleCounts,
                  [rule.listType]: Math.max(0, platform.identityRuleCounts[rule.listType] - 1)
                }
              }
            : platform
        )
      });
    });

  const saveConfig = () =>
    runAction("系统配置已保存", async () => {
      if (!snapshot) return;
      const fields = await updateSystemConfig(configDraft);
      setSnapshot({
        ...snapshot,
        configFields: fields
      });
      setConfigDraft({});
    });

  if (loading) {
    return (
      <main className="loading-screen">
        <Loader2 className="spin" size={32} />
        <span>正在加载 SaaS 控制台</span>
      </main>
    );
  }

  if (error && !snapshot) {
    return (
      <main className="error-screen">
        <AlertTriangle size={34} />
        <strong>控制台加载失败</strong>
        <span>{error}</span>
        <Button className="primary-button" onClick={() => void load()}>
          <RefreshCcw size={16} />
          重试
        </Button>
      </main>
    );
  }

  if (!snapshot || !metrics) {
    return null;
  }

  return (
    <div className="console-root">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">BF</div>
          <div>
            <strong>BettaFish</strong>
            <span>SaaS Console</span>
          </div>
        </div>
        <nav className="nav-list" aria-label="Console sections">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <Button
                key={item.id}
                variant="ghost"
                className={classNames("nav-button", activeSection === item.id && "active")}
                onClick={() => setActiveSection(item.id)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </Button>
            );
          })}
        </nav>
        <div className="workspace-panel">
          <span>Workspace</span>
          <strong>{snapshot.workspaceId}</strong>
          <small>{snapshot.mock ? "Mock adapter" : "API connected"}</small>
        </div>
      </aside>

      <main className="main-surface">
        <header className="topbar">
          <div>
            <h1>舆情 SaaS 控制台</h1>
            <p>报告、爬虫、平台名单与运行状态统一入口</p>
          </div>
          <div className="topbar-actions">
            {notice && <span className="notice">{notice}</span>}
            {error && (
              <Button variant="ghost" className="ghost-button error-inline" onClick={() => setError(null)}>
                <XCircle size={16} />
                {error}
              </Button>
            )}
            <Button variant="outline" size="icon" className="icon-button" title="刷新" onClick={() => void load()}>
              <RefreshCcw size={18} />
            </Button>
          </div>
        </header>

        {activeSection === "dashboard" && (
          <section className="section-band">
            <SectionHeader title="运行总览" subtitle="引擎、任务与策略的实时摘要" />
            <div className="metric-grid">
              <MetricTile
                label="运行组件"
                value={`${metrics.runningComponents}/${snapshot.components.length}`}
                detail="Query / Media / Insight / Forum / Report"
                icon={Gauge}
              />
              <MetricTile
                label="报告生成中"
                value={String(metrics.runningReports)}
                detail={`${snapshot.reportTasks.length} 个报告任务`}
                icon={BarChart3}
              />
              <MetricTile
                label="采集内容"
                value={formatNumber(metrics.crawlerNotes)}
                detail="MindSpider notes"
                icon={Database}
              />
              <MetricTile
                label="屏蔽用户"
                value={String(metrics.blockedUsers)}
                detail="跨平台 identity rules"
                icon={Shield}
              />
            </div>
            <div className="two-column">
              <div className="flat-panel">
                <h3>组件状态</h3>
                <div className="component-list">
                  {snapshot.components.map((component) => (
                    <div className="component-row" key={component.id}>
                      <div>
                        <strong>{component.name}</strong>
                        <span>{component.message ?? "No message"}</span>
                      </div>
                      <div className="row-meta">
                        {component.lastHeartbeatAt && <span>{formatTime(component.lastHeartbeatAt)}</span>}
                        <StatusBadge value={component.status} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="flat-panel">
                <h3>近期日志</h3>
                <div className="compact-log-list">
                  {snapshot.logs.slice(0, 5).map((line) => (
                    <div className="compact-log" key={line.id}>
                      <span>{formatTime(line.timestamp)}</span>
                      <strong>{line.source}</strong>
                      <p>{line.message}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </section>
        )}

        {activeSection === "reports" && (
          <section className="section-band">
            <SectionHeader
              title="报告任务"
              subtitle="基于多智能体输入生成 HTML、Markdown 与 PDF 报告"
              action={
                <Button
                  className="primary-button"
                  onClick={() => void createReport()}
                  disabled={busyAction !== null}
                >
                  <FilePlus2 size={16} />
                  创建报告
                </Button>
              }
            />
            <div className="form-grid report-form">
              <label className="field">
                <span>主题</span>
                <Input
                  value={reportForm.topic}
                  onChange={(event) => setReportForm({ ...reportForm, topic: event.target.value })}
                  placeholder="输入报告主题"
                />
              </label>
              <label className="field">
                <span>模板</span>
                <select
                  value={reportForm.templateId}
                  onChange={(event) => setReportForm({ ...reportForm, templateId: event.target.value })}
                >
                  {reportTemplateOptions.map((template) => (
                    <option key={template.id} value={template.id}>
                      {template.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="report-engine-row">
                <span className="control-group-label">分析引擎</span>
                {reportEngineOptions.map(([engine, label]) => (
                  <label key={engine} className="toggle-pill">
                    <input
                      type="checkbox"
                      checked={reportForm.engines[engine]}
                      onChange={(event) =>
                        setReportForm({
                          ...reportForm,
                          engines: {
                            ...reportForm.engines,
                            [engine]: event.target.checked
                          }
                        })
                      }
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
              <div
                className={classNames("insight-mode-row", !reportForm.engines.insight && "disabled")}
                aria-disabled={!reportForm.engines.insight}
              >
                <span id="insight-mode-label" className="control-group-label">
                  Insight 模式
                </span>
                <div className="segmented-control" role="radiogroup" aria-labelledby="insight-mode-label">
                  {reportInsightModeOptions.map(([mode, label]) => (
                    <label
                      key={mode}
                      className={classNames(
                        "segmented-option",
                        reportForm.insightMode === mode && "active",
                        !reportForm.engines.insight && "disabled"
                      )}
                    >
                      <input
                        type="radio"
                        name="insight-mode"
                        value={mode}
                        checked={reportForm.insightMode === mode}
                        disabled={!reportForm.engines.insight}
                        onChange={() => setReportForm({ ...reportForm, insightMode: mode })}
                      />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="format-row">
                {(Object.keys(reportForm.formats) as ReportFormat[]).map((format) => (
                  <label key={format} className="toggle-pill">
                    <input
                      type="checkbox"
                      checked={reportForm.formats[format]}
                      onChange={(event) =>
                        setReportForm({
                          ...reportForm,
                          formats: {
                            ...reportForm.formats,
                            [format]: event.target.checked
                          }
                        })
                      }
                    />
                    <span>{format.toUpperCase()}</span>
                  </label>
                ))}
              </div>
            </div>
            {snapshot.reportTasks.length === 0 ? (
              <EmptyState title="暂无报告任务" detail="创建任务后会显示进度、状态和导出入口" />
            ) : (
              <div className="task-table">
                {snapshot.reportTasks.map((task) => (
                  <div className="task-row" key={task.id}>
                    <button
                      type="button"
                      className="task-main task-main-button"
                      onClick={() => void openTaskLogs("report", task.id, task.topic)}
                    >
                      <strong>{task.topic}</strong>
                      <span>
                        {formatReportEngines(task)} · {formatReportInsightMode(task)} · {task.id} ·{" "}
                        {formatTime(task.updatedAt)}
                      </span>
                      <ProgressBar value={task.progress} />
                    </button>
                    <StatusBadge value={task.status} />
                    <div className="artifact-list">
                      {task.artifacts.map((artifact) => (
                        <a
                          key={artifact.format}
                          className={classNames("artifact-chip", !artifact.ready && "disabled")}
                          href={artifact.ready ? artifact.downloadUrl : undefined}
                          aria-disabled={!artifact.ready}
                        >
                          <Download size={14} />
                          {artifact.format}
                        </a>
                      ))}
                    </div>
                    <div className="task-actions">
                      <Button
                        variant="outline"
                        size="icon"
                        className="icon-button"
                        title="查看任务日志"
                        onClick={() => void openTaskLogs("report", task.id, task.topic)}
                      >
                        <TerminalSquare size={16} />
                      </Button>
                      {task.status === "running" ? (
                        <Button
                          variant="outline"
                          size="icon"
                          className="icon-button danger"
                          title="取消报告任务"
                          onClick={() =>
                            void runAction("报告任务已取消", async () => {
                              const updated = await cancelReportTask(task.id);
                              setSnapshot({
                                ...snapshot,
                                reportTasks: replaceReportTask(snapshot.reportTasks, updated)
                              });
                            })
                          }
                        >
                          <CircleStop size={16} />
                        </Button>
                      ) : null}
                      {task.status !== "running" && task.status !== "pending" && task.status !== "queued" ? (
                        <Button
                          variant="outline"
                          size="icon"
                          className="icon-button"
                          title="重跑报告任务"
                          onClick={() => openReportRerun(task)}
                        >
                          <RefreshCcw size={16} />
                        </Button>
                      ) : null}
                      {task.status !== "running" && task.status !== "pending" ? (
                        <Button
                          variant="outline"
                          size="icon"
                          className="icon-button danger"
                          title="删除报告任务"
                          onClick={() =>
                            void runAction("报告任务已删除", async () => {
                              await deleteReportTask(task.id);
                              setSnapshot({
                                ...snapshot,
                                reportTasks: removeReportTask(snapshot.reportTasks, task.id)
                              });
                            })
                          }
                        >
                          <Trash2 size={16} />
                        </Button>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {activeSection === "crawlers" && (
          <section className="section-band">
            <SectionHeader
              title="爬虫任务"
              subtitle="指定关键词与平台后发起 MindSpider 采集"
              action={
                <Button
                  className="primary-button"
                  onClick={() => void createCrawler()}
                  disabled={busyAction !== null}
                >
                  <Play size={16} />
                  创建任务
                </Button>
              }
            />
            <div className="account-panel">
              <div className="account-panel-title">
                <div>
                  <h3>爬虫账号</h3>
                  <span>{filteredCrawlerAccounts.length} / {snapshot.crawlerAccounts.length} 个账号</span>
                </div>
                <div className="account-filters">
                  <Button variant="secondary" className="secondary-button" onClick={() => setShowAccountModal(true)}>
                    <Plus size={16} />
                    增加账号
                  </Button>
                  <select
                    value={accountPlatformFilter}
                    aria-label="账号平台筛选"
                    onChange={(event) =>
                      setAccountPlatformFilter(event.target.value === "all" ? "all" : (event.target.value as PlatformId))
                    }
                  >
                    <option value="all">全部平台</option>
                    {snapshot.platforms.map((platform) => (
                      <option key={platform.id} value={platform.id}>
                        {platform.name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={accountStatusFilter}
                    aria-label="账号状态筛选"
                    onChange={(event) =>
                      setAccountStatusFilter(
                        event.target.value === "all" ? "all" : (event.target.value as CrawlerAccountStatus)
                      )
                    }
                  >
                    <option value="all">全部状态</option>
                    <option value="active">active</option>
                    <option value="login_required">login_required</option>
                    <option value="expired">expired</option>
                    <option value="disabled">disabled</option>
                    <option value="error">error</option>
                    <option value="unknown">unknown</option>
                  </select>
                </div>
              </div>
              {filteredCrawlerAccounts.length === 0 ? (
                <EmptyState title="暂无爬虫账号" detail="账号接入后会显示平台、状态、登录方式与校验结果" />
              ) : (
                <div className="account-list">
                  {filteredCrawlerAccounts.map((account) => (
                    <div className="account-row" key={account.id}>
                      <div className="account-identity">
                        <span className="account-avatar" aria-hidden="true">
                          {account.avatarUrl ? (
                            <img src={account.avatarUrl} alt="" />
                          ) : (
                            (account.displayName ?? account.username ?? account.accountId).slice(0, 1).toUpperCase()
                          )}
                        </span>
                        <div>
                          <strong>{account.displayName ?? account.username ?? account.accountId}</strong>
                          <span>{account.username ?? account.accountId}</span>
                        </div>
                      </div>
                      <div className="account-meta">
                        <span>{platformNames[account.platformId]}</span>
                        <StatusBadge value={account.status} />
                      </div>
                      <div className="account-meta">
                        <span>{account.loginType ? loginTypeLabels[account.loginType] : "未登记"}</span>
                        <small>{account.lastCheckedAt ? formatTime(account.lastCheckedAt) : "未校验"}</small>
                      </div>
                      <div className="account-detail">
                        <strong>{account.accountId}</strong>
                        <span>{account.details?.message ?? "无附加说明"}</span>
                      </div>
                      <div className="account-actions">
                        <Button
                          variant="outline"
                          size="icon"
                          className="icon-button danger"
                          title="删除爬虫账号"
                          onClick={() => void removeCrawlerAccountRow(account)}
                          disabled={busyAction !== null}
                        >
                          <Trash2 size={16} />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="form-grid crawler-form">
              <label className="field wide">
                <span>关键词</span>
                <Textarea
                  value={crawlerForm.keywords}
                  onChange={(event) => setCrawlerForm({ ...crawlerForm, keywords: event.target.value })}
                  placeholder="每行一个关键词"
                />
              </label>
              <label className="field">
                <span>开始日期</span>
                <Input
                  type="date"
                  value={crawlerForm.startDate}
                  onChange={(event) => setCrawlerForm({ ...crawlerForm, startDate: event.target.value })}
                />
              </label>
              <label className="field">
                <span>结束日期</span>
                <Input
                  type="date"
                  value={crawlerForm.endDate}
                  onChange={(event) => setCrawlerForm({ ...crawlerForm, endDate: event.target.value })}
                />
              </label>
              <label className="field">
                <span>爬取深度</span>
                <Input
                  type="number"
                  min={1}
                  max={10}
                  value={crawlerForm.crawlDepth}
                  onChange={(event) => setCrawlerForm({ ...crawlerForm, crawlDepth: Number(event.target.value) })}
                />
              </label>
              <label className="field">
                <span>定时</span>
                <select
                  value={crawlerForm.scheduleMode}
                  onChange={(event) =>
                    setCrawlerForm({ ...crawlerForm, scheduleMode: event.target.value as CrawlFrequency["mode"] })
                  }
                >
                  <option value="manual">手动执行</option>
                  <option value="hourly">每小时</option>
                  <option value="daily">每日</option>
                  <option value="weekly">每周</option>
                  <option value="cron">Cron</option>
                </select>
              </label>
              {crawlerForm.scheduleMode === "cron" ? (
                <label className="field">
                  <span>Cron</span>
                  <Input
                    value={crawlerForm.scheduleCron}
                    onChange={(event) => setCrawlerForm({ ...crawlerForm, scheduleCron: event.target.value })}
                    placeholder="0 9 * * *"
                  />
                </label>
              ) : null}
              <label className="field">
                <span>笔记/关键词</span>
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  value={crawlerForm.maxNotesPerKeyword}
                  onChange={(event) =>
                    setCrawlerForm({ ...crawlerForm, maxNotesPerKeyword: Number(event.target.value) })
                  }
                />
              </label>
              <label className="field">
                <span>评论/笔记</span>
                <Input
                  type="number"
                  min={0}
                  max={5000}
                  value={crawlerForm.maxCommentsPerNote}
                  onChange={(event) =>
                    setCrawlerForm({ ...crawlerForm, maxCommentsPerNote: Number(event.target.value) })
                  }
                />
              </label>
              <label className="toggle-pill headless-toggle">
                <input
                  type="checkbox"
                  checked={crawlerForm.headless}
                  onChange={(event) => setCrawlerForm({ ...crawlerForm, headless: event.target.checked })}
                />
                <span>Headless</span>
              </label>
              <div className="platform-checks">
                {snapshot.platforms.map((platform) => (
                  <label key={platform.id} className="toggle-pill">
                    <input
                      type="checkbox"
                      checked={crawlerForm.platforms.includes(platform.id)}
                      onChange={(event) =>
                        setCrawlerForm({
                          ...crawlerForm,
                          platforms: event.target.checked
                            ? [...crawlerForm.platforms, platform.id]
                            : crawlerForm.platforms.filter((id) => id !== platform.id)
                        })
                      }
                    />
                    <span>{platform.name}</span>
                  </label>
                ))}
              </div>
            </div>
            <div className="task-table">
              {snapshot.crawlerTasks.map((task) => (
                <div className="task-row crawler-row" key={task.id}>
                  <button
                    type="button"
                    className="task-main task-main-button"
                    onClick={() =>
                      void openTaskLogs(
                        "crawler",
                        task.id,
                        task.keywords.slice(0, 3).join(" / ") || task.runMode
                      )
                    }
                  >
                    <strong>{task.keywords.slice(0, 3).join(" / ") || task.runMode}</strong>
                    <span>
                      {task.platforms.map((id) => platformNames[id]).join(" / ")} · {formatCrawlerDateRange(task)} ·{" "}
                      {formatCrawlerSchedule(task.schedule)} ·{" "}
                      {formatTime(task.updatedAt)}
                    </span>
                    <ProgressBar value={task.progress} />
                  </button>
                  <StatusBadge value={task.status} />
                  <div className="task-stats">
                    <span>{formatNumber(task.stats.totalNotes)} notes</span>
                    <span>{formatNumber(task.stats.totalComments)} comments</span>
                  </div>
                  <div className="task-actions">
                    <Button
                      variant="outline"
                      size="icon"
                      className="icon-button"
                      title="查看任务日志"
                      onClick={() =>
                        void openTaskLogs(
                          "crawler",
                          task.id,
                          task.keywords.slice(0, 3).join(" / ") || task.runMode
                        )
                      }
                    >
                      <TerminalSquare size={16} />
                    </Button>
                    {task.status === "running" ? (
                      <Button
                        variant="outline"
                        size="icon"
                        className="icon-button danger"
                        title="停止爬虫任务"
                        onClick={() =>
                          void runAction("爬虫任务停止中", async () => {
                            const updated = await stopCrawlerTask(task.id);
                            setSnapshot({
                              ...snapshot,
                              crawlerTasks: replaceCrawlerTask(snapshot.crawlerTasks, updated)
                            });
                          })
                        }
                      >
                        <StopCircle size={16} />
                      </Button>
                    ) : null}
                    {task.status !== "running" && task.status !== "stopping" ? (
                      <Button
                        variant="outline"
                        size="icon"
                        className="icon-button danger"
                        title="删除爬虫任务"
                        onClick={() =>
                          void runAction("爬虫任务已删除", async () => {
                            await deleteCrawlerTask(task.id);
                            setSnapshot({
                              ...snapshot,
                              crawlerTasks: removeCrawlerTask(snapshot.crawlerTasks, task.id)
                            });
                          })
                        }
                      >
                        <Trash2 size={16} />
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {activeSection === "crawlerData" && (
          <section className="section-band">
            <SectionHeader
              title="爬取数据库"
              subtitle="按平台、类型和关键词检索已入库的采集内容"
              action={
                <div className="section-actions">
                  <Button
                    variant="outline"
                    size="icon"
                    className="icon-button"
                    title="刷新爬取数据"
                    onClick={() => void loadCrawlerData()}
                    disabled={crawlerDataLoading}
                  >
                    {crawlerDataLoading ? <Loader2 className="spin" size={18} /> : <RefreshCcw size={18} />}
                  </Button>
                  <Button
                    className="primary-button"
                    onClick={() => void searchCrawlerData()}
                    disabled={crawlerDataLoading}
                  >
                    {crawlerDataLoading ? <Loader2 className="spin" size={16} /> : <Search size={16} />}
                    检索
                  </Button>
                </div>
              }
            />
            <div className="data-filters">
              <label className="field">
                <span>平台</span>
                <select
                  value={crawlerDataFilters.platform}
                  onChange={(event) => {
                    setCrawlerDataPage(1);
                    setSelectedCrawlerDataRecords({});
                    setCrawlerDataFilters({
                      ...crawlerDataFilters,
                      platform: event.target.value === "all" ? "all" : (event.target.value as PlatformId)
                    });
                  }}
                >
                  <option value="all">全部平台</option>
                  {snapshot.platforms.map((platform) => (
                    <option key={platform.id} value={platform.id}>
                      {platform.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>类型</span>
                <select
                  value={crawlerDataFilters.contentType}
                  onChange={(event) => {
                    setCrawlerDataPage(1);
                    setSelectedCrawlerDataRecords({});
                    setCrawlerDataFilters({
                      ...crawlerDataFilters,
                      contentType: event.target.value as "content" | "comment" | "all"
                    });
                  }}
                >
                  <option value="all">全部类型</option>
                  <option value="content">内容</option>
                  <option value="comment">评论</option>
                </select>
              </label>
              <label className="field data-query-field">
                <span>检索词</span>
                <Input
                  value={crawlerDataFilters.q}
                  onChange={(event) => {
                    setCrawlerDataPage(1);
                    setSelectedCrawlerDataRecords({});
                    setCrawlerDataFilters({ ...crawlerDataFilters, q: event.target.value });
                  }}
                  placeholder="标题、正文、作者、关键词"
                />
              </label>
            </div>
            <div className="data-summary">
              <MetricTile
                label="结果数"
                value={formatNumber(crawlerData?.summary.totalRecords ?? 0)}
                detail="当前筛选返回记录"
                icon={Database}
              />
              <MetricTile
                label="内容"
                value={formatNumber(crawlerData?.summary.byType.content ?? 0)}
                detail="contents tables"
                icon={ClipboardList}
              />
              <MetricTile
                label="评论"
                value={formatNumber(crawlerData?.summary.byType.comment ?? 0)}
                detail="comments tables"
                icon={Activity}
              />
            </div>
            <div className="crawler-data-toolbar">
              <label className="checkbox-row crawler-data-select-all">
                <input
                  type="checkbox"
                  checked={allCurrentCrawlerDataSelected}
                  disabled={!crawlerData || crawlerData.records.length === 0}
                  onChange={(event) => toggleCurrentCrawlerDataPage(event.target.checked)}
                  aria-label="全选当前页爬取数据"
                />
                <span>全选本页</span>
              </label>
              <span className="selection-count">{selectedCrawlerDataKeys.length} 条已选</span>
              <Button
                variant="outline"
                className="secondary-button danger"
                onClick={() => void removeSelectedCrawlerDataRecords()}
                disabled={busyAction !== null || selectedCrawlerDataKeys.length === 0}
              >
                <Trash2 size={16} />
                删除选中
              </Button>
              <div className="crawler-data-pagination">
                <Button
                  variant="outline"
                  className="secondary-button"
                  onClick={() => goToCrawlerDataPage(crawlerDataPage - 1)}
                  disabled={crawlerDataLoading || !crawlerData?.pageInfo.hasPreviousPage}
                >
                  上一页
                </Button>
                <span>
                  {crawlerData?.pageInfo.totalPages
                    ? `${crawlerData.pageInfo.page} / ${crawlerData.pageInfo.totalPages}`
                    : "0 / 0"}
                </span>
                <Button
                  variant="outline"
                  className="secondary-button"
                  onClick={() => goToCrawlerDataPage(crawlerDataPage + 1)}
                  disabled={crawlerDataLoading || !crawlerData?.pageInfo.hasNextPage}
                >
                  下一页
                </Button>
                <label className="page-size-control">
                  <span>每页</span>
                  <select
                    value={crawlerDataPageSize}
                    onChange={(event) => {
                      const nextPageSize = Number(event.target.value);
                      setCrawlerDataPageSize(nextPageSize);
                      setCrawlerDataPage(1);
                      setSelectedCrawlerDataRecords({});
                      void loadCrawlerData(1, nextPageSize);
                    }}
                    aria-label="爬取数据每页条数"
                  >
                    <option value={10}>10</option>
                    <option value={20}>20</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                  </select>
                </label>
              </div>
            </div>
            {!crawlerData || crawlerData.records.length === 0 ? (
              <EmptyState
                title="暂无匹配数据"
                detail={crawlerData?.message ?? "完成爬取并入库后，可在这里检索内容与评论"}
              />
            ) : (
              <div className="crawler-data-table">
                {crawlerData.records.map((record) => (
                  <div className="crawler-data-row" key={record.id}>
                    <label className="crawler-data-checkbox">
                      <input
                        type="checkbox"
                        checked={Boolean(selectedCrawlerDataRecords[crawlerDataRecordKey(record)])}
                        onChange={(event) => toggleCrawlerDataRecord(record, event.target.checked)}
                        aria-label={`选择爬取数据 ${record.title || record.sourceId}`}
                      />
                    </label>
                    <div className="crawler-data-main">
                      <div className="crawler-data-title">
                        <strong>{record.title || record.sourceId}</strong>
                        <Badge className="crawler-data-type-badge" variant="idle">
                          {crawlerDataTypeLabels[record.contentType]}
                        </Badge>
                        <SentimentBadge value={record.sentiment} />
                      </div>
                      <p>{record.textSnippet || "无正文摘要"}</p>
                      <div className="crawler-data-meta">
                        <span>{platformNames[record.platformId]}</span>
                        {record.keyword && <span>{record.keyword}</span>}
                        {record.author && <span>{record.author}</span>}
                      </div>
                    </div>
                    <div className="crawler-data-metrics">
                      <span>{record.metrics?.likes ?? "-"} likes</span>
                      <span>{record.metrics?.comments ?? "-"} comments</span>
                    </div>
                    <div className="crawler-data-actions">
                      {record.url ? (
                        <Button
                          asChild
                          variant="outline"
                          size="icon"
                          className="icon-button"
                          title="打开原文"
                        >
                          <a href={record.url} target="_blank" rel="noreferrer">
                            <ExternalLink size={16} />
                          </a>
                        </Button>
                      ) : null}
                      <Button
                        variant="outline"
                        size="icon"
                        className="icon-button danger"
                        title="删除爬取数据"
                        onClick={() => void removeCrawlerDataRecord(record)}
                        disabled={busyAction !== null}
                      >
                        <Trash2 size={16} />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}

        {activeSection === "platforms" && (
          <section className="section-band">
            <SectionHeader
              title="平台用户名单"
              subtitle="按平台维护 allow/block 用户名单"
            />
            <div className="platform-layout">
              <div className="platform-list">
                {snapshot.platforms.map((platform) => (
                  <button
                    key={platform.id}
                    className={classNames("platform-button", selectedPlatformId === platform.id && "active")}
                    onClick={() => setSelectedPlatformId(platform.id)}
                  >
                    <strong>{platform.name}</strong>
                    <span>
                      allow {platform.identityRuleCounts.allow} · block {platform.identityRuleCounts.block}
                    </span>
                  </button>
                ))}
              </div>

              {selectedPlatform && (
                <div className="policy-editor">
                  <div className="policy-title">
                    <div>
                      <h3>{selectedPlatform.name}</h3>
                      <span>{selectedPlatform.id} / {selectedPlatform.crawlerType}</span>
                    </div>
                  </div>
                  <div className="identity-manager">
                    <h3>用户名单</h3>
                    <div className="identity-form">
                      <select
                        value={identityForm.listType}
                        onChange={(event) =>
                          setIdentityForm({ ...identityForm, listType: event.target.value as IdentityListType })
                        }
                      >
                        <option value="block">block</option>
                        <option value="allow">allow</option>
                      </select>
                      <Input
                        value={identityForm.userId}
                        onChange={(event) => setIdentityForm({ ...identityForm, userId: event.target.value })}
                        placeholder="平台用户 ID"
                      />
                      <Input
                        value={identityForm.label}
                        onChange={(event) => setIdentityForm({ ...identityForm, label: event.target.value })}
                        placeholder="标签"
                      />
                      <Button variant="secondary" className="secondary-button" onClick={() => void addIdentity()}>
                        <Shield size={16} />
                        添加
                      </Button>
                    </div>
                    {selectedRules.length === 0 ? (
                      <EmptyState title="暂无名单规则" detail="allow/block 规则会影响爬取与素材筛选" />
                    ) : (
                      <div className="rule-list">
                        {selectedRules.map((rule) => (
                          <div className="rule-row" key={rule.id}>
                            <StatusBadge value={rule.listType === "allow" ? "running" : "stopped"} />
                            <div>
                              <strong>{rule.userId}</strong>
                              <span>{rule.label || rule.reason || "未填写说明"}</span>
                            </div>
                            <Button
                              variant="outline"
                              size="icon"
                              className="icon-button danger"
                              title="删除名单规则"
                              onClick={() => void removeIdentity(rule)}
                            >
                              <Trash2 size={16} />
                            </Button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </section>
        )}

        {activeSection === "config" && (
          <section className="section-band">
            <SectionHeader
              title="系统配置"
              subtitle="LLM、搜索、数据库与爬虫运行参数"
              action={
                <Button className="primary-button" onClick={() => void saveConfig()}>
                  <Save size={16} />
                  保存配置
                </Button>
              }
            />
            <div className="config-groups">
              {(["server", "database", "llm", "search", "crawler"] as ConfigField["group"][]).map((group) => {
                const fields = snapshot.configFields.filter((field) => field.group === group);
                if (fields.length === 0) return null;
                return (
                  <div className="config-group" key={group}>
                    <h3>{group}</h3>
                    <div className="config-grid">
                      {fields.map((field) => {
                        const value = configDraft[field.key] ?? (field.sensitive ? "" : field.value);
                        return (
                          <label className="field" key={field.key}>
                            <span>
                              {field.sensitive && <LockKeyhole size={14} />}
                              {field.label}
                            </span>
                            {field.type === "enum" ? (
                              <select
                                value={value}
                                onChange={(event) =>
                                  setConfigDraft({ ...configDraft, [field.key]: event.target.value })
                                }
                              >
                                {field.options?.map((option) => (
                                  <option key={option} value={option}>
                                    {option}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <Input
                                type={field.sensitive ? "password" : field.type === "number" ? "number" : "text"}
                                value={value}
                                placeholder={field.sensitive ? field.value : ""}
                                onChange={(event) =>
                                  setConfigDraft({ ...configDraft, [field.key]: event.target.value })
                                }
                              />
                            )}
                          </label>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {activeSection === "logs" && (
          <section className="section-band">
            <SectionHeader
              title="运行日志"
              subtitle="按来源查看系统、引擎、报告和爬虫日志"
              action={
                <label className="filter-control">
                  <Filter size={16} />
                  <select value={logSource} onChange={(event) => setLogSource(event.target.value)}>
                    <option value="all">all</option>
                    <option value="system">system</option>
                    <option value="query">query</option>
                    <option value="media">media</option>
                    <option value="insight">insight</option>
                    <option value="forum">forum</option>
                    <option value="report">report</option>
                    <option value="crawler">crawler</option>
                  </select>
                </label>
              }
            />
            {filteredLogs.length === 0 ? (
              <EmptyState title="没有匹配日志" detail="调整来源筛选后再查看" />
            ) : (
              <div className="log-table">
                {filteredLogs.map((line) => (
                  <div className="log-row" key={line.id}>
                    <span>{formatTime(line.timestamp)}</span>
                    <StatusBadge value={line.level === "error" ? "failed" : line.level === "warning" ? "queued" : "running"} />
                    <strong>{line.source}</strong>
                    <p>{line.message}</p>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </main>

      {reportRerunModal && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="rerun-report-title">
            <div className="modal-header">
              <div>
                <h3 id="rerun-report-title">重跑报告任务</h3>
                <span>{reportRerunModal.task.topic}</span>
              </div>
              <Button
                variant="outline"
                size="icon"
                className="icon-button"
                title="关闭"
                onClick={() => setReportRerunModal(null)}
              >
                <XCircle size={18} />
              </Button>
            </div>
            <div className="report-engine-row modal-engine-row">
              <span className="control-group-label">重跑引擎</span>
              {reportEngineOptions.map(([engine, label]) => (
                <label key={engine} className="toggle-pill">
                  <input
                    type="checkbox"
                    checked={reportRerunModal.engines[engine]}
                    onChange={(event) =>
                      setReportRerunModal({
                        ...reportRerunModal,
                        engines: {
                          ...reportRerunModal.engines,
                          [engine]: event.target.checked
                        }
                      })
                    }
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
            <div className="modal-actions">
              <Button variant="secondary" className="secondary-button" onClick={() => setReportRerunModal(null)}>
                关闭
              </Button>
              <Button className="primary-button" onClick={() => void rerunReport()} disabled={busyAction !== null}>
                <RefreshCcw size={16} />
                重新运行
              </Button>
            </div>
          </div>
        </div>
      )}

      {showAccountModal && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="add-account-title">
            <div className="modal-header">
              <div>
                <h3 id="add-account-title">增加账号</h3>
                <span>选择平台和登录方式</span>
              </div>
              <Button
                variant="outline"
                size="icon"
                className="icon-button"
                title="关闭"
                onClick={() => {
                  setShowAccountModal(false);
                  setAccountLoginSession(null);
                }}
              >
                <XCircle size={18} />
              </Button>
            </div>
            <div className="form-grid modal-form">
              <label className="field">
                <span>平台</span>
                <select
                  value={accountForm.platformId}
                  onChange={(event) =>
                    setAccountForm({ ...accountForm, platformId: event.target.value as PlatformId })
                  }
                  disabled={accountLoginInProgress}
                >
                  {snapshot.platforms.map((platform) => (
                    <option key={platform.id} value={platform.id}>
                      {platform.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>登录方式</span>
                <select
                  value={accountForm.loginType}
                  onChange={(event) =>
                    setAccountForm({ ...accountForm, loginType: event.target.value as PlatformPolicy["loginType"] })
                  }
                  disabled={accountLoginInProgress}
                >
                  <option value="qrcode">qrcode</option>
                  <option value="phone">phone</option>
                  <option value="cookie">cookie</option>
                </select>
              </label>
            </div>
            {accountLoginSession && (
              <div className="login-session-panel">
                <div className="login-session-heading">
                  <StatusBadge
                    value={
                      accountLoginSession.status === "completed"
                        ? "succeeded"
                        : accountLoginSession.status === "failed"
                          ? "failed"
                          : "running"
                    }
                  />
                  <strong>{accountLoginSession.message ?? accountLoginSession.status}</strong>
                </div>
                {accountLoginSession.observedStateCount !== undefined && (
                  <span>{accountLoginSession.observedStateCount} 个浏览器状态项</span>
                )}
                {accountLoginSession.loginPreviewImage && accountLoginSession.status !== "completed" && (
                  <div className="login-preview">
                    <img
                      src={accountLoginSession.loginPreviewImage}
                      alt={accountLoginSession.loginPreviewKind === "qrcode" ? "登录二维码" : "登录页预览"}
                    />
                    <span>
                      {accountLoginSession.loginPreviewKind === "qrcode"
                        ? "用对应平台 App 扫码后保持此窗口打开"
                        : "未定位到二维码，已显示当前登录页预览"}
                    </span>
                  </div>
                )}
                {accountLoginSession.account && (
                  <span>已登记：{accountLoginSession.account.displayName ?? accountLoginSession.account.accountId}</span>
                )}
              </div>
            )}
            <div className="modal-actions">
              <Button
                variant="secondary"
                className="secondary-button"
                onClick={() => {
                  setShowAccountModal(false);
                  setAccountLoginSession(null);
                }}
              >
                关闭
              </Button>
              <Button
                className="primary-button"
                onClick={() => void startAccountLogin()}
                disabled={busyAction !== null || accountLoginInProgress}
              >
                <ExternalLink size={16} />
                打开登录页
              </Button>
            </div>
          </div>
        </div>
      )}

      {taskLogModal && (
        <div className="modal-backdrop" role="presentation">
          <div
            className="modal-panel task-log-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="task-log-title"
          >
            <div className="modal-header">
              <div>
                <h3 id="task-log-title">任务日志</h3>
                <span>
                  {taskLogModal.title} · {taskLogModal.taskType} · {taskLogModal.taskId}
                </span>
              </div>
              <div className="task-log-header-actions">
                <Button
                  variant="outline"
                  size="icon"
                  className="icon-button"
                  title="刷新任务日志"
                  onClick={() =>
                    void openTaskLogs(taskLogModal.taskType, taskLogModal.taskId, taskLogModal.title)
                  }
                  disabled={taskLogModal.loading}
                >
                  {taskLogModal.loading ? <Loader2 className="spin" size={18} /> : <RefreshCcw size={18} />}
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="icon-button"
                  title="关闭"
                  onClick={() => setTaskLogModal(null)}
                >
                  <XCircle size={18} />
                </Button>
              </div>
            </div>
            <div className="task-log-content">
              {taskLogModal.loading ? (
                <div className="task-log-state">
                  <Loader2 className="spin" size={22} />
                  <span>正在加载任务日志</span>
                </div>
              ) : taskLogModal.error ? (
                <div className="task-log-state error">
                  <AlertTriangle size={22} />
                  <span>{taskLogModal.error}</span>
                </div>
              ) : taskLogModal.page?.events.length ? (
                <div className="task-log-list">
                  {taskLogModal.page.events.map((event) => (
                    <div className="task-log-row" key={event.id}>
                      <div className="task-log-meta">
                        <span>{formatTime(event.timestamp)}</span>
                        <StatusBadge value={taskLogStatus(event.type)} />
                        <strong>{event.type}</strong>
                      </div>
                      <pre>{formatPayload(event.payload)}</pre>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState title="暂无任务日志" detail="该任务还没有写入事件记录" />
              )}
            </div>
          </div>
        </div>
      )}

      {busyAction && (
        <div className="busy-overlay" role="status">
          <Loader2 className="spin" size={18} />
          <span>{busyAction}</span>
        </div>
      )}
    </div>
  );
}
