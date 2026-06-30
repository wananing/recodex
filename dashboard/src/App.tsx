import {
  Activity,
  ArrowRight,
  AtSign,
  CalendarDays,
  CheckCircle2,
  Code2,
  Database,
  Download,
  Eye,
  FileText,
  Filter,
  FolderInput,
  GitGraph,
  History,
  Languages,
  ListChecks,
  Paperclip,
  Pause,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  TerminalSquare,
  XCircle,
  Zap,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  EmptyState,
  Metric,
  SectionHeader,
  SettingLine,
  StatusPill,
} from "@/components/dashboardPrimitives";
import { AppSidebar } from "@/components/AppSidebar";
import { DashboardSelect, DashboardSelectCard } from "@/components/DashboardSelect";
import { getJson, postAction, postJson, type ApiResult, type JsonResult } from "@/lib/recodexClient";
import { LlmSettingsPanel } from "@/components/LlmSettingsPanel";
import {
  artifactOptions,
  conflictOptions,
  navItems,
  panelIds,
  sourceOptions,
  targetOptions,
} from "@/lib/dashboardConfig";
import type {
  ArtifactPreview,
  ArtifactType,
  ConflictPolicy,
  ExportResponse,
  ImprovementRecord,
  MiningReviewPayload,
  PanelId,
  ProjectRecord,
  ProviderAsset,
  ProviderAssetType,
  ProviderRecord,
  SessionRecord,
  SkillTarget,
  SourceType,
  WatchSourceRecord,
  OverviewPayload,
} from "@/lib/dashboardTypes";
import { formatBytes, formatCount, statusClass } from "@/lib/dashboardUtils";
import { LANG_OPTIONS, useI18n, type Lang } from "@/lib/i18n";
import { ReportPage } from "@/pages/ReportPage";
import { MiningReviewPanel } from "@/panels/MiningReviewPanel";
import { ProvidersPanel } from "@/panels/ProvidersPanel";
import { SessionsPanel } from "@/panels/SessionsPanel";
import { TranscriptGraphPage } from "@/pages/TranscriptGraphPage";

type HomeTranscriptEvent = {
  event_id: string;
  event_index: number;
  role: string;
  kind: string;
  phase: string;
  event_type: string;
  created_at: string | null;
  text_excerpt: string;
  user_input_text?: string | null;
};

type HomeSessionGraph = {
  ok: boolean;
  events: HomeTranscriptEvent[];
  tool_calls: Array<{ event_id: string; command?: string; status?: string }>;
  file_refs: Array<{ event_id: string; path?: string; path_role?: string }>;
  error_refs: Array<{ event_id: string; message?: string; error_type?: string }>;
};

type HomeReportRecord = {
  id: string;
  kind: string;
  session_id: string | null;
  project_path: string | null;
  title: string;
  html_path: string | null;
  markdown_path: string | null;
  json_path: string | null;
  created_at: string;
  core_summary?: Record<string, unknown>;
};

type HomeReportContent = {
  id: string;
  content_type: "json" | "markdown" | "md";
  path: string;
  content: string;
};

type HomeReportData = {
  schema_version?: string;
  task_outcome?: Record<string, unknown>;
  cost_ledger?: Record<string, unknown>;
  findings?: unknown;
  improvement_opportunities?: unknown;
  artifact_candidates?: unknown;
  artifact_review_queue?: unknown;
  core_answers?: Record<string, unknown>;
  effect_observation?: Record<string, unknown>;
  meta?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  issues?: unknown;
  evidence?: unknown;
  suggestions?: unknown;
  workflow?: unknown;
  artifacts?: unknown;
  core_diagnostics?: unknown;
  efficiency_analysis?: unknown;
  efficiency_diagnosis?: unknown;
  report_focus?: unknown;
  chat_transcript_analysis?: unknown;
  conversation_analysis?: unknown;
  efficiency_actions?: unknown;
  token_usage?: unknown;
  evidence_audit?: unknown;
};

type HomeAnalysisJob = {
  id: string;
  type: "analysis" | "report";
  status: "queued" | "running" | "succeeded" | "failed";
  phase: string;
  message: string;
  elapsed_ms: number;
  result: unknown;
  error: string | null;
};

type HomeView = "chain" | "analysis" | "audit" | "artifact";

