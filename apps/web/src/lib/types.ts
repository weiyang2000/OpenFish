export type TaskStatus =
  | "queued"
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "stopping"
  | "stopped";

export type ComponentStatus =
  | "unknown"
  | "stopped"
  | "starting"
  | "running"
  | "degraded"
  | "failed"
  | "stopping";

export type ComponentId =
  | "query"
  | "media"
  | "insight"
  | "forum"
  | "report"
  | "mindspider"
  | "database";

export type PlatformId = "xhs" | "dy" | "ks" | "bili" | "wb" | "tieba" | "zhihu";

export type ReportFormat = "html" | "json" | "md" | "pdf";

export type ReportEngineId = "query" | "media" | "insight";

export type RunMode = "topic_extraction" | "deep_sentiment" | "full_workflow";

export type IdentityListType = "allow" | "block";

export type KeywordSource = "manual" | "broad_topic_extraction" | "mixed";

export type CrawlerTaskKeywordSource = "manual";

export type CrawlerAccountStatus =
  | "active"
  | "login_required"
  | "expired"
  | "disabled"
  | "error"
  | "unknown";

export type CrawlerLoginType = "qrcode" | "phone" | "cookie";

export type CrawlerSentiment = "positive" | "neutral" | "negative" | "unknown";

export type LogLevel = "debug" | "info" | "warning" | "error" | "critical";

export type LogSource =
  | "system"
  | "query"
  | "media"
  | "insight"
  | "forum"
  | "report"
  | "mindspider"
  | "crawler";

export interface UserRef {
  userId: string;
  displayName: string;
  role?: "owner" | "operator" | "reviewer" | "service_account";
}

export interface SystemComponent {
  id: ComponentId;
  name: string;
  status: ComponentStatus;
  /**
   * Optional infrastructure listener port. Not a Query/Media/Insight page route.
   */
  port?: number;
  healthUrl?: string;
  outputLines?: number;
  lastHeartbeatAt?: string;
  message?: string;
}

export interface ReportArtifact {
  format: ReportFormat;
  ready: boolean;
  filename?: string;
  sizeBytes?: number;
  downloadUrl?: string;
}

export interface ReportTask {
  id: string;
  workspaceId: string;
  tenantId?: string;
  topic: string;
  status: Exclude<TaskStatus, "stopping" | "stopped">;
  progress: number;
  stage:
    | "queued"
    | "prepare"
    | "io_ready"
    | "orchestrating"
    | "forum_running"
    | "data_loaded"
    | "agent_running"
    | "retry_wait"
    | "persist"
    | "completed"
    | "failed";
  templateId?: string;
  sourceScope?: {
    searchRunId?: string | null;
    crawlerTaskIds?: string[];
    includeForumLog?: boolean;
    inputFileRefs?: string[];
    orchestration?: {
      enabled?: boolean;
      mode?: string;
      status?: string;
      workspacePath?: string;
      rerunEngines?: ReportEngineId[];
      historyEngines?: ReportEngineId[];
      engines?:
        | ReportEngineId[]
        | Partial<Record<ReportEngineId, Record<string, string | undefined>>>;
      forum?: Record<string, string | undefined>;
      startedAt?: string;
      completedAt?: string;
    };
  };
  artifacts: ReportArtifact[];
  owner?: UserRef;
  createdAt: string;
  updatedAt: string;
  errorMessage?: string;
}

export interface CrawlerStats {
  totalKeywords: number;
  totalPlatforms: number;
  totalTasks: number;
  successfulTasks: number;
  failedTasks: number;
  totalNotes: number;
  totalComments: number;
  platformSummary?: Partial<
    Record<
      PlatformId,
      {
        successfulKeywords: number;
        failedKeywords: number;
        totalNotes: number;
        totalComments: number;
        sentiment?: {
          processed?: number;
          updated?: number;
          failed?: number;
          disabled?: boolean;
          error?: string;
          tables?: Record<string, { processed: number; updated: number; failed: number }>;
        };
      }
    >
  >;
}

export interface CrawlerTask {
  id: string;
  workspaceId: string;
  runMode: RunMode;
  status: TaskStatus;
  progress: number;
  strategyId?: string;
  targetDate?: string;
  startDate?: string;
  endDate?: string;
  schedule?: CrawlFrequency;
  crawlDepth: number;
  platforms: PlatformId[];
  keywords: string[];
  keywordSource: CrawlerTaskKeywordSource;
  stats: CrawlerStats;
  owner?: UserRef;
  createdAt: string;
  updatedAt: string;
  errorMessage?: string;
}

export interface CrawlFrequency {
  mode: "manual" | "hourly" | "daily" | "weekly" | "cron";
  cron?: string;
  timezone: string;
}

export interface PlatformPolicy {
  platformId: PlatformId;
  enabled: boolean;
  crawlDepth: number;
  maxKeywords: number;
  maxNotesPerKeyword: number;
  maxCommentsPerNote: number;
  keywords: string[];
  keywordSource: KeywordSource;
  frequency: CrawlFrequency;
  loginType: CrawlerLoginType;
  headless: boolean;
  updatedAt: string;
}

export interface Platform {
  id: PlatformId;
  name: string;
  enabled: boolean;
  crawlerType: "search" | "detail" | "creator";
  policy: PlatformPolicy;
  identityRuleCounts: {
    allow: number;
    block: number;
  };
  accountCounts?: Partial<Record<CrawlerAccountStatus | "loginRequired", number>>;
}

