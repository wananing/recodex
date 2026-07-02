import {
  CheckCircle2,
  Filter,
  FileText,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DashboardSelect } from "@/components/DashboardSelect";
import { useI18n } from "@/lib/i18n";
import { getJson } from "@/lib/recodexClient";

type ReportKindFilter = "all" | "session" | "workflow";
type ReportArtifactFilter = "all" | "with_artifacts" | "needs_review" | "without_artifacts";

type ReportFilters = {
  project: string;
  kind: ReportKindFilter;
  artifact: ReportArtifactFilter;
  query: string;
};

type ReportRecord = {
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

type ReportContent = {
  id: string;
  content_type: "json" | "markdown" | "md";
  path: string;
  content: string;
};

type ReportData = {
  schema_version?: string;
  task_outcome?: Record<string, unknown>;
  cost_ledger?: Record<string, unknown>;
  findings?: unknown;
  improvement_opportunities?: unknown;
  artifact_candidates?: unknown;
  artifact_review_queue?: unknown;
  core_answers?: Record<string, unknown>;
  effect_observation?: Record<string, unknown>;
  evidence_audit?: unknown;
  meta?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  efficiency_analysis?: unknown;
  efficiency_diagnosis?: unknown;
  report_focus?: unknown;
  llm_retro?: unknown;
  chat_transcript_analysis?: unknown;
  user_efficiency_analysis?: unknown;
  conversation_analysis?: unknown;
  efficiency_actions?: unknown;
  token_usage?: unknown;
};

type ProjectRecord = {
  project_id: string;
  project_path: string;
  project_name: string;
  session_count: number;
  command_count: number;
  error_count: number;
  latest_at: string | null;
  sources: string[];
};

export function ReportPage({
  projects = [],
}: {
  projects?: ProjectRecord[];
}) {
  const { t } = useI18n();
  const [reports, setReports] = useState<ReportRecord[]>([]);
  const [selectedReportId, setSelectedReportId] = useState("");
  const [reportData, setReportData] = useState<ReportData | null>(null);
  const [reportPath, setReportPath] = useState("");
  const [reportView, setReportView] = useState<"list" | "detail">(() =>
    new URLSearchParams(window.location.search).get("id") ? "detail" : "list",
  );
  const [selectedProject, setSelectedProject] = useState("all");
  const [reportQuery, setReportQuery] = useState("");
  const [reportKindFilter, setReportKindFilter] = useState<ReportKindFilter>("all");
  const [reportArtifactFilter, setReportArtifactFilter] = useState<ReportArtifactFilter>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);

  const visibleReports = useMemo(
    () =>
      filterReportRecords(reports, {
        project: selectedProject,
        kind: reportKindFilter,
        artifact: reportArtifactFilter,
        query: reportQuery,
      }),
    [reportArtifactFilter, reportKindFilter, reportQuery, reports, selectedProject],
  );
  const selectedReport = useMemo(
    () => reports.find((report) => report.id === selectedReportId) ?? null,
    [reports, selectedReportId],
  );
  const reportFiltersActive =
    selectedProject !== "all" ||
    reportKindFilter !== "all" ||
    reportArtifactFilter !== "all" ||
    reportQuery.trim().length > 0;

  useEffect(() => {
    void loadReports(false);
  }, []);

  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("id");
    if (initial) {
      setSelectedReportId(initial);
      setReportView("detail");
    }
  }, []);

  useEffect(() => {
    if (reportView !== "detail" || !selectedReport?.id) {
      return;
    }
    void loadReportData(selectedReport.id, false);
  }, [reportView, selectedReport?.id]);

  useEffect(() => {
    if (reports.length === 0) {
      setReportData(null);
      setReportPath("");
      return;
    }
    if (reportView === "detail" && selectedReportId && !reports.some((report) => report.id === selectedReportId)) {
      setReportView("list");
      setSelectedReportId("");
    }
  }, [reportView, reports, selectedReportId]);

  async function run<T>(
    key: string,
    request: () => Promise<{ ok: true; data: T } | { ok: false; message: string }>,
    onSuccess: (data: T) => void | Promise<void>,
    message: (data: T) => string,
  ) {
    setBusy(key);
    const result = await request();
    setBusy(null);
    if (!result.ok) {
      setNotice({ ok: false, message: result.message });
      return;
    }
    try {
      await onSuccess(result.data);
    } catch (error) {
      setNotice({ ok: false, message: error instanceof Error ? error.message : "request failed" });
      return;
    }
    setNotice({ ok: true, message: message(result.data) });
  }

  async function loadReports(showNotice = true) {
    await run<{ ok: boolean; reports: ReportRecord[] }>(
      "reports",
      () => getJson("/reports"),
      (data) => {
        setReports(data.reports ?? []);
        const initial = new URLSearchParams(window.location.search).get("id");
        const next = initial || selectedReportId || "";
        if (next) {
          setSelectedReportId(next);
        }
        if (initial) {
          setReportView("detail");
        }
      },
      (data) => (showNotice ? t("message.reportsLoaded", { count: data.reports.length }) : ""),
    );
    if (!showNotice) {
      setNotice(null);
    }
  }

  async function loadReportData(reportId: string, showNotice = true) {
    await run<ReportContent>(
      "report-json",
      () => getJson(`/reports/${encodeURIComponent(reportId)}/json`),
      (data) => {
        setReportPath(data.path);
        setReportData(JSON.parse(data.content) as ReportData);
      },
      (data) => (showNotice ? t("message.analysisReportDone", { path: data.path }) : ""),
    );
    if (!showNotice) {
      setNotice(null);
    }
  }

  function resetReportFilters() {
    setSelectedProject("all");
    setReportKindFilter("all");
    setReportArtifactFilter("all");
    setReportQuery("");
  }

  function openReport(reportId: string) {
    setSelectedReportId(reportId);
    setReportView("detail");
    const params = new URLSearchParams(window.location.search);
    params.set("panel", "reports");
    params.set("id", reportId);
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  }

  function showReportList() {
    setReportView("list");
    const params = new URLSearchParams(window.location.search);
    params.set("panel", "reports");
    params.delete("id");
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  }

  const meta = reportData?.meta ?? {};
  const summary = reportData?.summary ?? {};
  const metrics = reportData?.metrics ?? {};
  const taskOutcome = asRecord(reportData?.task_outcome);
  const costLedger = asRecord(reportData?.cost_ledger);
  const v2Answers = asRecord(reportData?.core_answers);
  const effectObservation = asRecord(reportData?.effect_observation);
  const evidenceAudit = asRecord(reportData?.evidence_audit);
  const auditMetrics = asRecord(evidenceAudit.metrics);
  const v2Findings = asRecords(reportData?.findings);
  const v2Opportunities = asRecords(reportData?.improvement_opportunities);
  const v2ArtifactCandidates = asRecords(reportData?.artifact_candidates);
  const reportFocus = asRecord(reportData?.report_focus);
  const reportFocusArtifacts = asRecords(reportFocus.recommended_artifacts);
  const allArtifactCandidates = [...reportFocusArtifacts, ...v2ArtifactCandidates];
  const chatTranscriptAnalysis = asRecord(reportData?.chat_transcript_analysis);
  const userEfficiencyAnalysis = asRecord(reportData?.user_efficiency_analysis);
  const efficiencyActions = asRecords(reportData?.efficiency_actions);
  const userEfficiencyGuidance = asRecords(userEfficiencyAnalysis.top_guidance);
  const guidanceCards = userEfficiencyGuidance.length > 0 ? userEfficiencyGuidance : efficiencyActions;
  const chatTranscriptMethod = asRecord(chatTranscriptAnalysis.method);
  const chatTranscriptObservations = firstTextArray(chatTranscriptAnalysis.key_observations);
  const chatTranscriptFrictionPoints = firstTextArray(chatTranscriptAnalysis.friction_points);
  const chatTranscriptSample = asRecords(chatTranscriptAnalysis.transcript_sample);
  const effectSuccessIndicators = firstTextArray(effectObservation.success_indicators);
  const conversationAnalysis = asRecords(reportData?.conversation_analysis);
  const artifactReviewQueue = asRecords(reportData?.artifact_review_queue);
  const topV2Opportunity = v2Opportunities[0] ?? {};
  const topV2Artifact = v2ArtifactCandidates[0] ?? {};
  const v2Mechanisms = firstTextArray(
    reportFocusArtifacts.map((item) => item.mechanism),
    v2Opportunities.map((item) => item.recommended_mechanism),
    allArtifactCandidates.map((item) => item.mechanism),
  );
  const maxAvoidableCost = text(
    v2Answers.most_expensive_avoidable_cost ?? summary.max_avoidable_cost,
    "未发现明显可避免成本",
  );
  const primaryCause = text(
    reportFocus.primary_cause ?? v2Answers.why_it_happened ?? summary.primary_cause ?? topV2Opportunity.cause,
    "证据不足，暂不推断根因。",
  );
  const primaryImprovement = text(
    reportFocus.primary_improvement ?? v2Answers.highest_leverage_change ?? summary.primary_improvement ?? topV2Opportunity.title,
    "继续积累报告证据后再沉淀改进。",
  );
  const primaryMechanism = v2Mechanisms[0] || text(topV2Opportunity.recommended_mechanism, "");
  const costRows = reportCostRows(costLedger, metrics);
  const reportFocusTitle = text(
    reportFocus.title ?? summary.top_focus ?? summary.headline,
    "本次报告结论",
  );
  const overviewMetrics = [
    {
      label: "可节省成本",
      value: formatNumber(costRows.reduce((total, item) => total + item.value, 0)),
      note: "报告能直接看到的浪费",
    },
    {
      label: "证据检查",
      value: friendlyStatus(text(evidenceAudit.status, "pending")),
      note: text(auditMetrics.traceability, "等待检查"),
    },
    {
      label: "建议动作",
      value: formatNumber(guidanceCards.length || v2Opportunities.length),
      note: "优先照这个顺序处理",
    },
    {
      label: "沉淀建议",
      value: formatNumber(allArtifactCandidates.length),
      note: `${formatNumber(artifactReviewQueue.length)} 条待确认`,
    },
    {
      label: "问题证据",
      value: formatNumber(v2Findings.length),
      note: "可回溯到聊天和成本",
    },
  ];
  const actionPlanCards = guidanceCards.slice(0, 3);
  const fallbackActionCards = v2Opportunities.slice(0, 3);
  const displayActionCards = actionPlanCards.length > 0 ? actionPlanCards : fallbackActionCards;
  const reportStatusRows = [
    {
      label: "最大可避免成本",
      value: maxAvoidableCost,
      note: "报告核心结论",
    },
    {
      label: "后续效果",
      value: friendlyStatus(text(effectObservation.status ?? v2Answers.has_effect_been_observed, "not observed")),
      note: text(effectObservation.message, "等待后续会话"),
    },
  ];
  const reportTitle = text(summary.title ?? summary.report_title, "recodex AI 编程效率剖析报告");
  const reportProject = text(meta.project ?? selectedReport?.project_path ?? (selectedProject !== "all" ? selectedProject : ""), "未选择项目");
  const reportSessionId = text(meta.session_id ?? selectedReport?.session_id, "latest");
  const reportGeneratedAt = text(meta.started_at ?? meta.generated_at ?? selectedReport?.created_at, "未生成");
  const reportMode = text(meta.analysis_mode, "llm+rules+deep-audit");
  const v2MechanismGroups = groupCount(allArtifactCandidates.map((artifact) => text(artifact.mechanism, "review")));
  const reviewableArtifacts = allArtifactCandidates.filter((artifact) => {
    const status = text(artifact.status, "proposed").toLowerCase();
    return !["accepted", "rejected", "applied"].includes(status);
  });
  const selectedReviewArtifact = reportFocusArtifacts[0] ?? reviewableArtifacts[0] ?? topV2Artifact;
  const processSteps = reportProcessSteps(summary, taskOutcome, costLedger, evidenceAudit, selectedReviewArtifact);
  const routeCards = reportRouteCards(allArtifactCandidates, v2MechanismGroups, primaryMechanism);
  const verificationRows = reportVerificationRows(taskOutcome, evidenceAudit, selectedReport, artifactReviewQueue, reviewableArtifacts);
  const contextRows = reportContextRows(v2Opportunities, v2ArtifactCandidates);
  const preserveRows = reportPreserveRows(summary, evidenceAudit, selectedReport);
  const hasSkillCandidate = routeCards.some((item) => item.mechanism === "skill");

  return (
    <section className="report-page report-page-integrated report-codex-review">
      <header className="report-review-titlebar">
        <div>
          <h1>{reportView === "list" ? "报告列表" : reportTitle}</h1>
          <div className="report-review-meta">
            {reportView === "list" ? (
              <>
                <span>报告 <b>{formatNumber(reports.length)}</b></span>
                <span>匹配 <b>{formatNumber(visibleReports.length)}</b></span>
                <span>入口 <b>首页生成</b></span>
              </>
            ) : (
              <>
                <span>项目 <b>{reportProject}</b></span>
                <span>会话 ID <b>{reportSessionId}</b></span>
                <span>生成时间 <b>{reportGeneratedAt}</b></span>
                <span>处理方式 <b>{friendlyAnalysisMode(reportMode)}</b></span>
                <span className="is-ready"><ShieldCheck className="h-3.5 w-3.5" />报告已生成</span>
              </>
            )}
          </div>
        </div>
        <div className="report-review-actions">
          <button type="button" className="report-secondary" disabled={busy === "reports"} onClick={() => void loadReports()}>
            <RefreshCw className="h-4 w-4" />
            {t("common.refresh")}
          </button>
        </div>
      </header>

      {notice?.message && (
        <div className={notice.ok ? "report-notice ok" : "report-notice error"}>
          <CheckCircle2 className="h-4 w-4" />
          <span>{notice.message}</span>
        </div>
      )}

      {reportView === "list" && (
        <section className="report-list-page" aria-label="报告列表">
          <div className="report-list-toolbar">
            <div className="report-command-heading">
              <div>
                <Filter className="h-4 w-4" />
                <span>报告列表</span>
              </div>
              <strong>{visibleReports.length} / {reports.length}</strong>
            </div>
            <div className="report-filter-matrix">
              <label className="report-search-field">
                <span>搜索报告</span>
                <div>
                  <Search className="h-4 w-4" />
                  <input
                    value={reportQuery}
                    onChange={(event) => setReportQuery(event.target.value)}
                    placeholder="标题、项目、会话、建议落点"
                  />
                </div>
              </label>
              <div>
                <span>{t("common.project")}</span>
                <DashboardSelect
                  value={selectedProject}
                  options={[
                    { value: "all", label: t("project.all") },
                    ...projects.map((project) => ({
                      value: project.project_path,
                      label: `${project.project_name} (${project.session_count})`,
                    })),
                  ]}
                  onChange={setSelectedProject}
                  ariaLabel={t("common.project")}
                />
              </div>
              <div>
                <span>沉淀建议</span>
                <DashboardSelect<ReportArtifactFilter>
                  value={reportArtifactFilter}
                  options={[
                    { value: "all", label: "全部状态" },
                    { value: "with_artifacts", label: "有沉淀建议" },
                    { value: "needs_review", label: "待确认" },
                    { value: "without_artifacts", label: "无沉淀建议" },
                  ]}
                  onChange={setReportArtifactFilter}
                  ariaLabel="沉淀建议状态"
                />
              </div>
            </div>
            <div className="report-command-footer">
              <span>{reportFiltersActive ? "已筛选" : "全部报告"}</span>
              <button type="button" className="report-secondary" disabled={!reportFiltersActive} onClick={resetReportFilters}>
                清除筛选
              </button>
            </div>
          </div>
        </section>
      )}

      {reportView === "list" ? (
        visibleReports.length === 0 ? (
        <section className="report-library-empty-state">
          <FileText className="h-9 w-9" />
          <h2>没有匹配的报告</h2>
          <p>调整项目、沉淀建议状态或搜索词；新报告请回到首页生成。</p>
        </section>
        ) : (
          <section className="report-list-grid" aria-label="可打开的报告">
            {visibleReports.map((report) => {
              const summary = asRecord(report.core_summary);
              const mechanisms = firstTextArray(summary.recommended_mechanisms);
              return (
                <button type="button" className="report-list-card" key={report.id} onClick={() => openReport(report.id)}>
                  <span>{report.created_at}</span>
                  <strong>{report.title || report.id}</strong>
                  <p>{text(summary.max_avoidable_cost ?? report.project_path, report.project_path ?? "unknown project")}</p>
                  <div>
                    <b>{reportArtifactCount(report)} 条沉淀建议</b>
                    <b>{mechanisms.slice(0, 3).map(mechanismLabel).join(" / ") || "人工确认"}</b>
                  </div>
                </button>
              );
            })}
          </section>
        )
      ) : !selectedReport ? (
        <section className="report-library-empty-state">
          <FileText className="h-9 w-9" />
          <h2>没有找到报告</h2>
          <p>返回报告列表重新选择。</p>
          <button type="button" className="report-secondary" onClick={showReportList}>返回列表</button>
        </section>
      ) : (
        <article className="report-review-sheet report-design-sheet">
          <div className="report-detail-toolbar">
            <button type="button" className="report-secondary" onClick={showReportList}>
              返回报告列表
            </button>
            <span>{selectedReport.title || selectedReport.id}</span>
          </div>
          <section className="report-overview-panel">
            <div className="report-overview-copy">
              <span className="report-hero-eyebrow">聊天与提效分析</span>
              <h2>{reportFocusTitle}</h2>
              <p>{primaryImprovement}</p>
              <small>{primaryCause}</small>
              <div className="report-overview-status">
                {reportStatusRows.map((row) => (
                  <span key={row.label}>
                    <b>{row.value}</b>
                    {row.label}
                  </span>
                ))}
              </div>
            </div>
            <div className="report-overview-metrics">
              {overviewMetrics.map((row) => (
                <article key={row.label}>
                  <span>{row.label}</span>
                  <strong>{row.value}</strong>
                  <em>{row.note}</em>
                </article>
              ))}
            </div>
          </section>

          <section className="report-review-section report-action-plan-section">
            <div className="report-section-heading">
              <div>
                <h3>下一次怎么做</h3>
                <p>只保留最值得先执行的动作，证据放到后面。</p>
              </div>
            </div>
            <div className="report-action-plan-grid">
              {displayActionCards.map((finding, index) => {
                const evidenceRefs = firstTextArray(finding.evidence_refs ?? finding.source_finding_ids);
                return (
                  <article className="report-action-plan-card" key={text(finding.id, `action-${index}`)}>
                    <span>{index + 1}</span>
                    <div>
                      <strong>{text(finding.title, `建议动作 ${index + 1}`)}</strong>
                      <p>{text(finding.next_action ?? finding.best_action ?? finding.recommendation ?? finding.recommended_action, "等待可执行建议。")}</p>
                      <small>{text(finding.why ?? finding.problem ?? finding.evidence_summary ?? finding.source_finding, "证据在下方展开。")}</small>
                      <footer>
                        <b>{text(finding.suggested_target ?? finding.recommended_mechanism, "待定落点")}</b>
                        {evidenceRefs.slice(0, 3).map((ref) => <b key={ref}>{ref}</b>)}
                      </footer>
                    </div>
                  </article>
                );
              })}
              {displayActionCards.length === 0 && <div className="report-empty">还没有生成提效动作。</div>}
            </div>
          </section>

          <section className="report-review-section report-chat-evidence-section">
            <div className="report-section-heading">
              <div>
                <h3>聊天依据</h3>
                <p>只展示支撑结论的聊天观察和文字样例。</p>
              </div>
            </div>
            <div className="report-chat-evidence-layout">
              <article className="report-chat-transcript-summary">
                <span>开发提效建议</span>
                <strong>{text(userEfficiencyAnalysis.summary ?? chatTranscriptAnalysis.summary, "还没有生成聊天与提效合并分析。")}</strong>
                <dl>
                  <dt>范围</dt>
                  <dd>{friendlyScope(text(chatTranscriptMethod.scope, "raw_user_and_assistant_chat_text"))}</dd>
                  <dt>消息</dt>
                  <dd>{formatNumber(numericMetric(chatTranscriptAnalysis.message_count, 0))} 条</dd>
                  <dt>排除</dt>
                  <dd>{friendlyExcludedItems(firstTextArray(chatTranscriptMethod.excluded)).join(" / ") || "命令输出 / 工具结果"}</dd>
                </dl>
              </article>
              <div className="report-chat-transcript-insights">
                <div>
                  <strong>关键观察</strong>
                  {chatTranscriptObservations.length > 0 ? (
                    <ul>
                      {chatTranscriptObservations.slice(0, 4).map((item) => <li key={item}>{item}</li>)}
                    </ul>
                  ) : (
                    <p>等待模型返回聊天文字观察。</p>
                  )}
                </div>
                <div>
                  <strong>提效卡点</strong>
                  {chatTranscriptFrictionPoints.length > 0 ? (
                    <ul>
                      {chatTranscriptFrictionPoints.slice(0, 4).map((item) => <li key={item}>{item}</li>)}
                    </ul>
                  ) : (
                    <p>未识别到明确摩擦点。</p>
                  )}
                </div>
              </div>
              <div className="report-chat-transcript-sample">
                {chatTranscriptSample.slice(0, 4).map((item, index) => (
                  <blockquote key={text(item.event_id, `chat-sample-${index}`)}>
                    <cite>{text(item.event_id, `event_${index}`)} / {text(item.role, "chat")}</cite>
                    <span>{text(item.quote, "缺少聊天片段")}</span>
                  </blockquote>
                ))}
                {chatTranscriptSample.length === 0 && <div className="report-empty">还没有可展示的纯聊天样例。</div>}
              </div>
            </div>
          </section>

          <section className="report-review-section report-process-section">
            <details className="report-detail-drawer">
              <summary>
                <span>流程轨迹</span>
                <small>展开查看效率成本发生在哪个阶段。</small>
              </summary>
              <div className="report-process-track">
                {processSteps.map((step, index) => (
                  <article className={`report-process-step step-${index + 1}`} key={step.title}>
                    <span>{index + 1}</span>
                    <strong>{step.title}</strong>
                    <small>{step.badge}</small>
                    <p>{step.detail}</p>
                  </article>
                ))}
              </div>
            </details>
          </section>

          <div className="report-review-two-col">
            <section className="report-review-section">
              <div className="report-section-heading">
                <div>
                  <h3>提效问题证据</h3>
                  <p>把上面的行动拆回成本、根因和证据。</p>
                </div>
              </div>
              <div className="report-finding-card-list">
                {v2Findings.slice(0, 3).map((item, index) => (
                  <article className="report-finding-card" key={text(item.id, `finding-${index}`)}>
                    <span className={`report-finding-rank ${index === 0 ? "danger" : index === 1 ? "warn" : "info"}`}>{index + 1}</span>
                    <div>
                      <h4>{text(item.title, "效率问题")}</h4>
                      <p>{text(item.observation ?? item.why_it_slows_work ?? item.problem, "等待效率诊断。")}</p>
                      <dl>
                        <dt>成本</dt>
                        <dd>{findingCostLabel(item)}</dd>
                        <dt>根因</dt>
                        <dd>{text(item.root_cause ?? item.recommended_action, "等待建议")}</dd>
                        <dt>动作</dt>
                        <dd>{text(item.recommendation ?? item.mechanism ?? item.suggested_target, "人工判断")}</dd>
                      </dl>
                    </div>
                  </article>
                ))}
                {v2Findings.length === 0 && <div className="report-empty">还没有识别到效率问题。</div>}
              </div>
            </section>

            <section className="report-review-section">
              <div className="report-section-heading">
                <div>
                  <h3>建议沉淀到哪里</h3>
                  <p>不是所有问题都需要做成固定流程。</p>
                </div>
              </div>
              <div className="report-route-grid">
                {routeCards.map((route) => (
                  <article className="report-route-card" key={`${route.mechanism}-${route.target}`}>
                    <strong>{route.label}</strong>
                    <span>{route.target}</span>
                    <p>{route.reason}</p>
                  </article>
                ))}
              </div>
              <div className="report-skill-callout">
                <strong>是否要沉淀成固定流程？</strong>
                <p>
                  {hasSkillCandidate
                    ? "当前报告包含固定流程候选，但仍需要人工确认证据、触发条件、分支和验证步骤后再长期保留。"
                    : "当前不建议直接做成固定流程。优先把稳定项目知识、检查清单或脚本入口放到更轻量的位置；重复出现后再升级。"}
                </p>
              </div>
            </section>
          </div>

          <div className="report-review-two-col">
            <section className="report-review-section">
              <div className="report-section-heading">
                <div>
                  <h3>验收证据</h3>
                  <p>区分“真的验证”和“只是说完成”。</p>
                </div>
              </div>
              <div className="report-review-table">
                <div className="report-review-table-head">
                  <span>项目</span>
                  <span>状态</span>
                  <span>证据 / 备注</span>
                </div>
                {verificationRows.map((row) => (
                  <div className="report-review-table-row" key={row.item}>
                    <span>{row.item}</span>
                    <span><b className={row.statusClass}>{row.status}</b></span>
                    <span>{row.note}</span>
                  </div>
                ))}
              </div>
              {effectSuccessIndicators.length > 0 && (
                <div className="report-effect-indicators">
                  <strong>下次观察指标</strong>
                  <ul>
                    {effectSuccessIndicators.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>

            <section className="report-review-section">
              <div className="report-section-heading">
                <div>
                  <h3>哪些信息应该更早告诉助手</h3>
                  <p>按“信息类型”整理，而不是只写给下一次对话。</p>
                </div>
              </div>
              <div className="report-review-table context">
                <div className="report-review-table-head">
                  <span>信息项</span>
                  <span>出现时机</span>
                  <span>建议位置</span>
                </div>
                {contextRows.map((row) => (
                  <div className="report-review-table-row" key={`${row.item}-${row.target}`}>
                    <span>{row.item}</span>
                    <span>{row.timing}</span>
                    <span>{row.target}</span>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <div className="report-review-two-col">
            <section className="report-review-section">
              <div className="report-section-heading">
                <div>
                  <h3>值得保留的做法</h3>
                  <p>保留有效协作动作，不把报告只做成问题列表。</p>
                </div>
              </div>
              <ul className="report-retain-list">
                {preserveRows.map((item) => (
                  <li key={item}>
                    <CheckCircle2 className="h-4 w-4" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </section>

            <section className="report-review-section">
              <div className="report-section-heading">
                <div>
                  <h3>依据与证据</h3>
                  <p>默认折叠，分享时可展开。</p>
                </div>
              </div>
              <div className="report-evidence-details">
                {conversationAnalysis.slice(0, 3).map((card, index) => (
                  <details key={text(card.id, `evidence-${index}`)}>
                    <summary>{text(card.title, `证据 ${index + 1}`)}</summary>
                    <p>{text(card.analysis ?? card.basis, "这条结论还没有解析到聊天证据。")}</p>
                    <div className="report-chat-quotes">
                      {asRecords(card.evidence).slice(0, 2).map((evidenceItem, evidenceIndex) => (
                        <blockquote key={text(evidenceItem.ref_id, `quote-${evidenceIndex}`)}>
                          <cite>{text(evidenceItem.ref_id ?? evidenceItem.event_id, `evidence-${evidenceIndex + 1}`)}</cite>
                          <span>{text(evidenceItem.quote, "缺少聊天片段")}</span>
                        </blockquote>
                      ))}
                    </div>
                  </details>
                ))}
                {conversationAnalysis.length === 0 && <div className="report-empty">还没有解析到聊天记录证据。</div>}
              </div>
            </section>
          </div>

          <footer className="report-design-footer">
            recodex · 本地生成 · 报告基于可追溯证据 · {reportPath || selectedReport?.json_path || "未加载报告数据路径"}
          </footer>
        </article>
      )}
    </section>
  );
}

function reportProcessSteps(
  summary: Record<string, unknown>,
  taskOutcome: Record<string, unknown>,
  costLedger: Record<string, unknown>,
  evidenceAudit: Record<string, unknown>,
  artifact: Record<string, unknown>,
): Array<{ title: string; badge: string; detail: string }> {
  const userCorrections = numericMetric(costLedger.user_corrections, 0);
  return [
    {
      title: "提出任务",
      badge: text(summary.user_intent, "目标输入"),
      detail: text(summary.user_intent ?? summary.headline, "任务目标已进入报告分析。"),
    },
    {
      title: "探索定位",
      badge: costLedgerBasis(costLedger),
      detail: "从命令、文件读取和用户纠偏中定位可避免成本。",
    },
    {
      title: "用户纠偏",
      badge: userCorrections > 0 ? `${formatNumber(userCorrections)} 次纠偏` : "未观察到纠偏",
      detail: text(taskOutcome.remaining_risk, "用户纠偏越多，越说明稳定项目知识需要前置。"),
    },
    {
      title: "实现修改",
      badge: friendlyStatus(text(taskOutcome.result, "unknown")),
      detail: `完成可信度：${friendlyStatus(text(taskOutcome.completion_confidence, "unknown"))}。`,
    },
    {
      title: "收尾验收",
      badge: friendlyStatus(text(evidenceAudit.status ?? taskOutcome.verification_status, "pending")),
      detail: text(artifact.target_path ?? evidenceAudit.summary, "沉淀建议需要人工确认和后续效果观察。"),
    },
  ];
}

function reportRouteCards(
  artifacts: Record<string, unknown>[],
  mechanismGroups: Array<{ label: string; count: number }>,
  primaryMechanism: string,
): Array<{ label: string; mechanism: string; target: string; reason: string }> {
  const cards = artifacts.slice(0, 4).map((artifact) => {
    const mechanism = text(artifact.mechanism ?? artifact.artifact_type, "review");
    return {
      label: mechanismLabel(mechanism),
      mechanism,
      target: text(artifact.target_path ?? artifact.scope, "人工确认"),
      reason: text(artifact.rationale ?? artifact.expected_benefit, "沉淀建议必须经过人工确认后再落地。"),
    };
  });
  if (cards.length > 0) {
    return cards;
  }
  const grouped = mechanismGroups.slice(0, 4).map((group) => ({
    label: mechanismLabel(group.label),
    mechanism: group.label,
    target: `${formatNumber(group.count)} 个候选`,
    reason: routeReasonLabel({ recommended_mechanism: group.label }),
  }));
  return grouped.length > 0
    ? grouped
    : [
        {
          label: mechanismLabel(primaryMechanism || "review"),
          mechanism: primaryMechanism || "review",
          target: "人工确认",
          reason: "当前报告还没有明确的沉淀建议，先保留为人工判断。",
        },
      ];
}

function reportVerificationRows(
  taskOutcome: Record<string, unknown>,
  evidenceAudit: Record<string, unknown>,
  selectedReport: ReportRecord | null,
  artifactReviewQueue: Record<string, unknown>[],
  reviewableArtifacts: Record<string, unknown>[],
): Array<{ item: string; status: string; statusClass: "ok" | "warn" | "danger"; note: string }> {
  const reviewCount = reviewableArtifacts.length || artifactReviewQueue.length;
  const evidenceStatus = text(evidenceAudit.status, "unknown");
  const verificationStatus = text(taskOutcome.verification_status, "unknown");
  return [
    {
      item: "任务结果",
      status: friendlyStatus(text(taskOutcome.result, "unknown")),
      statusClass: reportStatusClass(taskOutcome.result),
      note: text(taskOutcome.remaining_risk ?? taskOutcome.completion_confidence, "报告没有记录任务剩余风险。"),
    },
    {
      item: "验证闭环",
      status: friendlyStatus(verificationStatus),
      statusClass: reportStatusClass(verificationStatus),
      note: text(taskOutcome.completion_confidence, "需要报告写明测试、构建或人工验收结果。"),
    },
    {
      item: "证据检查",
      status: friendlyStatus(evidenceStatus),
      statusClass: reportStatusClass(evidenceStatus),
      note: text(evidenceAudit.summary, "当前报告没有证据检查摘要。"),
    },
    {
      item: "沉淀建议确认",
      status: reviewCount > 0 ? `${formatNumber(reviewCount)} 待确认` : "无待确认项",
      statusClass: reviewCount > 0 ? "warn" : "ok",
      note: selectedReport?.json_path || "沉淀建议只展示，不会自动写入长期规范。",
    },
  ];
}

function reportContextRows(
  opportunities: Record<string, unknown>[],
  artifacts: Record<string, unknown>[],
): Array<{ item: string; timing: string; target: string }> {
  const rows = opportunities.slice(0, 4).map((opportunity) => ({
    item: text(opportunity.title ?? opportunity.best_action, "稳定协作信息"),
    timing: usefulText(opportunity.cause, opportunity.problem, opportunity.routing_reason, "成本出现后才被发现"),
    target: text(opportunity.suggested_target ?? opportunity.target_path ?? opportunity.recommended_mechanism, "AGENTS.md / 检查清单"),
  }));
  if (rows.length > 0) {
    return rows;
  }
  const artifactRows = artifacts.slice(0, 4).map((artifact) => ({
    item: text(artifact.title ?? artifact.mechanism, "沉淀建议"),
    timing: text(artifact.rationale, "报告生成后"),
    target: text(artifact.target_path ?? artifact.scope, "人工确认"),
  }));
  return artifactRows.length > 0
    ? artifactRows
    : [
        { item: "标准入口与命令", timing: "任务开始前", target: "AGENTS.md" },
        { item: "完成验收标准", timing: "修改前", target: "Checklist / CI" },
      ];
}

function reportPreserveRows(
  summary: Record<string, unknown>,
  evidenceAudit: Record<string, unknown>,
  selectedReport: ReportRecord | null,
): string[] {
  const rows = [
    text(summary.overall, ""),
    text(evidenceAudit.ok === true ? "证据检查通过，关键结论能回指到报告证据。" : evidenceAudit.summary, ""),
    selectedReport?.html_path ? "报告保留本地 HTML、Markdown 和 JSON 路径，便于分享与复盘。" : "",
  ].filter(Boolean);
  return rows.length > 0 ? rows.slice(0, 3) : ["报告已经按结构化证据生成，后续可以继续观察同类成本是否下降。"];
}

function findingCostLabel(finding: Record<string, unknown>): string {
  const observed = asRecord(finding.observed_cost);
  const chips = [
    metricChip("重复命令", observed.repeated_commands),
    metricChip("失败命令", observed.failed_commands),
    metricChip("重复读文件", observed.repeated_file_reads),
    metricChip("用户纠偏", observed.user_corrections),
    metricChip("额外轮次", observed.extra_turns),
  ].filter((chip): chip is string => Boolean(chip));
  if (chips.length > 0) {
    return chips.join("，");
  }
  const occurrences = numericMetric(finding.occurrences ?? finding.signal_count ?? finding.count, 0);
  return occurrences > 0 ? `${formatNumber(occurrences)} 次出现` : `${evidenceRefCount(finding)} 条证据引用`;
}

function mechanismLabel(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized === "agents_md") {
    return "AGENTS.md";
  }
  if (normalized === "hook_or_ci") {
    return "自动检查";
  }
  if (normalized === "script") {
    return "脚本";
  }
  if (normalized === "skill") {
    return "固定流程";
  }
  if (normalized === "checklist") {
    return "检查清单";
  }
  if (normalized === "prompt_template") {
    return "对话模板";
  }
  if (normalized === "review") {
    return "人工确认";
  }
  return value || "人工确认";
}

function friendlyAnalysisMode(value: string): string {
  const normalized = value.toLowerCase();
  const parts = [];
  if (normalized.includes("llm")) {
    parts.push("模型分析");
  }
  if (normalized.includes("rules")) {
    parts.push("规则检查");
  }
  if (normalized.includes("audit")) {
    parts.push("证据检查");
  }
  return parts.length > 0 ? parts.join(" + ") : value || "默认分析";
}

function friendlyAnalysisSource(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized === "llm") {
    return "模型分析";
  }
  if (normalized === "rules") {
    return "规则分析";
  }
  return value || "规则分析";
}

function friendlyScope(value: string): string {
  if (value === "raw_user_and_assistant_chat_text") {
    return "你和助手的文字消息";
  }
  if (value === "pure_user_messages") {
    return "你的文字消息";
  }
  if (value === "user_message_efficiency_signals") {
    return "聊天中的提效信号";
  }
  if (value === "avoidable_cost_findings") {
    return "可节省成本问题";
  }
  return value || "聊天文字";
}

function friendlyExcludedItems(values: string[]): string[] {
  const labels: Record<string, string> = {
    tool_calls: "工具调用",
    tool_outputs: "工具输出",
    command_results: "命令结果",
    environment_context: "环境上下文",
    system_or_developer_instructions: "系统和开发者指令",
    tool_outputs_as_chat_conclusions: "把工具输出当作聊天结论",
    assistant_success_claims_as_primary_subject: "把助手成功声明当作主结论",
  };
  return values.map((value) => labels[value] ?? value);
}

function friendlyStatus(value: string): string {
  const normalized = value.toLowerCase();
  if (["pass", "passed", "ok", "success", "succeeded", "supported"].includes(normalized)) {
    return "通过";
  }
  if (["pending", "not observed", "not_observed"].includes(normalized)) {
    return "待观察";
  }
  if (["unknown", "result unknown"].includes(normalized)) {
    return "未知";
  }
  if (normalized === "completed_with_evidence") {
    return "已完成，有验证";
  }
  if (normalized === "completed_with_verification_gap") {
    return "已完成，但验证不足";
  }
  if (normalized === "needs_review") {
    return "需要确认";
  }
  if (normalized === "medium_low") {
    return "中低";
  }
  if (normalized === "medium") {
    return "中";
  }
  if (normalized === "high") {
    return "高";
  }
  if (normalized === "low") {
    return "低";
  }
  return value || "未知";
}

function reportStatusClass(value: unknown): "ok" | "warn" | "danger" {
  const raw = text(value, "").toLowerCase();
  if (["pass", "passed", "ok", "success", "succeeded", "supported", "completed"].some((token) => raw.includes(token))) {
    return "ok";
  }
  if (["fail", "failed", "missing", "缺失", "不足", "error", "blocked"].some((token) => raw.includes(token))) {
    return "danger";
  }
  return "warn";
}

function numericMetric(value: unknown, fallback = 0): number {
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function groupCount(values: string[]): Array<{ label: string; count: number }> {
  const counts = new Map<string, number>();
  for (const value of values) {
    const label = value.trim() || "unknown";
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return Array.from(counts, ([label, count]) => ({ label, count })).sort(
    (left, right) => right.count - left.count || left.label.localeCompare(right.label),
  );
}

function reportCostRows(costLedger: Record<string, unknown>, metrics: Record<string, unknown>) {
  return [
    {
      key: "failed_commands",
      label: "失败命令",
      value: numericMetric(costLedger.failed_commands ?? metrics.failed_commands),
      note: "命令失败带来的直接工具成本",
    },
    {
      key: "repeated_commands",
      label: "重复命令",
      value: numericMetric(costLedger.repeated_commands ?? metrics.repeated_commands),
      note: "相似命令无新假设重复执行",
    },
    {
      key: "repeated_file_reads",
      label: "重复读文件",
      value: numericMetric(costLedger.repeated_file_reads ?? metrics.repeated_file_reads),
      note: "上下文读取成本与噪声",
    },
    {
      key: "user_corrections",
      label: "用户纠正",
      value: numericMetric(costLedger.user_corrections ?? metrics.user_corrections),
      note: "监督与返工信号",
    },
    {
      key: "verification_followups",
      label: "验证追问",
      value: numericMetric(costLedger.verification_followups ?? metrics.verification_followups),
      note: "验证成本是否转移给用户",
    },
    {
      key: "extra_turns",
      label: "额外轮次",
      value: numericMetric(costLedger.extra_turns ?? metrics.extra_turns),
      note: "估计的可避免交互成本",
    },
  ];
}

function evidenceRefCount(item: Record<string, unknown>): number {
  return firstArray(item.evidence_refs, item.evidence_event_ids, item.evidence_claim_ids).length;
}

function costLedgerBasis(costLedger: Record<string, unknown>): string {
  const chips = [
    metricChip("重复读文件", costLedger.repeated_file_reads),
    metricChip("重复命令", costLedger.repeated_commands),
    metricChip("失败命令", costLedger.failed_commands),
    metricChip("用户纠正", costLedger.user_corrections),
    metricChip("验证追问", costLedger.verification_followups),
  ].filter((chip): chip is string => Boolean(chip));
  return chips.length > 0 ? chips.join("，") : "未发现明显可避免成本。";
}

function metricChip(label: string, value: unknown): string {
  const count = numericMetric(value, 0);
  return count > 0 ? `${label} ${formatNumber(count)}` : "";
}

function routeReasonLabel(opportunity: Record<string, unknown>): string {
  const mechanism = text(opportunity.recommended_mechanism, "");
  const target = text(opportunity.suggested_target, "人工判断");
  const raw = text(opportunity.routing_reason, "");
  if (mechanism === "agents_md" || target.toLowerCase() === "agents.md") {
    return "这是稳定项目约定，不是一次性修复；放进 AGENTS.md 才会进入后续默认上下文。";
  }
  if (mechanism === "hook_or_ci") {
    return "这是必须强制执行的安全或验证边界，靠提醒不够，需要 hook/CI/policy。";
  }
  if (mechanism === "script") {
    return "这是重复出现的验证或排查入口，固化成脚本比继续口头约定更稳定。";
  }
  return raw || `建议落点：${target}`;
}

function usefulText(...values: unknown[]): string {
  const fallback = text(values.at(-1), "缺少说明");
  for (const value of values.slice(0, -1)) {
    const candidate = text(value, "");
    if (candidate && !isMachineEvidenceDump(candidate)) {
      return candidate;
    }
  }
  return fallback;
}

function isMachineEvidenceDump(value: string): boolean {
  return value.includes("assistant 运行了命令") && value.includes("结果为 unknown");
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
}

function filterReportRecords(reports: ReportRecord[], filters: ReportFilters): ReportRecord[] {
  const query = filters.query.trim().toLowerCase();
  return reports.filter((report) => {
    if (filters.project !== "all" && (report.project_path || "(unknown)") !== filters.project) {
      return false;
    }
    if (filters.kind !== "all" && report.kind !== filters.kind) {
      return false;
    }
    if (filters.artifact === "with_artifacts" && reportArtifactCount(report) === 0) {
      return false;
    }
    if (filters.artifact === "without_artifacts" && reportArtifactCount(report) > 0) {
      return false;
    }
    if (filters.artifact === "needs_review" && !reportHasReviewableArtifact(report)) {
      return false;
    }
    return !query || reportSearchText(report).includes(query);
  });
}

function reportArtifactCount(report: ReportRecord): number {
  const summary = asRecord(report.core_summary);
  const explicitCount = numericMetric(summary.artifact_candidate_count, 0);
  if (explicitCount > 0) {
    return explicitCount;
  }
  return asRecords(summary.top_artifact_candidates).length;
}

function reportHasReviewableArtifact(report: ReportRecord): boolean {
  const candidates = asRecords(asRecord(report.core_summary).top_artifact_candidates);
  if (candidates.length === 0) {
    return reportArtifactCount(report) > 0;
  }
  return candidates.some((candidate) => {
    const status = text(candidate.status, "proposed").toLowerCase();
    return !["accepted", "rejected"].includes(status);
  });
}

function reportSearchText(report: ReportRecord): string {
  const summary = asRecord(report.core_summary);
  const opportunities = asRecords(summary.top_opportunities);
  const artifacts = asRecords(summary.top_artifact_candidates);
  return [
    report.id,
    report.kind,
    report.title,
    report.session_id,
    report.project_path,
    report.created_at,
    summary.max_avoidable_cost,
    summary.primary_cause,
    summary.primary_improvement,
    firstTextArray(summary.recommended_mechanisms).join(" "),
    ...opportunities.flatMap((item) => [
      item.title,
      item.recommended_mechanism,
      item.suggested_target,
      item.best_action,
    ]),
    ...artifacts.flatMap((item) => [item.artifact_type, item.target_path, item.status]),
  ]
    .map((value) => text(value, ""))
    .join(" ")
    .toLowerCase();
}

function asRecords(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map(asRecord).filter((item) => Object.keys(item).length > 0);
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function firstArray(...values: unknown[]): unknown[] {
  return values.find((value): value is unknown[] => Array.isArray(value)) ?? [];
}

function firstTextArray(...values: unknown[]): string[] {
  for (const value of values) {
    const items = Array.isArray(value) ? value : [];
    const textItems = items.map((item) => text(item, "")).filter(Boolean);
    if (textItems.length > 0) {
      return textItems;
    }
  }
  return [];
}

function text(value: unknown, fallback = "unknown"): string {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  if (Array.isArray(value)) {
    return value.map((item) => text(item, "")).filter(Boolean).join(", ") || fallback;
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}