export function App() {
  const { lang, setLang, t } = useI18n();
  const [panel, setPanel] = useState<PanelId>(() => initialPanel());
  const [source, setSource] = useState<SourceType>("codex");
  const [path, setPath] = useState("~/.codex/sessions");
  const [scope, setScope] = useState("local-ai-coding");
  const [skillTarget, setSkillTarget] = useState<SkillTarget>("project");
  const [skillOut, setSkillOut] = useState("");
  const [conflict, setConflict] = useState<ConflictPolicy>("rename");
  const [selectedProviderId, setSelectedProviderId] = useState("codex");
  const [providerAssetType, setProviderAssetType] = useState<ProviderAssetType>("all");
  const [selectedMiningClusterId, setSelectedMiningClusterId] = useState("");
  const [selectedImprovementId, setSelectedImprovementId] = useState("");
  const [workflowSessionId, setWorkflowSessionId] = useState("");
  const [artifactType, setArtifactType] = useState<ArtifactType>("skill");
  const [action, setAction] = useState<ApiResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [catalogProjects, setCatalogProjects] = useState<ProjectRecord[]>([]);
  const [catalogSessions, setCatalogSessions] = useState<SessionRecord[]>([]);
  const [selectedCatalogProject, setSelectedCatalogProject] = useState("");
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [providerAssets, setProviderAssets] = useState<ProviderAsset[]>([]);
  const [miningReview, setMiningReview] = useState<MiningReviewPayload | null>(null);
  const [watchSources, setWatchSources] = useState<WatchSourceRecord[]>([]);
  const [improvements, setImprovements] = useState<ImprovementRecord[]>([]);
  const [artifactPreview, setArtifactPreview] = useState<ArtifactPreview | null>(null);
  const autoCatalogTriggeredRef = useRef(false);

  const activeNavItem = useMemo(
    () => navItems.find((item) => item.id === panel) ?? navItems[0],
    [panel],
  );
  const activeTitle = t(activeNavItem.labelKey);
  const activeHint = t(activeNavItem.hintKey);
  const primaryNavItems = useMemo(
    () => navItems.filter((item) => ["overview", "sessions", "graph", "reports", "evidence", "artifacts"].includes(item.id)),
    [],
  );

  useEffect(() => {
    document.title = `${activeTitle} · recodex`;
  }, [activeTitle]);

  useEffect(() => {
    void loadOverview(false);
    void loadProjects(false);
    void loadCatalogProjects(false);
    void loadSessions(false);
    void loadProviders(false);
    void loadWatchSources(false);
    void loadMiningReview(false);
  }, []);

  useEffect(() => {
    if (!workflowSessionId) {
      const preferredSession = preferredWorkflowSession(sessions);
      if (preferredSession?.session_id) {
        setWorkflowSessionId(preferredSession.session_id);
      }
    }
  }, [sessions, workflowSessionId]);

  useEffect(() => {
    if (!overview || autoCatalogTriggeredRef.current) {
      return;
    }
    if ((overview.catalog_sessions ?? 0) > 0) {
      return;
    }
    autoCatalogTriggeredRef.current = true;
    void runInitialCatalogScan();
  }, [overview]);

  useEffect(() => {
    if (!selectedCatalogProject && catalogProjects[0]?.project_path) {
      setSelectedCatalogProject(catalogProjects[0].project_path);
    }
  }, [catalogProjects, selectedCatalogProject]);

  useEffect(() => {
    setSelectedCatalogProject("");
    void loadCatalogProjects(false);
  }, [source]);

  useEffect(() => {
    if (selectedCatalogProject) {
      void loadCatalogSessions(selectedCatalogProject, false);
    } else {
      setCatalogSessions([]);
    }
  }, [selectedCatalogProject]);

  useEffect(() => {
    if (panel === "providers") {
      void loadProviderAssets(false);
    }
  }, [panel, selectedProviderId, providerAssetType]);

  useEffect(() => {
    if (panel === "evidence") {
      void loadMiningReview(false);
    }
  }, [panel, selectedMiningClusterId]);

  useEffect(() => {
    if (panel === "artifacts" || panel === "skills") {
      void loadImprovements(false);
    }
  }, [panel]);

  async function runAction(key: string, endpoint: string, payload: unknown) {
    setBusy(key);
    const result = await postAction(endpoint, payload);
    setAction(result);
    setBusy(null);
    if (!result.ok) {
      return;
    }
    if (endpoint.startsWith("/catalog/scan")) {
      void loadOverview(false);
      void loadCatalogProjects(false);
    }
    if (endpoint.startsWith("/catalog/import")) {
      void loadOverview(false);
      void loadProjects(false);
      void loadSessions(false);
      void loadCatalogProjects(false);
      if (selectedCatalogProject) {
        void loadCatalogSessions(selectedCatalogProject, false);
      }
    }
    if (endpoint.startsWith("/import")) {
      void loadOverview(false);
      void loadProjects(false);
      void loadSessions(false);
    }
    if (endpoint.startsWith("/watch")) {
      void loadOverview(false);
      void loadWatchSources(false);
    }
    if (endpoint.startsWith("/skills")) {
      void loadImprovements(false);
    }
  }

  async function runJson<T>(
    key: string | null,
    request: () => Promise<JsonResult<T>>,
    onSuccess: (data: T) => void | Promise<void>,
    successMessage: (data: T) => string,
    showBanner = true,
  ) {
    if (key) {
      setBusy(key);
    }
    const result = await request();
    if (key) {
      setBusy(null);
    }
    if (!result.ok) {
      if (showBanner) {
        setAction({ ok: false, message: result.message });
      }
      return;
    }
    await onSuccess(result.data);
    if (showBanner) {
      setAction({ ok: true, message: successMessage(result.data) });
    }
  }

  async function loadOverview(showBanner = true) {
    await runJson<OverviewPayload>(
      showBanner ? "overview-load" : null,
      () => getJson("/overview"),
      setOverview,
      (data) => t("message.loadedOverview", { count: data.sessions }),
      showBanner,
    );
  }

  async function runInitialCatalogScan() {
    setBusy("catalog-auto");
    const initialSources: SourceType[] = ["codex", "claude-code"];
    const results = await Promise.all(
      initialSources.map((initialSource) =>
        postJson<{ ok: boolean; scanned: number; cataloged: number; failed: number }>(
          "/catalog/scan",
          { source: initialSource },
        ),
      ),
    );
    setBusy(null);
    const okResults = results.flatMap((result) => (result.ok ? [result.data] : []));
    if (okResults.length === 0) {
      const failed = results.find((result) => !result.ok);
      setAction({ ok: false, message: failed && !failed.ok ? failed.message : "catalog scan failed" });
      return;
    }
    const cataloged = okResults.reduce((total, result) => total + result.cataloged, 0);
    await loadOverview(false);
    await loadCatalogProjects(false);
    setAction({
      ok: true,
      message: t("message.autoCatalogDone", { count: cataloged, sources: okResults.length }),
    });
  }

  async function loadSessions(showBanner = true) {
    await runJson<{ ok: boolean; sessions: SessionRecord[] }>(
      showBanner ? "sessions-load" : null,
      () => getJson("/sessions"),
      (data) => setSessions(data.sessions ?? []),
      (data) => t("message.loadedSessions", { count: data.sessions.length }),
      showBanner,
    );
  }

  async function loadProjects(showBanner = true) {
    await runJson<{ ok: boolean; projects: ProjectRecord[] }>(
      showBanner ? "projects-load" : null,
      () => getJson("/projects"),
      (data) => setProjects(data.projects ?? []),
      (data) => t("message.loadedProjects", { count: data.projects.length }),
      showBanner,
    );
  }

  async function loadCatalogProjects(showBanner = true) {
    const query = source !== "auto" ? `?source=${encodeURIComponent(source)}` : "";
    await runJson<{ ok: boolean; projects: ProjectRecord[] }>(
      showBanner ? "catalog-projects-load" : null,
      () => getJson(`/catalog/projects${query}`),
      (data) => setCatalogProjects(data.projects ?? []),
      (data) => t("message.loadedCatalogProjects", { count: data.projects.length }),
      showBanner,
    );
  }

  async function loadCatalogSessions(projectPath = selectedCatalogProject, showBanner = true) {
    const params = new URLSearchParams();
    if (projectPath) {
      params.set("project", projectPath);
    }
    if (source !== "auto") {
      params.set("source", source);
    }
    const query = params.toString() ? `?${params.toString()}` : "";
    await runJson<{ ok: boolean; sessions: SessionRecord[] }>(
      showBanner ? "catalog-sessions-load" : null,
      () => getJson(`/catalog/sessions${query}`),
      (data) => setCatalogSessions(data.sessions ?? []),
      (data) => t("message.loadedCatalogSessions", { count: data.sessions.length }),
      showBanner,
    );
  }

  async function loadWatchSources(showBanner = true) {
    await runJson<{ ok: boolean; sources: WatchSourceRecord[] }>(
      showBanner ? "watch-load" : null,
      () => getJson("/watch/status"),
      (data) => setWatchSources(data.sources ?? []),
      (data) => t("message.loadedWatch", { count: data.sources.length }),
      showBanner,
    );
  }

  async function loadProviders(showBanner = true) {
    await runJson<{ ok: boolean; providers: ProviderRecord[] }>(
      showBanner ? "providers-load" : null,
      () => getJson("/providers"),
      (data) => {
        const rows = data.providers ?? [];
        setProviders(rows);
        if (!rows.some((provider) => provider.id === selectedProviderId)) {
          setSelectedProviderId(rows.find((provider) => provider.detected)?.id ?? rows[0]?.id ?? "codex");
        }
      },
      (data) => t("message.loadedProviders", { count: data.providers.length }),
      showBanner,
    );
  }

  async function loadProviderAssets(showBanner = true) {
    const params = new URLSearchParams({ type: providerAssetType });
    await runJson<{ ok: boolean; assets: ProviderAsset[] }>(
      showBanner ? "provider-assets-load" : null,
      () => getJson(`/providers/${encodeURIComponent(selectedProviderId)}/assets?${params.toString()}`),
      (data) => setProviderAssets(data.assets ?? []),
      (data) => t("message.loadedProviderAssets", { count: data.assets.length }),
      showBanner,
    );
  }

  async function loadMiningReview(showBanner = true) {
    const params = new URLSearchParams();
    if (selectedMiningClusterId) {
      params.set("cluster_id", selectedMiningClusterId);
    }
    await runJson<MiningReviewPayload>(
      showBanner ? "mining-review-load" : null,
      () => getJson(`/mining/review${params.toString() ? `?${params.toString()}` : ""}`),
      (data) => {
        setMiningReview(data);
        const selectedId = data.selected_cluster?.cluster_id ?? "";
        if (!selectedMiningClusterId && selectedId) {
          setSelectedMiningClusterId(selectedId);
        }
      },
      (data) => t("message.loadedMiningReview", { count: data.clusters.length }),
      showBanner,
    );
  }

  async function loadImprovements(showBanner = true) {
    await runJson<{ ok: boolean; improvements: ImprovementRecord[] }>(
      showBanner ? "improvements-load" : null,
      () => getJson("/improvements"),
      (data) => {
        setImprovements(data.improvements ?? []);
        if (!selectedImprovementId && data.improvements?.[0]) {
          setSelectedImprovementId(String(data.improvements[0].id));
        }
      },
      (data) => t("message.loadedCandidates", { count: data.improvements.length }),
      showBanner,
    );
  }

  async function setImprovementStatus(id: number, status: "accept" | "reject") {
    await runJson<{ ok: boolean; improvement: ImprovementRecord }>(
      `improvement-${status}-${id}`,
      () => postJson(`/improvements/${id}/${status}`, {}),
      (data) => {
        setImprovements((current) =>
          current.map((item) => (item.id === data.improvement.id ? data.improvement : item)),
        );
        setSelectedImprovementId(String(data.improvement.id));
      },
      (data) => `${data.improvement.status}: ${data.improvement.title}`,
    );
  }

  async function previewArtifact() {
    const params = new URLSearchParams({ type: artifactType });
    if (selectedImprovementId) {
      params.set("improvement_id", selectedImprovementId);
    }
    await runJson<ArtifactPreview>(
      "artifact-preview",
      () => getJson(`/artifacts/preview?${params.toString()}`),
      setArtifactPreview,
      (data) => t("message.previewLoaded", { count: data.files.length }),
    );
  }

  async function exportArtifact() {
    await runJson<ExportResponse>(
      "artifact-export",
      () =>
        postJson("/artifacts/export", {
          type: artifactType,
          improvement_id: selectedImprovementId ? Number(selectedImprovementId) : undefined,
          target: skillTarget,
          out: skillOut || undefined,
          on_conflict: conflict,
        }),
      () => undefined,
      (data) =>
        t("message.exportedFiles", {
          count: data.paths.length,
          path: data.paths[0] ?? data.artifact_type,
        }),
    );
  }

  async function refreshActivePanel() {
    if (panel === "overview") {
      await loadOverview();
      await loadProjects(false);
    } else if (panel === "ingest") {
      await loadWatchSources();
    } else if (panel === "providers") {
      await loadProviders(false);
      await loadProviderAssets();
    } else if (panel === "sessions") {
      await loadProjects(false);
      await loadSessions();
    } else if (panel === "graph") {
      await loadProjects(false);
      await loadSessions();
    } else if (panel === "evidence") {
      await loadMiningReview();
    } else if (panel === "artifacts" || panel === "skills") {
      await loadImprovements();
    }
  }

  function openEvidenceCluster(clusterId: string) {
    setSelectedMiningClusterId(clusterId);
    setPanel("evidence");
  }

  return (
    <div className={panel === "overview" ? "recodex-app codex-home-shell" : "recodex-app"}>
      <AppSidebar activePanel={panel} onPanelChange={setPanel} />

      <div className="workspace">
        <header className="topbar">
          <div>
            <div className="eyebrow">{t("app.dashboard")}</div>
            <h1>{activeTitle}</h1>
            <p className="topbar-copy">{activeHint}</p>
          </div>
          <div className="topbar-actions">
            <StatusPill icon={Database} label={t("status.sqlite")} value={t("status.local")} />
            <div className="language-switch" title={t("app.language")}>
              <Languages className="h-4 w-4" />
              <DashboardSelect
                value={lang}
                options={LANG_OPTIONS}
                onChange={(value) => setLang(value as Lang)}
                size="sm"
                ariaLabel={t("app.language")}
                triggerClassName="language-select-trigger"
              />
            </div>
            <button
              type="button"
              className="icon-command"
              title={t("app.refresh")}
              disabled={busy?.includes("load")}
              onClick={() => void refreshActivePanel()}
            >
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
          <nav className="mobile-nav-strip" aria-label="Mobile primary">
            {primaryNavItems.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={item.id === panel ? "mobile-nav-button active" : "mobile-nav-button"}
                  onClick={() => setPanel(item.id)}
                  title={t(item.hintKey)}
                >
                  <Icon className="h-4 w-4" />
                  <span>{t(item.labelKey)}</span>
                </button>
              );
            })}
          </nav>
        </header>

        {action && (
          <div className={action.ok ? "action-banner ok" : "action-banner error"}>
            {action.ok ? <CheckCircle2 className="h-4 w-4" /> : <Activity className="h-4 w-4" />}
            <span>{action.message}</span>
          </div>
        )}

        <main className="panel-surface">
          {panel === "overview" && (
            <OverviewPanel
              sessions={sessions}
              projects={projects}
              miningReview={miningReview}
              onNavigate={setPanel}
              onOpenCluster={openEvidenceCluster}
              selectedSessionId={workflowSessionId}
              onSessionSelect={setWorkflowSessionId}
            />
          )}
          {panel === "ingest" && (
            <IngestPanel
              source={source}
              path={path}
              scope={scope}
              busy={busy}
              catalogProjects={catalogProjects}
              catalogSessions={catalogSessions}
              selectedCatalogProject={selectedCatalogProject}
              watchSources={watchSources}
              onSourceChange={(nextSource) => {
                setSource(nextSource);
                setPath(defaultSourcePath(nextSource));
              }}
              onPathChange={setPath}
              onScopeChange={setScope}
              onCatalogProjectChange={setSelectedCatalogProject}
              onRefreshCatalog={() => loadCatalogProjects()}
              onRefreshCatalogSessions={() => loadCatalogSessions()}
              onAction={runAction}
            />
          )}
          {panel === "providers" && (
            <ProvidersPanel
              providers={providers}
              assets={providerAssets}
              selectedProviderId={selectedProviderId}
              assetType={providerAssetType}
              busy={busy}
              onProviderChange={setSelectedProviderId}
              onAssetTypeChange={setProviderAssetType}
              onLoadProviders={() => loadProviders()}
              onLoadAssets={() => loadProviderAssets()}
            />
          )}
          {panel === "sessions" && <SessionsPanel sessions={sessions} projects={projects} />}
          {panel === "graph" && (
            <TranscriptGraphPage
              sessions={sessions}
              projects={projects}
              initialSessionId={workflowSessionId}
              onSessionChange={setWorkflowSessionId}
            />
          )}
          {panel === "evidence" && (
            <MiningReviewPanel
              review={miningReview}
              selectedClusterId={selectedMiningClusterId}
              busy={busy}
              onClusterChange={setSelectedMiningClusterId}
              onRefresh={() => loadMiningReview()}
            />
          )}
          {panel === "reports" && (
            <ReportPage
              projects={projects}
            />
          )}
          {panel === "artifacts" && (
            <ArtifactsPanel
              type={artifactType}
              target={skillTarget}
              out={skillOut}
              conflict={conflict}
              selectedImprovementId={selectedImprovementId}
              improvements={improvements}
              preview={artifactPreview}
              busy={busy}
              onTypeChange={setArtifactType}
              onTargetChange={setSkillTarget}
              onOutChange={setSkillOut}
              onConflictChange={setConflict}
              onSelectedImprovementChange={setSelectedImprovementId}
              onLoadImprovements={() => loadImprovements()}
              onPreview={previewArtifact}
              onExport={exportArtifact}
              onSetStatus={setImprovementStatus}
            />
          )}
          {panel === "skills" && (
            <SkillsPanel
              target={skillTarget}
              out={skillOut}
              conflict={conflict}
              busy={busy}
              improvements={improvements}
              onTargetChange={setSkillTarget}
              onOutChange={setSkillOut}
              onConflictChange={setConflict}
              onAction={runAction}
            />
          )}
          {panel === "llm" && <LlmSettingsPanel />}
          {panel === "settings" && <SettingsPanel />}
        </main>
      </div>
    </div>
  );
}