export interface IdentityRule {
  id: string;
  platformId: PlatformId;
  listType: IdentityListType;
  userId: string;
  label?: string;
  reason?: string;
  expiresAt?: string;
  createdAt: string;
  createdBy?: UserRef;
}

export interface ConfigField {
  key: string;
  label: string;
  group: "server" | "database" | "llm" | "search" | "crawler";
  type: "string" | "number" | "boolean" | "enum" | "secret" | "url";
  value: string;
  editable: boolean;
  sensitive: boolean;
  required?: boolean;
  options?: string[];
}

export interface LogLine {
  id: string;
  source: LogSource;
  level: LogLevel;
  timestamp: string;
  message: string;
  taskId?: string;
}

export type TaskLogType = "report" | "crawler";

export interface TaskLogEvent {
  id: string;
  type: string;
  taskId: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface TaskLogPage {
  taskId: string;
  taskType: TaskLogType;
  events: TaskLogEvent[];
}

export interface ReportTemplate {
  id: string;
  name: string;
  filename: string;
  description: string;
  sizeBytes: number;
}

export interface CrawlerStrategy {
  id: string;
  workspaceId: string;
  name: string;
  runMode: RunMode;
  platformPolicies: PlatformPolicy[];
  createdAt: string;
  updatedAt: string;
}

export interface CrawlerAccountDetail {
  scopes?: string[];
  message?: string;
  expiresAt?: string;
  [key: string]: unknown;
}

export interface CrawlerAccount {
  id: string;
  workspaceId: string;
  platformId: PlatformId;
  accountId: string;
  status: CrawlerAccountStatus;
  username?: string;
  displayName?: string;
  avatarUrl?: string;
  profileUrl?: string;
  loginType?: CrawlerLoginType;
  lastLoginAt?: string;
  lastCheckedAt?: string;
  details?: CrawlerAccountDetail;
  createdAt: string;
  updatedAt: string;
}

export type CrawlerAccountLoginSessionStatus =
  | "opening"
  | "waiting"
  | "completed"
  | "failed";

export interface CrawlerAccountLoginSession {
  id: string;
  workspaceId: string;
  platformId: PlatformId;
  loginType: CrawlerLoginType;
  status: CrawlerAccountLoginSessionStatus;
  loginUrl: string;
  message?: string;
  observedStateNames?: string[];
  observedStateCount?: number;
  loginPreviewImage?: string | null;
  loginPreviewKind?: "qrcode" | "page" | null;
  loginPreviewUpdatedAt?: string;
  account?: CrawlerAccount;
  error?: {
    code: string;
    message: string;
  };
  createdAt: string;
  updatedAt: string;
  expiresAt?: string;
}

export interface CrawlerDataRecord {
  id: string;
  platformId: PlatformId;
  contentType: "content" | "comment";
  tableName: string;
  sourceId: string;
  title: string;
  textSnippet: string;
  author?: string;
  keyword?: string;
  url?: string;
  createdAt?: string | number;
  scrapedAt?: string | number;
  sentiment?: CrawlerSentiment;
  metrics?: {
    likes?: string | number;
    comments?: string | number;
  };
}

export interface CrawlerDataSummary {
  totalRecords: number;
  byPlatform: Partial<Record<PlatformId, number>>;
  byType: Partial<Record<"content" | "comment", number>>;
}

export interface PageInfo {
  page: number;
  pageSize: number;
  totalRecords: number;
  totalPages: number;
  hasPreviousPage: boolean;
  hasNextPage: boolean;
}

export interface CrawlerDataPage {
  records: CrawlerDataRecord[];
  summary: CrawlerDataSummary;
  pageInfo: PageInfo;
  source?: string;
  message?: string;
}

export interface ConsoleSnapshot {
  workspaceId: string;
  generatedAt: string;
  mock: boolean;
  components: SystemComponent[];
  reportTasks: ReportTask[];
  reportTemplates: ReportTemplate[];
  crawlerTasks: CrawlerTask[];
  crawlerStrategies: CrawlerStrategy[];
  crawlerAccounts: CrawlerAccount[];
  platforms: Platform[];
  identityRules: IdentityRule[];
  configFields: ConfigField[];
  logs: LogLine[];
}

export interface CreateReportTaskInput {
  topic: string;
  templateId?: string;
  sourceScope?: {
    orchestration?: {
      enabled?: boolean;
      engines: ReportEngineId[];
    };
  };
  outputFormats: ReportFormat[];
  owner: UserRef;
}

export interface RerunReportTaskInput {
  engines: ReportEngineId[];
}

export interface CreateCrawlerTaskInput {
  strategyId?: string;
  runMode?: RunMode;
  targetDate?: string;
  startDate?: string;
  endDate?: string;
  schedule?: CrawlFrequency;
  platforms: PlatformId[];
  keywords: string[];
  crawlDepth?: number;
  keywordSource?: CrawlerTaskKeywordSource;
  maxNotesPerKeyword?: number;
  maxCommentsPerNote?: number;
  loginType?: CrawlerLoginType;
  headless?: boolean;
  owner: UserRef;
}

export interface CreateCrawlerAccountLoginSessionInput {
  platformId: PlatformId;
  loginType: CrawlerLoginType;
  headless?: boolean;
  timeoutSeconds?: number;
}

export interface IdentityRuleInput {
  platformId: PlatformId;
  listType: IdentityListType;
  userId: string;
  label?: string;
  reason?: string;
  createdBy: UserRef;
}