function initialPanel(): PanelId {
  const raw = new URLSearchParams(window.location.search).get("panel");
  return panelIds.includes(raw as PanelId) ? (raw as PanelId) : "overview";
}

function OverviewPanel({
  sessions,
  projects,
  miningReview,
  onNavigate,
  onOpenCluster,
  selectedSessionId,
  onSessionSelect,
}: {
  sessions: SessionRecord[];
  projects: ProjectRecord[];
  miningReview: MiningReviewPayload | null;
  onNavigate: (panel: PanelId) => void;
  onOpenCluster: (clusterId: string) => void;
  selectedSessionId: string;
  onSessionSelect: (sessionId: string) => void;
}) {
  const { t } = useI18n();
  const [projectFilter, setProjectFilter] = useState("all");
  const [since, setSince] = useState("30d");
  const [analysisMode, setAnalysisMode] = useState<"workflow" | "improvements" | "patterns">("workflow");
  const [analysisNote, setAnalysisNote] = useState("");
  const [reportOutputDir, setReportOutputDir] = useState("");
  const [includeLlmReport, setIncludeLlmReport] = useState(true);
  const [homeView, setHomeView] = useState<HomeView>("chain");
  const [graph, setGraph] = useState<HomeSessionGraph | null>(null);
  const [graphBusy, setGraphBusy] = useState(false);
  const [graphError, setGraphError] = useState("");
  const [reports, setReports] = useState<HomeReportRecord[]>([]);
  const [selectedReportId, setSelectedReportId] = useState("");
  const [reportData, setReportData] = useState<HomeReportData | null>(null);
  const [activeJob, setActiveJob] = useState<HomeAnalysisJob | null>(null);
  const [jobNotice, setJobNotice] = useState<{ ok: boolean; message: string } | null>(null);
  const [homeImprovements, setHomeImprovements] = useState<ImprovementRecord[]>([]);
  const [selectedHomeImprovementId, setSelectedHomeImprovementId] = useState("");
  const [artifactPreviewLocal, setArtifactPreviewLocal] = useState<ArtifactPreview | null>(null);
  const [artifactTypeLocal, setArtifactTypeLocal] = useState<ArtifactType>("skill");
  const [artifactBusy, setArtifactBusy] = useState(false);
  const [localClusterId, setLocalClusterId] = useState("");

  const clusters = miningReview?.clusters ?? [];
  const projectSessions = useMemo(
    () => sessions.filter((session) => projectFilter === "all" || sessionProjectPath(session) === projectFilter),
    [projectFilter, sessions],
  );
  const selectedSession =
    projectSessions.find((session) => session.session_id === selectedSessionId) ??
    preferredWorkflowSession(projectSessions) ??
    preferredWorkflowSession(sessions);
  const focusCluster = clusters.find((cluster) => cluster.cluster_id === localClusterId) ?? clusters[0] ?? null;
  const reviewQueue = clusters.slice(0, 3);
  const selectedProject = sessionProjectName(selectedSession);
  const evidenceItems = clusters.reduce(
    (count, cluster) => count + (cluster.card_count ?? cluster.card_ids?.length ?? 0),
    0,
  );
  const graphEvents = graph?.events ?? [];
  const selectedReport = reports.find((report) => report.id === selectedReportId) ?? reports[0] ?? null;
  const summary = homeRecord(reportData?.summary);
  const v2Findings = homeRecords(reportData?.findings);
  const v2Artifacts = homeRecords(reportData?.artifact_candidates);
  const evidenceAudit = homeRecord(reportData?.evidence_audit);
  const auditedObjects = homeRecords(evidenceAudit.audited_objects);
  const reportEvidenceCount = auditedObjects.length + v2Findings.length;
  const acceptedImprovements = homeImprovements.filter((item) => item.status === "accepted");
  const proposedImprovements = homeImprovements.filter((item) => item.status === "proposed");
  const selectedImprovement =
    homeImprovements.find((item) => String(item.id) === selectedHomeImprovementId) ??
    proposedImprovements[0] ??
    acceptedImprovements[0] ??
    homeImprovements[0] ??
    null;
  const readyClusters = clusters.filter((cluster) => cluster.readiness?.includes("ready")).length;
  const highPriorityClusters = clusters.filter((cluster) => (cluster.priority_score ?? 0) >= 18).length;
  const jobRunning = Boolean(activeJob && ["queued", "running"].includes(activeJob.status));
  const hasReport = Boolean(reportData || selectedReport);
  const auditProgress = clusters.length > 0 ? Math.min(100, Math.round((readyClusters / clusters.length) * 100)) : 0;
  const evidenceProgress = clusters.length > 0 || reportEvidenceCount > 0 ? 100 : selectedSession ? 62 : 0;

  useEffect(() => {
    if (projectSessions.length > 0 && !projectSessions.some((session) => session.session_id === selectedSessionId)) {
      onSessionSelect(projectSessions[0].session_id);
    }
  }, [onSessionSelect, projectSessions, selectedSessionId]);

  useEffect(() => {
    if (!selectedSession?.session_id) {
      setGraph(null);
      return;
    }
    void loadSessionGraph(selectedSession.session_id);
  }, [selectedSession?.session_id]);

  useEffect(() => {
    void loadHomeReports(false);
    void loadHomeImprovements(false);
  }, []);

  useEffect(() => {
    if (!selectedSession?.session_id || reports.length === 0) {
      return;
    }
    const nextReport = reports.find((report) => report.session_id === selectedSession.session_id) ?? reports[0];
    if (nextReport?.id && nextReport.id !== selectedReportId) {
      setSelectedReportId(nextReport.id);
      void loadReportData(nextReport.id, false);
    }
  }, [reports, selectedReportId, selectedSession?.session_id]);

  useEffect(() => {
    if (!jobRunning || !activeJob?.id) {
      return;
    }
    const timer = window.setInterval(() => {
      void refreshAnalysisJob(activeJob.id);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [activeJob?.id, jobRunning]);

  useEffect(() => {
    if (!selectedHomeImprovementId && selectedImprovement?.id) {
      setSelectedHomeImprovementId(String(selectedImprovement.id));
    }
  }, [selectedHomeImprovementId, selectedImprovement?.id]);

  async function loadSessionGraph(sessionId: string) {
    setGraphBusy(true);
    setGraphError("");
    const result = await getJson<HomeSessionGraph>(`/transcripts/${encodeURIComponent(sessionId)}/graph`);
    setGraphBusy(false);
    if (!result.ok) {
      setGraphError(result.message);
      setGraph(null);
      return;
    }
    setGraph(result.data);
  }

  async function loadHomeReports(showNotice = true) {
    const result = await getJson<{ ok: boolean; reports: HomeReportRecord[] }>("/reports");
    if (!result.ok) {
      if (showNotice) {
        setJobNotice({ ok: false, message: result.message });
      }
      return;
    }
    const rows = result.data.reports ?? [];
    setReports(rows);
    if (showNotice) {
      setJobNotice({ ok: true, message: t("message.reportsLoaded", { count: rows.length }) });
    }
  }

  async function loadReportData(reportId: string, showNotice = true) {
    const result = await getJson<HomeReportContent>(`/reports/${encodeURIComponent(reportId)}/json`);
    if (!result.ok) {
      if (showNotice) {
        setJobNotice({ ok: false, message: result.message });
      }
      setReportData(null);
      return;
    }
    try {
      const parsed = JSON.parse(result.data.content) as HomeReportData;
      setReportData(parsed);
      if (showNotice) {
        setJobNotice({ ok: true, message: t("message.analysisReportDone", { path: result.data.path }) });
      }
    } catch (error) {
      setJobNotice({ ok: false, message: error instanceof Error ? error.message : "invalid report json" });
    }
  }

  async function loadHomeImprovements(showNotice = false) {
    const result = await getJson<{ ok: boolean; improvements: ImprovementRecord[] }>("/improvements");
    if (!result.ok) {
      if (showNotice) {
        setJobNotice({ ok: false, message: result.message });
      }
      return;
    }
    const rows = result.data.improvements ?? [];
    setHomeImprovements(rows);
    if (!selectedHomeImprovementId && rows[0]) {
      setSelectedHomeImprovementId(String(rows[0].id));
    }
    if (showNotice) {
      setJobNotice({ ok: true, message: t("message.loadedCandidates", { count: rows.length }) });
    }
  }

  async function startHomeAnalysis() {
    if (!selectedSession?.session_id) {
      setJobNotice({ ok: false, message: t("common.noSessions") });
      setHomeView("chain");
      return;
    }
    setHomeView("analysis");
    setJobNotice(null);
    const payload = {
      type: "analysis",
      mode: analysisMode,
      target: selectedSession.session_id,
      project: projectFilter === "all" ? undefined : projectFilter,
      since,
      note: analysisNote.trim() || undefined,
    };
    const result = await postJson<{ ok: boolean; job: HomeAnalysisJob }>("/analysis/jobs", payload);
    if (!result.ok) {
      setJobNotice({ ok: false, message: result.message });
      return;
    }
    setActiveJob(result.data.job);
    setJobNotice({ ok: true, message: result.data.job.message });
    if (homeJobTerminal(result.data.job)) {
      await finishHomeJob(result.data.job);
    }
  }

  async function startHomeReport() {
    if (!selectedSession?.session_id) {
      setJobNotice({ ok: false, message: t("common.noSessions") });
      setHomeView("chain");
      return;
    }
    setHomeView("analysis");
    setJobNotice(null);
    const payload = {
      type: "report",
      target: selectedSession.session_id,
      project: projectFilter === "all" ? undefined : projectFilter,
      reports_dir: reportOutputDir.trim() || undefined,
      include_llm: includeLlmReport,
    };
    const result = await postJson<{ ok: boolean; job: HomeAnalysisJob }>("/analysis/jobs", payload);
    if (!result.ok) {
      setJobNotice({ ok: false, message: result.message });
      return;
    }
    setActiveJob(result.data.job);
    setJobNotice({ ok: true, message: result.data.job.message });
    if (homeJobTerminal(result.data.job)) {
      await finishHomeJob(result.data.job);
    }
  }

  async function refreshAnalysisJob(jobId: string) {
    const result = await getJson<{ ok: boolean; job: HomeAnalysisJob }>(`/analysis/jobs/${encodeURIComponent(jobId)}`);
    if (!result.ok) {
      setActiveJob((current) =>
        current?.id === jobId
          ? { ...current, status: "failed", phase: "error", message: result.message, error: result.message }
          : current,
      );
      setJobNotice({ ok: false, message: result.message });
      return;
    }
    setActiveJob(result.data.job);
    if (homeJobTerminal(result.data.job)) {
      await finishHomeJob(result.data.job);
    }
  }

  async function finishHomeJob(job: HomeAnalysisJob) {
    if (job.status === "failed") {
      setJobNotice({ ok: false, message: job.error || job.message });
      return;
    }
    const result = homeRecord(job.result);
    const report = homeRecord(result.report) as Partial<HomeReportRecord>;
    if (typeof report.id === "string") {
      const nextReport = report as HomeReportRecord;
      setReports((current) => [nextReport, ...current.filter((item) => item.id !== nextReport.id)]);
      setSelectedReportId(nextReport.id);
      await loadReportData(nextReport.id, false);
    } else {
      await loadHomeReports(false);
    }
    await loadHomeImprovements(false);
    setJobNotice({
      ok: true,
      message: job.message || t("message.analysisReportDone", { path: homeText(report.json_path ?? result.mode, "analysis") }),
    });
  }

  async function updateImprovementStatus(id: number, status: "accept" | "reject") {
    const result = await postJson<{ ok: boolean; improvement: ImprovementRecord }>(`/improvements/${id}/${status}`, {});
    if (!result.ok) {
      setJobNotice({ ok: false, message: result.message });
      return;
    }
    setHomeImprovements((current) =>
      current.map((item) => (item.id === result.data.improvement.id ? result.data.improvement : item)),
    );
    setSelectedHomeImprovementId(String(result.data.improvement.id));
    setJobNotice({ ok: true, message: `${result.data.improvement.status}: ${result.data.improvement.title}` });
  }

  async function previewHomeArtifact() {
    if (!selectedImprovement?.id) {
      setJobNotice({ ok: false, message: t("common.noCandidates") });
      return;
    }
    setArtifactBusy(true);
    const params = new URLSearchParams({
      type: artifactTypeLocal,
      improvement_id: String(selectedImprovement.id),
    });
    const result = await getJson<ArtifactPreview>(`/artifacts/preview?${params.toString()}`);
    setArtifactBusy(false);
    if (!result.ok) {
      setJobNotice({ ok: false, message: result.message });
      return;
    }
    setArtifactPreviewLocal(result.data);
    setHomeView("artifact");
    setJobNotice({ ok: true, message: t("message.previewLoaded", { count: result.data.files.length }) });
  }

  const workflowStages: Array<{
    key: string;
    title: string;
    desc: string;
    view: HomeView;
    icon: typeof FolderInput;
    progress: number;
    status: "complete" | "active" | "pending";
    meta: string;
  }> = [
    {
      key: "session",
      title: "Select Session",
      desc: selectedSession?.title || t("overview.simple.step.session.desc"),
      view: "chain",
      icon: CheckCircle2,
      progress: selectedSession ? 100 : 0,
      status: selectedSession ? "complete" : "active",
      meta: selectedProject,
    },
    {
      key: "analyze",
      title: "Analyze",
      desc: activeJob?.message || t("overview.simple.step.report.desc"),
      view: "analysis",
      icon: Activity,
      progress: activeJob?.status === "succeeded" ? 100 : jobRunning ? 58 : selectedSession ? 35 : 0,
      status: activeJob?.status === "succeeded" ? "complete" : selectedSession ? "active" : "pending",
      meta: activeJob ? activeJob.phase : analysisMode,
    },
    {
      key: "report",
      title: "Report",
      desc: homeText(summary.headline, selectedReport?.title || t("common.noReports")),
      view: "analysis",
      icon: FileText,
      progress: hasReport ? 100 : activeJob?.status === "succeeded" ? 72 : 0,
      status: hasReport ? "complete" : activeJob ? "active" : "pending",
      meta: selectedReport?.created_at ?? "waiting",
    },
    {
      key: "evidence",
      title: "Evidence Cards",
      desc: `${formatCount(evidenceItems + reportEvidenceCount)} evidence / ${formatCount(clusters.length)} clusters`,
      view: "audit",
      icon: Sparkles,
      progress: evidenceProgress,
      status: clusters.length > 0 || reportEvidenceCount > 0 ? "complete" : selectedSession ? "active" : "pending",
      meta: `${formatCount(auditedObjects.length)} audited`,
    },
    {
      key: "assets",
      title: "Generate Assets",
      desc: t("overview.simple.step.artifact.desc"),
      view: "artifact",
      icon: ShieldCheck,
      progress: acceptedImprovements.length > 0 || artifactPreviewLocal ? 100 : proposedImprovements.length > 0 || readyClusters > 0 ? 48 : 0,
      status: acceptedImprovements.length > 0 || artifactPreviewLocal ? "complete" : proposedImprovements.length > 0 || readyClusters > 0 ? "active" : "pending",
      meta: acceptedImprovements.length > 0 ? `${formatCount(acceptedImprovements.length)} accepted` : `${formatCount(proposedImprovements.length)} candidates`,
    },
  ];
  const metricRows = [
    {
      label: "Current Session",
      value: selectedSession ? "1" : "0",
      trend: selectedSession?.title || "import sessions first",
      icon: GitGraph,
      tone: "",
    },
    {
      label: "Chain Events",
      value: formatCount(graphEvents.length),
      trend: `${formatCount(graph?.tool_calls.length ?? 0)} tools / ${formatCount(graph?.error_refs.length ?? 0)} errors`,
      icon: FileText,
      tone: "blue",
    },
    {
      label: "Report Findings",
      value: formatCount(v2Findings.length),
      trend: `${formatCount(reportEvidenceCount)} v2 evidence / ${formatCount(v2Artifacts.length)} artifacts`,
      icon: ShieldCheck,
      tone: "blue",
    },
    {
      label: "Artifacts Ready",
      value: formatCount(acceptedImprovements.length + readyClusters),
      trend: `${formatCount(proposedImprovements.length)} candidates`,
      icon: Download,
      tone: "",
    },
  ];
  const evidenceRows = reviewQueue.length > 0
    ? reviewQueue.map((cluster, index) => ({
        id: cluster.cluster_id,
        kind: index === 0 ? "USER CORRECTION" : index === 1 ? "VALIDATION GAP" : "WORKFLOW PATTERN",
        severity: (cluster.priority_score ?? 0) >= 24 ? "High" : (cluster.priority_score ?? 0) >= 18 ? "Medium" : "Info",
        title: cluster.title,
        desc: cluster.common_pattern,
        meta: `${cluster.frequency}x · ${cluster.readiness}`,
        icon: index === 0 ? History : index === 1 ? XCircle : GitGraph,
        tone: index === 0 ? "green" : index === 1 ? "orange" : "blue",
        onClick: () => onOpenCluster(cluster.cluster_id),
      }))
    : [
        {
          id: "empty-session",
          kind: "SESSION",
          severity: selectedSession ? "Info" : "Waiting",
          title: selectedSession?.title || t("common.noSessions"),
          desc: selectedSession ? `${selectedProject} · ${selectedSession.session_id}` : t("overview.simple.importFirst"),
          meta: selectedSession?.updated_at ?? t("common.never"),
          icon: History,
          tone: "blue",
          onClick: () => onNavigate("sessions"),
        },
      ];
  return (
    <div className="content-stack codex-home">
      <div className="codex-workspace-grid">
        <section className="codex-workspace-main">
          <section className="codex-prompt-card" aria-label="Analysis prompt">
            <Sparkles className="h-5 w-5 codex-prompt-lead" />
            <input
              className="codex-prompt-input"
              type="text"
              value={analysisNote}
              onChange={(event) => setAnalysisNote(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  void startHomeAnalysis();
                }
              }}
              placeholder="What should we analyze next?"
              aria-label="What should we analyze next?"
            />
            <div className="codex-prompt-tools">
              <button type="button" className="codex-tool-icon" aria-label="Refresh chain" onClick={() => selectedSession && void loadSessionGraph(selectedSession.session_id)}>
                <Paperclip className="h-4 w-4" />
              </button>
              <button type="button" className="codex-tool-icon" aria-label="Mention source" onClick={() => onNavigate("providers")}>
                <AtSign className="h-4 w-4" />
              </button>
              <span className="codex-shortcut"><span>⌘</span><span>↵</span></span>
            </div>
          </section>

          <div className="codex-selector-row">
            <DashboardSelectCard
              value={projectFilter}
              options={[
                { value: "all", label: `全部项目 (${sessions.length})` },
                ...projects.map((project) => ({
                  value: project.project_path,
                  label: `${project.project_name} (${project.session_count})`,
                })),
              ]}
              onChange={setProjectFilter}
              label="Project"
              icon={<FolderInput className="h-4 w-4" />}
              ariaLabel="Project"
              placeholder="选择项目"
            />
            <DashboardSelectCard
              value={selectedSession?.session_id ?? ""}
              options={projectSessions.map((session) => ({
                value: session.session_id,
                label: session.title || session.session_id,
              }))}
              onChange={onSessionSelect}
              label="Session"
              icon={<History className="h-4 w-4" />}
              ariaLabel="Session"
              placeholder={sessions.length === 0 ? "未导入" : "选择会话"}
            />
            <DashboardSelectCard
              value={since}
              options={[
                { value: "7d", label: "Last 7 days" },
                { value: "30d", label: "Last 30 days" },
                { value: "90d", label: "Last 90 days" },
                { value: "365d", label: "Last year" },
              ]}
              onChange={setSince}
              label="Time Range"
              icon={<CalendarDays className="h-4 w-4" />}
              ariaLabel="Time Range"
            />
            <DashboardSelectCard
              value={analysisMode}
              options={[
                { value: "workflow", label: "Workflow Audit" },
                { value: "improvements", label: "Improvements" },
                { value: "patterns", label: "Patterns" },
              ]}
              onChange={(value) => setAnalysisMode(value as typeof analysisMode)}
              label="Analysis Mode"
              icon={<Target className="h-4 w-4" />}
              ariaLabel="Analysis Mode"
            />
          </div>

          <section className="codex-home-report-runner" aria-label="报告生成">
            <div>
              <FileText className="h-4 w-4" />
              <span>v2 报告生成</span>
              <strong>{includeLlmReport ? "LLM + rules + deep audit" : "local rules"}</strong>
            </div>
            <label>
              <span>输出目录</span>
              <input value={reportOutputDir} onChange={(event) => setReportOutputDir(event.target.value)} placeholder="./reports" />
            </label>
            <label className="codex-home-toggle">
              <input type="checkbox" checked={includeLlmReport} onChange={(event) => setIncludeLlmReport(event.target.checked)} />
              <span>LLM 增强</span>
            </label>
          </section>

          <div className="codex-action-row">
            <button
              type="button"
              className="codex-action-button primary"
              disabled={jobRunning || sessions.length === 0}
              onClick={() => void startHomeReport()}
            >
              {jobRunning && activeJob?.type === "report" ? <RefreshCw className="h-4 w-4 spin" /> : <FileText className="h-4 w-4" />}
              生成 v2 报告
            </button>
            <button
              type="button"
              className="codex-action-button"
              disabled={jobRunning || sessions.length === 0}
              onClick={() => void startHomeAnalysis()}
            >
              {jobRunning && activeJob?.type === "analysis" ? <RefreshCw className="h-4 w-4 spin" /> : <Play className="h-4 w-4" />}
              运行辅助分析
            </button>
            <button type="button" className="codex-action-button" onClick={() => setHomeView("audit")}>
              <ListChecks className="h-4 w-4" />
              Review Queue
            </button>
            <button type="button" className="codex-action-button" onClick={() => void previewHomeArtifact()}>
              <FileText className="h-4 w-4" />
              Preview Artifact
            </button>
            <button type="button" className="codex-action-button" onClick={() => void loadHomeReports()}>
              <RefreshCw className="h-4 w-4" />
              Refresh Reports
            </button>
          </div>

          {jobNotice?.message && (
            <div className={jobNotice.ok ? "home-workbench-notice ok" : "home-workbench-notice error"}>
              {jobNotice.ok ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
              <span>{jobNotice.message}</span>
            </div>
          )}

          <section className="codex-metrics-card" aria-label="Analysis metrics">
            {metricRows.map((metric) => {
              const Icon = metric.icon;
              return (
                <article className="codex-metric" key={metric.label}>
                  <div className="codex-metric-label">{metric.label}</div>
                  <div className="codex-metric-value">{metric.value}</div>
                  <div className="codex-metric-trend">{metric.trend}</div>
                  <div className={`codex-metric-icon ${metric.tone}`}>
                    <Icon className="h-4 w-4" />
                  </div>
                </article>
              );
            })}
          </section>

          <section className="codex-workflow-card" aria-label={t("overview.simple.flow")}>
            <div className="codex-workflow-header">
              <div className="codex-workflow-title-group">
                <h2>Analysis Workflow</h2>
                <span><span className="status-dot ok" />Live Pipeline</span>
              </div>
              <div className="codex-workflow-controls">
                <span>Auto-advance</span>
                <button type="button" className="codex-toggle" aria-label="Toggle auto advance"><span /></button>
                <button type="button" className="codex-detail-button" onClick={() => onNavigate("graph")}>View Details</button>
              </div>
            </div>

            <div className="codex-workflow-track">
              {workflowStages.map((stage, index) => {
                const Icon = stage.icon;
                return (
                  <div className="codex-track-fragment" key={stage.key}>
                    <button
                      type="button"
                      className={`codex-workflow-step ${stage.status}`}
                      onClick={() => setHomeView(stage.view)}
                    >
                      <div className="codex-step-heading">
                        <span className={`codex-step-icon ${stage.status}`}>
                          <Icon className="h-3.5 w-3.5" />
                        </span>
                        <span className="codex-step-title">{stage.title}</span>
                        <span className={`codex-step-status ${stage.status}`} />
                      </div>
                      <div className="codex-step-copy">
                        <div>{stage.meta}</div>
                        <div>{stage.desc}</div>
                      </div>
                      <div className="codex-progress-row">
                        <span><i style={{ width: `${stage.progress}%` }} /></span>
                        <b>{stage.progress}%</b>
                      </div>
                    </button>
                    {index < workflowStages.length - 1 && <div className="codex-connector" />}
                  </div>
                );
              })}
            </div>

            <div className="codex-workflow-summary">
              <div><span>Evidence mined</span><strong>{formatCount(evidenceItems)}</strong></div>
              <div><span>High priority</span><strong className="green">{formatCount(highPriorityClusters)}</strong></div>
              <div><span>Needs review</span><strong className="orange">{formatCount(clusters.length)}</strong></div>
              <div><span>Reports</span><strong className="blue">{formatCount(reports.length)}</strong></div>
              <div className="estimate"><span>Selected</span><strong>{selectedSession ? "ready" : "waiting"}</strong></div>
            </div>
          </section>

          <HomeWorkbenchDetail
            view={homeView}
            selectedSession={selectedSession}
            selectedProject={selectedProject}
            graph={graph}
            graphBusy={graphBusy}
            graphError={graphError}
            reportData={reportData}
            activeJob={activeJob}
            jobRunning={jobRunning}
            clusters={clusters}
            focusCluster={focusCluster}
            miningReview={miningReview}
            homeImprovements={homeImprovements}
            selectedImprovement={selectedImprovement}
            artifactPreview={artifactPreviewLocal}
            artifactType={artifactTypeLocal}
            artifactBusy={artifactBusy}
            onViewChange={setHomeView}
            onRefreshGraph={() => {
              if (selectedSession) {
                return loadSessionGraph(selectedSession.session_id);
              }
              return undefined;
            }}
            onRunAnalysis={startHomeAnalysis}
            onSelectCluster={(clusterId) => setLocalClusterId(clusterId)}
            onOpenCluster={onOpenCluster}
            onRefreshImprovements={() => loadHomeImprovements(true)}
            onImprovementChange={setSelectedHomeImprovementId}
            onSetImprovementStatus={updateImprovementStatus}
            onArtifactTypeChange={setArtifactTypeLocal}
            onPreviewArtifact={previewHomeArtifact}
          />
        </section>

        <aside className="codex-evidence-panel" aria-label="Recent evidence">
          <div className="codex-evidence-header">
            <h2>Recent Evidence</h2>
            <button type="button" onClick={() => onNavigate("evidence")}>View all</button>
          </div>
          <div className="codex-evidence-list">
            {evidenceRows.map((item) => {
              const Icon = item.icon;
              return (
                <button key={item.id} type="button" className="codex-evidence-card" onClick={item.onClick}>
                  <span className={`codex-evidence-icon ${item.tone}`}>
                    <Icon className="h-4 w-4" />
                  </span>
                  <span className="codex-evidence-body">
                    <span className="codex-evidence-topline">
                      <span>{item.kind}</span>
                      <b className={item.severity.toLowerCase()}>{item.severity}</b>
                    </span>
                    <strong>{item.title}</strong>
                    <small>{item.desc}</small>
                    <em>{item.meta}</em>
                  </span>
                </button>
              );
            })}
          </div>
          {focusCluster && (
            <button type="button" className="codex-evidence-library-button" onClick={() => onOpenCluster(focusCluster.cluster_id)}>
              Open Evidence Library
              <ArrowRight className="h-4 w-4" />
            </button>
          )}
        </aside>
      </div>

      <section className="codex-console-panel" aria-label="Analysis console">
        <div className="codex-console-header">
          <div><TerminalSquare className="h-4 w-4" /><span>Analysis Console</span><span className="codex-console-live"><span className="status-dot ok" />Live</span></div>
          <div>
            <button type="button">Clear</button>
            <button type="button"><Filter className="h-4 w-4" />Filter</button>
            <button type="button" aria-label="Pause console"><Pause className="h-4 w-4" /></button>
          </div>
        </div>
        <div className="codex-terminal" role="log" aria-live="polite">
          <div><span>10:42:11</span><b>INFO</b><code>Session loaded: {selectedSession?.session_id ?? "waiting for session"}</code></div>
          <div><span>10:42:12</span><b>INFO</b><code>Project scope: {selectedProject}</code></div>
          <div><span>10:42:13</span><b>INFO</b><code>Analysis mode: Workflow Audit</code></div>
          <div><span>10:42:18</span><b className="ok">OK</b><code>Canonical events indexed from local history</code></div>
          <div><span>10:42:27</span><b>INFO</b><code>Building evidence windows and micro claims</code></div>
          <div><span>10:42:35</span><b className="ok">OK</b><code>Clusters available: {formatCount(clusters.length)} / Evidence: {formatCount(evidenceItems)}</code></div>
          <div><span>10:42:42</span><b>INFO</b><code>Audit queue: {formatCount(highPriorityClusters)} high priority patterns</code></div>
        </div>
      </section>
    </div>
  );
}

function HomeWorkbenchDetail({
  view,
  selectedSession,
  selectedProject,
  graph,
  graphBusy,
  graphError,
  reportData,
  activeJob,
  jobRunning,
  clusters,
  focusCluster,
  miningReview,
  homeImprovements,
  selectedImprovement,
  artifactPreview,
  artifactType,
  artifactBusy,
  onViewChange,
  onRefreshGraph,
  onRunAnalysis,
  onSelectCluster,
  onOpenCluster,
  onRefreshImprovements,
  onImprovementChange,
  onSetImprovementStatus,
  onArtifactTypeChange,
  onPreviewArtifact,
}: {
  view: HomeView;
  selectedSession: SessionRecord | null;
  selectedProject: string;
  graph: HomeSessionGraph | null;
  graphBusy: boolean;
  graphError: string;
  reportData: HomeReportData | null;
  activeJob: HomeAnalysisJob | null;
  jobRunning: boolean;
  clusters: NonNullable<MiningReviewPayload["clusters"]>;
  focusCluster: NonNullable<MiningReviewPayload["clusters"]>[number] | null;
  miningReview: MiningReviewPayload | null;
  homeImprovements: ImprovementRecord[];
  selectedImprovement: ImprovementRecord | null;
  artifactPreview: ArtifactPreview | null;
  artifactType: ArtifactType;
  artifactBusy: boolean;
  onViewChange: (view: HomeView) => void;
  onRefreshGraph: () => Promise<void> | void;
  onRunAnalysis: () => Promise<void> | void;
  onSelectCluster: (clusterId: string) => void;
  onOpenCluster: (clusterId: string) => void;
  onRefreshImprovements: () => Promise<void> | void;
  onImprovementChange: (id: string) => void;
  onSetImprovementStatus: (id: number, status: "accept" | "reject") => Promise<void> | void;
  onArtifactTypeChange: (type: ArtifactType) => void;
  onPreviewArtifact: () => Promise<void> | void;
}) {
  const evidenceAudit = homeRecord(reportData?.evidence_audit);
  const auditMetrics = homeRecord(evidenceAudit.metrics);
  const auditProblems = homeRecords(evidenceAudit.problems);
  const auditObjects = homeRecords(evidenceAudit.audited_objects);
  const cardsForCluster = focusCluster
    ? (miningReview?.cards ?? []).filter((card) => focusCluster.card_ids.includes(card.card_id))
    : [];

  return (
    <section className="home-workbench-panel">
      <div className="home-workbench-tabs">
        {[
          ["chain", "链路"],
          ["analysis", "分析"],
          ["audit", "审计"],
          ["artifact", "产物"],
        ].map(([id, label]) => (
          <button key={id} type="button" className={view === id ? "active" : ""} onClick={() => onViewChange(id as HomeView)}>
            {label}
          </button>
        ))}
      </div>

      {view === "chain" && (
        <div className="home-workbench-section">
          <div className="home-section-heading">
            <div>
              <h3>{selectedSession?.title ?? "未选择会话"}</h3>
              <p>{selectedProject} / {selectedSession?.session_id ?? "waiting"}</p>
            </div>
            <button type="button" className="codex-detail-button" disabled={graphBusy || !selectedSession} onClick={() => void onRefreshGraph()}>
              <RefreshCw className={graphBusy ? "h-4 w-4 spin" : "h-4 w-4"} />
              刷新链路
            </button>
          </div>
          {graphError && <div className="home-inline-error">{graphError}</div>}
          <div className="home-chain-stats">
            <span><strong>{formatCount(graph?.events.length ?? 0)}</strong> events</span>
            <span><strong>{formatCount(graph?.tool_calls.length ?? 0)}</strong> tools</span>
            <span><strong>{formatCount(graph?.file_refs.length ?? 0)}</strong> files</span>
            <span><strong>{formatCount(graph?.error_refs.length ?? 0)}</strong> errors</span>
          </div>
          <div className="home-chain-list">
            {(graph?.events ?? []).slice(0, 10).map((event) => (
              <article key={event.event_id} className="home-chain-event">
                <span>{event.event_index}</span>
                <div>
                  <strong>{event.role} / {event.kind}</strong>
                  <p>{event.user_input_text || event.text_excerpt}</p>
                </div>
                <em>{event.phase || event.event_type}</em>
              </article>
            ))}
            {!graphBusy && (graph?.events.length ?? 0) === 0 && <EmptyState label="当前会话还没有可展示链路。" />}
          </div>
        </div>
      )}

      {view === "analysis" && (
        <div className="home-workbench-section">
          <div className="home-section-heading">
            <div>
              <h3>{activeJob?.message ?? "Workflow analysis"}</h3>
              <p>{activeJob ? `${activeJob.status} / ${activeJob.phase} / ${Math.round((activeJob.elapsed_ms ?? 0) / 1000)}s` : "ready"}</p>
            </div>
            <button type="button" className="codex-action-button primary" disabled={jobRunning || !selectedSession} onClick={() => void onRunAnalysis()}>
              {jobRunning ? <RefreshCw className="h-4 w-4 spin" /> : <Play className="h-4 w-4" />}
              Run
            </button>
          </div>
          <div className="home-job-card">
            <div><span>Status</span><strong>{activeJob?.status ?? "idle"}</strong></div>
            <div><span>Phase</span><strong>{activeJob?.phase ?? "not started"}</strong></div>
            <div><span>Job</span><strong>{activeJob?.id ?? "none"}</strong></div>
          </div>
          <div className="home-job-log">
            <div><span>target</span><code>{selectedSession?.session_id ?? "no session"}</code></div>
            <div><span>message</span><code>{activeJob?.message ?? "waiting for run"}</code></div>
            {activeJob?.error && <div><span>error</span><code>{activeJob.error}</code></div>}
          </div>
        </div>
      )}

      {view === "audit" && (
        <div className="home-workbench-section">
          <div className="home-section-heading">
            <div>
              <h3>{homeText(evidenceAudit.status, focusCluster?.title ?? "暂无审计结果")}</h3>
              <p>{homeText(evidenceAudit.summary, focusCluster?.common_pattern ?? "等待 deep 报告或 evidence mining 结果")}</p>
            </div>
            {focusCluster && (
              <button type="button" className="codex-detail-button" onClick={() => onOpenCluster(focusCluster.cluster_id)}>
                打开审计页
              </button>
            )}
          </div>
          <div className="home-evidence-audit-card">
            {Object.keys(evidenceAudit).length > 0 ? (
              <>
                <div className="home-evidence-audit-stats">
                  <span><strong>{homeText(evidenceAudit.status, "unknown")}</strong> status</span>
                  <span><strong>{homeText(auditMetrics.traceability, "0")}</strong> traceability</span>
                  <span><strong>{formatCount(homeNumber(auditMetrics.audited_claims))}</strong> audited</span>
                  <span><strong>{formatCount(homeNumber(auditMetrics.problem_count))}</strong> problems</span>
                </div>
                <div className="home-evidence-audit-body">
                  <div>
                    <b>审计问题</b>
                    {auditProblems.length > 0 ? (
                      auditProblems.slice(0, 4).map((problem, index) => (
                        <p key={`${homeText(problem.code, "problem")}-${index}`}>
                          {homeText(problem.target, "unknown")} · {homeText(problem.message, "")}
                        </p>
                      ))
                    ) : (
                      <p>未发现证据引用断链。</p>
                    )}
                  </div>
                  <div>
                    <b>可追溯对象</b>
                    {auditObjects.slice(0, 4).map((item, index) => (
                      <p key={`${homeText(item.target, "audit")}-${index}`}>
                        {homeText(item.kind, "object")} · {homeText(item.title || item.id, "untitled")} · {homeText(item.status, "unknown")}
                      </p>
                    ))}
                    {auditObjects.length === 0 && <p>暂无审计对象。</p>}
                  </div>
                </div>
              </>
            ) : (
              <EmptyState label="当前报告没有 evidence_audit。请在 Dashboard 生成新报告，或使用 recodex --deep 生成 deep 报告。" />
            )}
          </div>
          <div className="home-audit-grid">
            <div className="home-cluster-list">
              {clusters.slice(0, 8).map((cluster) => (
                <button key={cluster.cluster_id} type="button" className={cluster.cluster_id === focusCluster?.cluster_id ? "active" : ""} onClick={() => onSelectCluster(cluster.cluster_id)}>
                  <span>{cluster.priority_score}</span>
                  <strong>{cluster.title}</strong>
                  <em>{cluster.frequency}x / {cluster.readiness}</em>
                </button>
              ))}
              {clusters.length === 0 && <EmptyState label="还没有 evidence cluster。" />}
            </div>
            <div className="home-card-list">
              {(cardsForCluster.length > 0 ? cardsForCluster : miningReview?.cards?.slice(0, 4) ?? []).map((card) => (
                <article key={card.card_id}>
                  <span>{card.card_type} / {card.candidate_destination}</span>
                  <strong>{card.title}</strong>
                  <p>{card.observed_fact}</p>
                </article>
              ))}
              {(!miningReview?.cards || miningReview.cards.length === 0) && <EmptyState label="还没有 analysis cards。" />}
            </div>
          </div>
        </div>
      )}

      {view === "artifact" && (
        <div className="home-workbench-section">
          <div className="home-section-heading">
            <div>
              <h3>{selectedImprovement?.title ?? "暂无候选产物"}</h3>
              <p>{selectedImprovement ? `${selectedImprovement.mechanism} / ${selectedImprovement.status}` : "waiting"}</p>
            </div>
            <div className="home-section-actions">
              <DashboardSelect
                value={artifactType}
                options={artifactOptions}
                onChange={(value) => onArtifactTypeChange(value as ArtifactType)}
                size="sm"
                ariaLabel="选择产物类型"
              />
              <button type="button" className="codex-detail-button" disabled={artifactBusy || !selectedImprovement} onClick={() => void onPreviewArtifact()}>
                {artifactBusy ? <RefreshCw className="h-4 w-4 spin" /> : <Eye className="h-4 w-4" />}
                预览
              </button>
            </div>
          </div>
          <div className="home-artifact-grid">
            <div className="home-improvement-list">
              <button type="button" className="home-refresh-row" onClick={() => void onRefreshImprovements()}>
                <RefreshCw className="h-4 w-4" /> 刷新候选
              </button>
              {homeImprovements.slice(0, 8).map((improvement) => (
                <article key={improvement.id} className={selectedImprovement?.id === improvement.id ? "active" : ""} onClick={() => onImprovementChange(String(improvement.id))}>
                  <span>{improvement.mechanism} / {improvement.status}</span>
                  <strong>{improvement.title}</strong>
                  <p>{improvement.recommendation}</p>
                  <div>
                    <button type="button" onClick={(event) => { event.stopPropagation(); void onSetImprovementStatus(improvement.id, "accept"); }}>确认</button>
                    <button type="button" onClick={(event) => { event.stopPropagation(); void onSetImprovementStatus(improvement.id, "reject"); }}>拒绝</button>
                  </div>
                </article>
              ))}
              {homeImprovements.length === 0 && <EmptyState label="还没有 improvement candidates。" />}
            </div>
            <div className="home-preview-box">
              {artifactPreview?.files?.[0] ? (
                <>
                  <strong>{artifactPreview.files[0].path}</strong>
                  <pre>{artifactPreview.files[0].content}</pre>
                </>
              ) : (
                <EmptyState label="选择候选并点击预览。" />
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function homeRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function homeRecords(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item));
  }
  const record = homeRecord(value);
  return Object.keys(record).length > 0 ? [record] : [];
}

function homeText(value: unknown, fallback = "unknown"): string {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (Array.isArray(value)) {
    const items = value.map((item) => homeText(item, "")).filter(Boolean);
    return items.length > 0 ? items.join(", ") : fallback;
  }
  if (typeof value === "object" && value !== null) {
    return JSON.stringify(value);
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return fallback;
}

function homeNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

function homeJobTerminal(job: HomeAnalysisJob): boolean {
  return job.status === "succeeded" || job.status === "failed";
}

function sessionProjectPath(session: SessionRecord | null | undefined): string {
  return session?.project_path || "(unknown)";
}

function IngestPanel({
  source,
  path,
  scope,
  busy,
  catalogProjects,
  catalogSessions,
  selectedCatalogProject,
  watchSources,
  onSourceChange,
  onPathChange,
  onScopeChange,
  onCatalogProjectChange,
  onRefreshCatalog,
  onRefreshCatalogSessions,
  onAction,
}: {
  source: SourceType;
  path: string;
  scope: string;
  busy: string | null;
  catalogProjects: ProjectRecord[];
  catalogSessions: SessionRecord[];
  selectedCatalogProject: string;
  watchSources: WatchSourceRecord[];
  onSourceChange: (value: SourceType) => void;
  onPathChange: (value: string) => void;
  onScopeChange: (value: string) => void;
  onCatalogProjectChange: (value: string) => void;
  onRefreshCatalog: () => Promise<void>;
  onRefreshCatalogSessions: () => Promise<void>;
  onAction: (key: string, endpoint: string, payload: unknown) => Promise<void>;
}) {
  const { t } = useI18n();
  const payload = { source, path, scope };
  const selectedProject = catalogProjects.find((project) => project.project_path === selectedCatalogProject);
  const importProjectPayload = { source, project: selectedCatalogProject };
  return (
    <div className="content-stack">
      <section className="work-panel">
        <SectionHeader title={t("ingest.catalog")} action={t("ingest.catalog.action")} />
        <div className="form-grid">
          <div className="field">
            <span>{t("common.source")}</span>
            <DashboardSelect
              value={source}
              options={sourceOptions.map((option) => ({ value: option, label: option }))}
              onChange={(value) => onSourceChange(value as SourceType)}
              ariaLabel={t("common.source")}
            />
          </div>
          <label className="field wide">
            <span>{t("common.path")}</span>
            <input value={path} onChange={(event) => onPathChange(event.target.value)} />
          </label>
          <label className="field">
            <span>{t("common.scope")}</span>
            <input value={scope} onChange={(event) => onScopeChange(event.target.value)} />
          </label>
        </div>
        <div className="command-row">
          <button
            type="button"
            className="primary-command"
            disabled={busy === "catalog-scan"}
            onClick={() => void onAction("catalog-scan", "/catalog/scan", payload)}
          >
            <Search className="h-4 w-4" />
            {t("ingest.scanCatalog")}
          </button>
          <button
            type="button"
            className="secondary-command"
            disabled={busy === "watch-add"}
            onClick={() => void onAction("watch-add", "/watch/add", payload)}
          >
            <FolderInput className="h-4 w-4" />
            {t("ingest.addWatch")}
          </button>
          <button
            type="button"
            className="secondary-command"
            disabled={busy === "import"}
            onClick={() => void onAction("import", "/import/run", payload)}
          >
            <Download className="h-4 w-4" />
            {t("ingest.fullImport")}
          </button>
        </div>
      </section>

      <section className="work-panel">
        <SectionHeader title={t("ingest.catalogProjects")} action={t("ingest.catalogProjects.action")} />
        <div className="form-grid">
          <div className="field wide">
            <span>{t("common.project")}</span>
            <DashboardSelect
              value={selectedCatalogProject}
              options={catalogProjects.map((project) => ({
                value: project.project_path,
                label: `${project.project_name} (${project.session_count})`,
              }))}
              onChange={onCatalogProjectChange}
              ariaLabel={t("common.project")}
              placeholder={t("ingest.noCatalogProjects")}
            />
          </div>
          <label className="field">
            <span>{t("sessions.count")}</span>
            <input readOnly value={selectedProject ? `${selectedProject.session_count} / ${formatBytes(selectedProject.total_bytes ?? 0)}` : "-"} />
          </label>
        </div>
        <div className="command-row">
          <button
            type="button"
            className="primary-command"
            disabled={!selectedCatalogProject || busy === "catalog-import"}
            onClick={() => void onAction("catalog-import", "/catalog/import", importProjectPayload)}
          >
            <Download className="h-4 w-4" />
            {t("ingest.importProject")}
          </button>
          <button
            type="button"
            className="secondary-command"
            disabled={busy === "catalog-projects-load"}
            onClick={() => void onRefreshCatalog()}
          >
            <RefreshCw className="h-4 w-4" />
            {t("common.refresh")}
          </button>
        </div>
        <div className="data-table">
          <div className="table-row head">
            <span>{t("common.project")}</span>
            <span>{t("sessions.count")}</span>
            <span>{t("common.size")}</span>
            <span>{t("common.updated")}</span>
          </div>
          {catalogProjects.length > 0 ? (
            catalogProjects.map((project) => (
              <button
                type="button"
                className={project.project_path === selectedCatalogProject ? "table-row selectable active" : "table-row selectable"}
                key={project.project_id}
                onClick={() => onCatalogProjectChange(project.project_path)}
              >
                <span title={project.project_path}>{project.project_name}</span>
                <span>{project.session_count}</span>
                <span>{formatBytes(project.total_bytes ?? 0)}</span>
                <span>{project.latest_at ?? t("common.never")}</span>
              </button>
            ))
          ) : (
            <div className="table-empty">{t("ingest.noCatalogProjects")}</div>
          )}
        </div>
      </section>

      <section className="work-panel">
        <SectionHeader title={t("ingest.catalogSessions")} action={t("ingest.catalogSessions.action")} />
        <div className="data-table">
          <div className="table-row head">
            <span>{t("report.session")}</span>
            <span>{t("common.status")}</span>
            <span>{t("common.size")}</span>
            <span>{t("common.updated")}</span>
          </div>
          {catalogSessions.length > 0 ? (
            catalogSessions.slice(0, 12).map((session) => (
              <div className="table-row" key={session.session_id}>
                <span title={session.source_path ?? session.session_id}>{session.title || session.session_id}</span>
                <span>{session.imported ? t("ingest.imported") : t("ingest.catalogOnly")}</span>
                <span>{formatBytes(session.file_size ?? 0)}</span>
                <span>{session.updated_at ?? t("common.never")}</span>
              </div>
            ))
          ) : (
            <div className="table-empty">{t("ingest.noCatalogSessions")}</div>
          )}
        </div>
        <div className="command-row">
          <button
            type="button"
            className="secondary-command"
            disabled={!selectedCatalogProject || busy === "catalog-sessions-load"}
            onClick={() => void onRefreshCatalogSessions()}
          >
            <RefreshCw className="h-4 w-4" />
            {t("common.refresh")}
          </button>
        </div>
      </section>

      <section className="work-panel">
        <SectionHeader title={t("ingest.watch")} action={t("ingest.watch.action")} />
        <div className="data-table">
          <div className="table-row head">
            <span>{t("common.source")}</span>
            <span>{t("common.path")}</span>
            <span>{t("ingest.lastSync")}</span>
            <span>{t("ingest.counts")}</span>
          </div>
          {watchSources.length > 0 ? (
            watchSources.map((item) => (
              <div className="table-row" key={item.id}>
                <span>
                  <span className={item.enabled ? "status-dot ok" : "status-dot muted"} />
                  {item.source}
                </span>
                <span title={item.path}>{item.path}</span>
                <span>{item.last_sync_at ?? t("common.never")}</span>
                <span>
                  {item.last_imported}/{item.last_skipped}/{item.last_failed}
                </span>
              </div>
            ))
          ) : (
            <div className="table-empty">{t("ingest.noWatch")}</div>
          )}
        </div>
        <div className="command-row">
          <button
            type="button"
            className="primary-command"
            disabled={busy === "watch-run"}
            onClick={() => void onAction("watch-run", "/watch/run", { enabled: true })}
          >
            <Play className="h-4 w-4" />
            {t("ingest.runSync")}
          </button>
        </div>
      </section>
    </div>
  );
}

function ArtifactsPanel({
  type,
  target,
  out,
  conflict,
  selectedImprovementId,
  improvements,
  preview,
  busy,
  onTypeChange,
  onTargetChange,
  onOutChange,
  onConflictChange,
  onSelectedImprovementChange,
  onLoadImprovements,
  onPreview,
  onExport,
  onSetStatus,
}: {
  type: ArtifactType;
  target: SkillTarget;
  out: string;
  conflict: ConflictPolicy;
  selectedImprovementId: string;
  improvements: ImprovementRecord[];
  preview: ArtifactPreview | null;
  busy: string | null;
  onTypeChange: (value: ArtifactType) => void;
  onTargetChange: (value: SkillTarget) => void;
  onOutChange: (value: string) => void;
  onConflictChange: (value: ConflictPolicy) => void;
  onSelectedImprovementChange: (value: string) => void;
  onLoadImprovements: () => Promise<void>;
  onPreview: () => Promise<void>;
  onExport: () => Promise<void>;
  onSetStatus: (id: number, status: "accept" | "reject") => Promise<void>;
}) {
  const { t } = useI18n();
  return (
    <div className="content-stack">
      <section className="work-panel">
        <SectionHeader title={t("artifacts.preview")} action={t("artifacts.preview.action")} />
        <div className="form-grid artifact-form">
          <div className="field">
            <span>{t("artifacts.artifact")}</span>
            <DashboardSelect
              value={type}
              options={artifactOptions}
              onChange={(value) => onTypeChange(value as ArtifactType)}
              ariaLabel={t("artifacts.artifact")}
            />
          </div>
          <div className="field wide">
            <span>{t("common.candidate")}</span>
            <DashboardSelect
              value={selectedImprovementId}
              options={improvements.map((item) => ({
                value: String(item.id),
                label: `#${item.id} / ${item.status} / ${item.title}`,
              }))}
              onChange={onSelectedImprovementChange}
              ariaLabel={t("common.candidate")}
              placeholder={t("artifacts.acceptedQueue")}
            />
          </div>
          <div className="field">
            <span>{t("common.conflict")}</span>
            <DashboardSelect
              value={conflict}
              options={conflictOptions.map((option) => ({ value: option, label: option }))}
              onChange={(value) => onConflictChange(value as ConflictPolicy)}
              ariaLabel={t("common.conflict")}
            />
          </div>
          <div className="field">
            <span>{t("common.target")}</span>
            <DashboardSelect
              value={target}
              options={targetOptions.map((option) => ({ value: option, label: option }))}
              onChange={(value) => onTargetChange(value as SkillTarget)}
              ariaLabel={t("common.target")}
            />
          </div>
          <label className="field wide">
            <span>{t("common.path")}</span>
            <input value={out} onChange={(event) => onOutChange(event.target.value)} placeholder={type === "skill" ? t("artifacts.pathPlaceholderSkill") : t("artifacts.pathPlaceholder")} />
          </label>
        </div>
        <div className="command-row">
          <button
            type="button"
            className="primary-command"
            disabled={busy === "artifact-preview"}
            onClick={() => void onPreview()}
          >
            <Eye className="h-4 w-4" />
            {t("common.preview")}
          </button>
          <button
            type="button"
            className="secondary-command"
            disabled={busy === "artifact-export"}
            onClick={() => void onExport()}
          >
            <Download className="h-4 w-4" />
            {t("common.export")}
          </button>
          <button
            type="button"
            className="secondary-command"
            disabled={busy === "improvements-load"}
            onClick={() => void onLoadImprovements()}
          >
            <RefreshCw className="h-4 w-4" />
            {t("artifacts.refreshCandidates")}
          </button>
        </div>
      </section>

      <section className="split-grid artifact-grid">
        <div className="work-panel">
          <SectionHeader title={t("artifacts.candidates")} action={t("artifacts.candidates.action")} />
          <div className="record-list">
            {improvements.length > 0 ? (
              improvements.map((item) => (
                <article className={String(item.id) === selectedImprovementId ? "record-item active" : "record-item"} key={item.id}>
                  <div>
                    <div className="line-title">#{item.id} {item.title}</div>
                    <div className="line-subtitle">{item.mechanism} / {item.session_id ?? t("common.global")}</div>
                    <p className="record-copy">{item.recommendation}</p>
                  </div>
                  <div className="record-actions stacked">
                    <span className={`badge ${statusClass(item.status)}`}>{item.status}</span>
                    <button
                      type="button"
                      className="secondary-command small"
                      onClick={() => onSelectedImprovementChange(String(item.id))}
                    >
                      {t("common.select")}
                    </button>
                    <button
                      type="button"
                      className="primary-command small"
                      disabled={busy === `improvement-accept-${item.id}`}
                      onClick={() => void onSetStatus(item.id, "accept")}
                    >
                      <CheckCircle2 className="h-4 w-4" />
                      {t("common.accept")}
                    </button>
                    <button
                      type="button"
                      className="danger-command small"
                      disabled={busy === `improvement-reject-${item.id}`}
                      onClick={() => void onSetStatus(item.id, "reject")}
                    >
                      <XCircle className="h-4 w-4" />
                      {t("common.reject")}
                    </button>
                  </div>
                </article>
              ))
            ) : (
              <EmptyState label={t("artifacts.emptyCandidates")} />
            )}
          </div>
        </div>

        <div className="work-panel">
          <SectionHeader title={t("artifacts.generated")} action={preview?.artifact_type ?? t("artifacts.generated.action")} />
          <ArtifactPreviewView preview={preview} />
        </div>
      </section>
    </div>
  );
}

function SkillsPanel({
  target,
  out,
  conflict,
  busy,
  improvements,
  onTargetChange,
  onOutChange,
  onConflictChange,
  onAction,
}: {
  target: SkillTarget;
  out: string;
  conflict: ConflictPolicy;
  busy: string | null;
  improvements: ImprovementRecord[];
  onTargetChange: (value: SkillTarget) => void;
  onOutChange: (value: string) => void;
  onConflictChange: (value: ConflictPolicy) => void;
  onAction: (key: string, endpoint: string, payload: unknown) => Promise<void>;
}) {
  const { t } = useI18n();
  const payload = { target, out: target === "custom" ? out : undefined, on_conflict: conflict };
  const rows = improvements.length > 0 ? improvements : [];
  return (
    <div className="content-stack">
      <section className="work-panel">
        <SectionHeader title={t("skills.export")} action={t("skills.export.action")} />
        <div className="form-grid">
          <div className="field">
            <span>{t("common.target")}</span>
            <DashboardSelect
              value={target}
              options={targetOptions.map((option) => ({ value: option, label: option }))}
              onChange={(value) => onTargetChange(value as SkillTarget)}
              ariaLabel={t("common.target")}
            />
          </div>
          <div className="field">
            <span>{t("common.conflict")}</span>
            <DashboardSelect
              value={conflict}
              options={conflictOptions.map((option) => ({ value: option, label: option }))}
              onChange={(value) => onConflictChange(value as ConflictPolicy)}
              ariaLabel={t("common.conflict")}
            />
          </div>
          <label className="field wide">
            <span>{t("skills.customPath")}</span>
            <input
              value={out}
              disabled={target !== "custom"}
              onChange={(event) => onOutChange(event.target.value)}
              placeholder="~/.codex/skills"
            />
          </label>
        </div>
        <div className="command-row">
          <button
            type="button"
            className="primary-command"
            disabled={busy === "skills-export"}
            onClick={() => void onAction("skills-export", "/skills/export", payload)}
          >
            <Download className="h-4 w-4" />
            {t("skills.exportAccepted")}
          </button>
        </div>
      </section>

      <section className="work-panel">
        <SectionHeader title={t("skills.candidates")} action={t("skills.candidates.action")} />
        <div className="data-table">
          <div className="table-row head">
            <span>{t("common.candidate")}</span>
            <span>{t("common.status")}</span>
            <span>{t("common.session")}</span>
            <span>{t("common.mechanism")}</span>
          </div>
          {rows.length > 0 ? (
            rows.map((row) => (
              <div className="table-row" key={row.id}>
                <span title={row.title}>{row.title}</span>
                <span className={`badge ${statusClass(row.status)}`}>{row.status}</span>
                <span>{row.session_id ?? t("common.global")}</span>
                <span>{row.mechanism}</span>
              </div>
            ))
          ) : (
            <div className="table-empty">{t("common.noCandidates")}</div>
          )}
        </div>
      </section>
    </div>
  );
}

function SettingsPanel() {
  const { t } = useI18n();
  return (
    <div className="content-stack">
      <section className="work-panel">
        <SectionHeader title={t("settings.runtime")} action={t("settings.runtime.action")} />
        <div className="settings-grid">
          <SettingLine label={t("settings.apiBase")} value={import.meta.env.VITE_RECODEX_API_BASE ?? t("settings.sameOrigin")} />
          <SettingLine label={t("settings.defaultSkillTarget")} value="project -> ./.agents/skills" />
          <SettingLine label={t("settings.codexSkillTarget")} value="$CODEX_HOME/skills or ~/.codex/skills" />
          <SettingLine label={t("settings.reportFormats")} value="HTML, Markdown, JSON" />
          <SettingLine label={t("settings.artifactFormats")} value="SKILL.md, Markdown, AGENTS patch, checklist, CI rule" />
        </div>
      </section>
      <section className="work-panel">
        <SectionHeader title={t("settings.cli")} action={t("settings.cli.action")} />
        <div className="terminal-block">
          <TerminalSquare className="h-4 w-4" />
          <code>PYTHONPATH=src python3 -m recodex report latest</code>
        </div>
        <div className="terminal-block">
          <TerminalSquare className="h-4 w-4" />
          <code>PYTHONPATH=src python3 -m recodex export skills --target codex</code>
        </div>
      </section>
    </div>
  );
}

function ArtifactPreviewView({ preview }: { preview: ArtifactPreview | null }) {
  const { t } = useI18n();
  if (!preview) {
    return <EmptyState label={t("artifacts.emptyPreview")} />;
  }
  return (
    <div className="artifact-files">
      {preview.files.map((file) => (
        <article className="file-preview" key={file.path}>
          <div className="file-header">
            <Code2 className="h-4 w-4" />
            <span>{file.path}</span>
          </div>
          <pre className="code-preview compact">{file.content}</pre>
        </article>
      ))}
    </div>
  );
}

function SessionMiniRow({ session }: { session: SessionRecord }) {
  const { t } = useI18n();
  return (
    <div className="run-row session-mini">
      <span>{session.source ?? t("common.unknown")}</span>
      <strong>{session.command_count}</strong>
      <span>{session.error_count} {t("sessions.err")}</span>
      <span>{session.updated_at ?? t("common.unknown")}</span>
      <span>{session.session_id}</span>
    </div>
  );
}

function sessionProjectName(session: SessionRecord | null | undefined): string {
  if (!session) {
    return "(unknown)";
  }
  if (session.project_name) {
    return session.project_name;
  }
  const path = session.project_path || "";
  return path.split("/").filter(Boolean).pop() || path || "(unknown)";
}

function preferredWorkflowSession(sessions: SessionRecord[]): SessionRecord | null {
  return sessions.find((session) => !looksLikeContextOnlySession(session)) ?? sessions[0] ?? null;
}

function looksLikeContextOnlySession(session: SessionRecord): boolean {
  const title = (session.title || "").trim().toLowerCase();
  return title.includes("agents.md instructions") || title === "description" || title.startsWith("# agents.md");
}

function defaultSourcePath(source: SourceType): string {
  if (source === "claude-code") {
    return "~/.claude/projects";
  }
  if (source === "cursor") {
    return "~/.cursor";
  }
  return "~/.codex/sessions";
}
