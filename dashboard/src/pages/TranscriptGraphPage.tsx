import {
  AlertTriangle,
  Braces,
  CheckCircle2,
  CircleDot,
  Code2,
  Eye,
  FileCode2,
  GitGraph,
  ListTree,
  MessageSquareText,
  RefreshCw,
  Search,
  TerminalSquare,
  TestTube,
  Workflow,
  UserCheck,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import { DashboardSelect } from "@/components/DashboardSelect";
import { useI18n } from "@/lib/i18n";
import { getJson } from "@/lib/recodexClient";

type SessionRecord = {
  session_id: string;
  source: string | null;
  title: string;
  updated_at: string | null;
  command_count: number;
  error_count: number;
  project_id?: string;
  project_path?: string | null;
  project_name?: string | null;
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

type GraphRow = Record<string, unknown>;
type GraphViewMode = "flow" | "evidence" | "raw";

type GraphEvent = GraphRow & {
  event_id: string;
  turn_id: string;
  event_index: number;
  role: string;
  event_type: string;
  kind: string;
  phase: string;
  created_at: string | null;
  source_ref: string;
  text_excerpt: string;
  user_input_text?: string | null;
  metadata_json?: string | null;
};

type TranscriptGraph = {
  ok: boolean;
  session: GraphRow;
  raw_artifacts: GraphRow[];
  raw_records: GraphRow[];
  turns: GraphRow[];
  events: GraphEvent[];
  tool_calls: GraphRow[];
  tool_results: GraphRow[];
  file_refs: GraphRow[];
  test_refs: GraphRow[];
  error_refs: GraphRow[];
  user_corrections: GraphRow[];
  edges: GraphRow[];
};

type LineageEndpoint = {
  type: string;
  id: string;
  relation: string;
  node?: GraphRow | null;
};

type LineageResponse = {
  ok: boolean;
  ref: string;
  node: {
    type: string;
    id: string;
  };
  upstream: LineageEndpoint[];
  downstream: LineageEndpoint[];
  evidence: GraphRow[];
};

const MAX_RENDERED_EVENTS = 500;

export function TranscriptGraphPage({
  sessions,
  projects,
  initialSessionId = "",
  onSessionChange,
}: {
  sessions: SessionRecord[];
  projects: ProjectRecord[];
  initialSessionId?: string;
  onSessionChange?: (sessionId: string) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [selectedProject, setSelectedProject] = useState("all");
  const [selectedSessionId, setSelectedSessionId] = useState(initialSessionId);
  const [graph, setGraph] = useState<TranscriptGraph | null>(null);
  const [selectedRef, setSelectedRef] = useState("");
  const [lineage, setLineage] = useState<LineageResponse | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);
  const [viewMode, setViewMode] = useState<GraphViewMode>("flow");

  const filteredSessions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return sessions.filter((session) => {
      if (selectedProject !== "all" && projectPath(session) !== selectedProject) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return [session.title, session.session_id, session.source ?? "", session.updated_at ?? "", projectPath(session), session.project_name ?? ""].some((value) =>
        value.toLowerCase().includes(needle),
      );
    });
  }, [query, selectedProject, sessions]);

  const projectSessions = useMemo(
    () => sessions.filter((session) => selectedProject === "all" || projectPath(session) === selectedProject),
    [selectedProject, sessions],
  );

  const selectedSession = useMemo(
    () => projectSessions.find((session) => session.session_id === selectedSessionId) ?? projectSessions[0] ?? sessions[0],
    [projectSessions, selectedSessionId, sessions],
  );

  const userInputEvents = useMemo(
    () => (graph?.events ?? []).filter((event) => Boolean(eventUserInput(event))),
    [graph?.events],
  );
  const flowEvents = useMemo(
    () => (graph?.events ?? []).filter((event) => includeInConversationChain(event, graph)),
    [graph],
  );
  const displayEvents = useMemo(
    () => (viewMode === "flow" ? flowEvents : graph?.events ?? []),
    [flowEvents, graph?.events, viewMode],
  );
  const eventsByTurn = useMemo(() => {
    const grouped = new Map<string, GraphEvent[]>();
    for (const event of displayEvents) {
      const turnId = event.turn_id;
      const current = grouped.get(turnId) ?? [];
      current.push(event);
      grouped.set(turnId, current);
    }
    return grouped;
  }, [displayEvents]);

  const visibleTurnGroups = useMemo(() => {
    const groups: { turn: GraphRow; events: GraphEvent[] }[] = [];
    let remaining = MAX_RENDERED_EVENTS;
    for (const turn of graph?.turns ?? []) {
      const turnId = text(turn, "turn_id");
      const turnEvents = eventsByTurn.get(turnId) ?? [];
      if (turnEvents.length === 0) {
        continue;
      }
      if (remaining <= 0 && turnEvents.length > 0) {
        break;
      }
      const visibleEvents = turnEvents.slice(0, Math.max(remaining, 0));
      if (visibleEvents.length > 0) {
        groups.push({ turn, events: visibleEvents });
      }
      remaining -= visibleEvents.length;
    }
    return groups;
  }, [eventsByTurn, graph?.turns]);

  const visibleEventCount = useMemo(
    () => visibleTurnGroups.reduce((total, group) => total + group.events.length, 0),
    [visibleTurnGroups],
  );
  const hiddenEventCount = Math.max(0, displayEvents.length - visibleEventCount);

  const selectedEvent = useMemo(
    () => graph?.events.find((event) => event.source_ref === selectedRef) ?? null,
    [graph?.events, selectedRef],
  );

  const selectedEventId = selectedEvent?.event_id ?? "";
  const selectedFiles = useMemo(
    () => rowsForEvent(graph?.file_refs ?? [], selectedEventId),
    [graph?.file_refs, selectedEventId],
  );
  const selectedTests = useMemo(
    () => rowsForEvent(graph?.test_refs ?? [], selectedEventId),
    [graph?.test_refs, selectedEventId],
  );
  const selectedErrors = useMemo(
    () => rowsForEvent(graph?.error_refs ?? [], selectedEventId),
    [graph?.error_refs, selectedEventId],
  );
  const selectedCorrections = useMemo(
    () => rowsForEvent(graph?.user_corrections ?? [], selectedEventId),
    [graph?.user_corrections, selectedEventId],
  );
  const selectedToolCalls = useMemo(
    () => rowsForEvent(graph?.tool_calls ?? [], selectedEventId),
    [graph?.tool_calls, selectedEventId],
  );
  const selectedProjectName = selectedSession ? projectName(selectedSession) : t("common.unknown");
  const selectedSessionTitle = selectedSession?.title || selectedSession?.session_id || t("common.unknown");
  const selectedSessionMeta = selectedSession
    ? [
        selectedSession.source ?? t("common.unknown"),
        selectedSession.updated_at ?? t("common.never"),
        selectedProjectName,
      ].join(" / ")
    : t("common.noData");

  useEffect(() => {
    if (initialSessionId && initialSessionId !== selectedSessionId) {
      setSelectedSessionId(initialSessionId);
    }
  }, [initialSessionId, selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId && projectSessions[0]?.session_id) {
      selectSession(projectSessions[0].session_id, false);
    }
  }, [projectSessions, selectedSessionId]);

  useEffect(() => {
    if (!selectedSessionId || selectedProject === "all") {
      return;
    }
    if (!projectSessions.some((session) => session.session_id === selectedSessionId)) {
      selectSession(projectSessions[0]?.session_id ?? "", false);
    }
  }, [projectSessions, selectedProject, selectedSessionId]);

  useEffect(() => {
    if (selectedSession?.session_id) {
      void loadGraph(selectedSession.session_id);
    }
  }, [selectedSession?.session_id]);

  useEffect(() => {
    if (viewMode !== "flow" || !graph || flowEvents.length === 0) {
      return;
    }
    if (flowEvents.some((event) => event.source_ref === selectedRef)) {
      return;
    }
    const firstRef = flowEvents[0].source_ref;
    setSelectedRef(firstRef);
    void loadLineage(selectedSession?.session_id ?? "", firstRef, false);
  }, [flowEvents, graph, selectedRef, selectedSession?.session_id, viewMode]);

  function selectSession(sessionId: string, clearGraph = true) {
    setSelectedSessionId(sessionId);
    onSessionChange?.(sessionId);
    if (clearGraph) {
      setGraph(null);
      setLineage(null);
    }
  }

  async function loadGraph(sessionId = selectedSession?.session_id ?? "") {
    if (!sessionId) {
      return;
    }
    setBusy("graph");
    setNotice(null);
    const result = await getJson<TranscriptGraph>(`/transcripts/${encodeURIComponent(sessionId)}/graph`);
    setBusy(null);
    if (!result.ok) {
      setGraph(null);
      setLineage(null);
      setNotice({ ok: false, message: result.message });
      return;
    }
    setGraph(result.data);
    const firstRef =
      result.data.events?.find((event) => includeInConversationChain(event, result.data))?.source_ref ??
      result.data.events?.find((event) => Boolean(eventUserInput(event)))?.source_ref ??
      result.data.events?.[0]?.source_ref ??
      "";
    setSelectedRef(firstRef);
    setNotice({
      ok: true,
      message: t("graph.loaded", { count: result.data.events?.length ?? 0 }),
    });
    if (firstRef) {
      void loadLineage(sessionId, firstRef, false);
    } else {
      setLineage(null);
    }
  }

  async function loadLineage(sessionId: string, ref: string, showBusy = true) {
    if (!sessionId || !ref) {
      return;
    }
    if (showBusy) {
      setBusy("lineage");
    }
    setSelectedRef(ref);
    const params = new URLSearchParams({ ref });
    const result = await getJson<LineageResponse>(
      `/transcripts/${encodeURIComponent(sessionId)}/lineage?${params.toString()}`,
    );
    if (showBusy) {
      setBusy(null);
    }
    if (!result.ok) {
      setLineage(null);
      setNotice({ ok: false, message: result.message });
      return;
    }
    setLineage(result.data);
  }

  if (sessions.length === 0) {
    return (
      <div className="content-stack graph-page">
        <section className="work-panel graph-empty-panel">
          <GitGraph className="h-6 w-6" />
          <strong>{t("graph.noSession")}</strong>
        </section>
      </div>
    );
  }

  const metrics = [
    { label: t("graph.turns"), value: graph?.turns.length ?? 0 },
    { label: t("graph.events"), value: graph?.events.length ?? 0 },
    { label: t("graph.flowEvents"), value: flowEvents.length },
    { label: t("graph.toolCalls"), value: graph?.tool_calls.length ?? 0 },
    { label: t("graph.files"), value: graph?.file_refs.length ?? 0 },
    { label: t("graph.errors"), value: graph?.error_refs.length ?? 0 },
  ];
  const hiddenContextCount = Math.max(0, (graph?.events.length ?? 0) - flowEvents.length);
  const toolLikeEventCount = graph ? flowEvents.filter((event) => hasToolSignal(event, graph)).length : 0;
  const flowStats = [
    { label: t("graph.flow.goal"), value: flowEvents.filter((event) => Boolean(eventUserInput(event))).length, className: "goal" },
    { label: t("graph.flow.assistant"), value: flowEvents.filter((event) => event.role === "assistant").length, className: "assistant" },
    { label: t("graph.flow.tool"), value: toolLikeEventCount, className: "tool" },
    { label: t("graph.flow.validation"), value: graph?.test_refs.length ?? 0, className: "validation" },
    { label: t("graph.flow.correction"), value: graph?.user_corrections.length ?? 0, className: "correction" },
    { label: t("graph.flow.context"), value: hiddenContextCount, className: "context" },
  ];

  return (
    <div className="content-stack graph-page graph-workbench">
      {notice && (
        <div className={notice.ok ? "inline-notice ok" : "inline-notice error"}>
          {notice.ok ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
          <span>{notice.message}</span>
        </div>
      )}

      <section className="graph-command-center">
        <div className="graph-session-hero">
          <div className="focus-kicker">
            <span className="status-dot ok" />
            {t("graph.currentSession")}
          </div>
          <h2>{selectedSessionTitle}</h2>
          <p>{selectedSessionMeta}</p>
          <div className="focus-meta-row">
            <span>{selectedSession?.command_count ?? 0} {t("sessions.cmd")}</span>
            <span>{selectedSession?.error_count ?? 0} {t("sessions.err")}</span>
            <span>{selectedSession?.session_id ?? t("common.unknown")}</span>
          </div>
          <div className="graph-hero-actions">
            <GraphViewToggle value={viewMode} onChange={setViewMode} />
            <button
              type="button"
              className="secondary-command"
              disabled={busy === "graph" || !selectedSession}
              onClick={() => void loadGraph()}
            >
              <RefreshCw className="h-4 w-4" />
              {t("graph.reload")}
            </button>
          </div>
          <div className="graph-flow-inventory" aria-label={t("graph.flowInventory")}>
            {flowStats.map((item) => (
              <span className={`graph-flow-chip ${item.className}`} key={item.label}>
                <strong>{item.value}</strong>
                {item.label}
              </span>
            ))}
          </div>
        </div>

        <div className="graph-kpi-panel" aria-label={t("graph.sessionGraph")}>
          <SectionKicker title={t("graph.traceHealth")} action={busy === "graph" ? t("graph.loading") : t("status.ready")} />
          <div className="graph-kpi-grid">
            {metrics.map((metric, index) => (
              <div className="graph-stat" key={metric.label}>
                <span>{index + 1}</span>
                <strong>{metric.value}</strong>
                <small>{metric.label}</small>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="graph-layout graph-review-layout">
        <aside className="work-panel graph-session-panel graph-navigator-panel">
          <GraphPanelTitle icon={<Search className="h-4 w-4" />} title={t("graph.selectSession")} action={t("graph.searchSession")} />
          <div className="field graph-project-filter">
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
          <div className="search-strip graph-search">
            <Search className="h-4 w-4" />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("graph.searchSession")} />
          </div>
          <div className="graph-session-list">
            {filteredSessions.map((session) => (
              <button
                key={session.session_id}
                type="button"
                className={session.session_id === selectedSession?.session_id ? "graph-session-button active" : "graph-session-button"}
                onClick={() => {
                  selectSession(session.session_id);
                }}
              >
                <span className="graph-session-title">{session.title || session.session_id}</span>
                <span className="graph-session-counters">
                  <span>{session.command_count} {t("sessions.cmd")}</span>
                  <span>{session.error_count} {t("sessions.err")}</span>
                </span>
                <span className="graph-session-meta">{session.source ?? t("common.unknown")}</span>
                <span className="graph-session-project">{projectName(session)}</span>
                <code>{session.session_id}</code>
              </button>
            ))}
            {filteredSessions.length === 0 && <div className="graph-muted-box">{t("sessions.filteredEmpty")}</div>}
          </div>
          <button
            type="button"
            className="secondary-command graph-refresh"
            disabled={busy === "graph" || !selectedSession}
            onClick={() => void loadGraph()}
          >
            <RefreshCw className="h-4 w-4" />
            {t("graph.reload")}
          </button>
        </aside>

        <main className="work-panel graph-main-panel graph-timeline-panel">
          <GraphPanelTitle icon={<ListTree className="h-4 w-4" />} title={t("graph.timeline")} action={viewMode === "flow" ? t("graph.timelineFlowAction") : t("graph.timelineAction")} />
          {viewMode === "flow" && hiddenContextCount > 0 && (
            <div className="graph-context-fold">
              <Workflow className="h-4 w-4" />
              <span>{t("graph.contextFolded", { count: hiddenContextCount })}</span>
            </div>
          )}
          <GraphChain groups={visibleTurnGroups} graph={graph} selectedEvent={selectedEvent} />
          {busy === "graph" && <div className="graph-muted-box">{t("graph.loading")}</div>}
          {!busy && graph && graph.events.length === 0 && <div className="graph-muted-box">{t("graph.empty")}</div>}
          {!busy && graph && graph.events.length > 0 && viewMode === "flow" && flowEvents.length === 0 && (
            <div className="graph-muted-box">{t("graph.noFlowEvents")}</div>
          )}
          {graph && visibleTurnGroups.map(({ turn, events }) => {
            const turnId = text(turn, "turn_id");
            return (
              <article className="graph-turn-card" key={turnId}>
                <div className="graph-turn-head">
                  <span>{t("graph.turn")} {text(turn, "turn_index")}</span>
                  <strong>{text(turn, "initiator", t("common.unknown"))}</strong>
                  <small>{text(turn, "phase_hint", t("common.unknown"))}</small>
                </div>
                <div className="graph-event-list">
                  {events.map((event) => (
                    <GraphEventRow
                      key={event.event_id}
                      event={event}
                      graph={graph}
                      selected={event.source_ref === selectedRef}
                      viewMode={viewMode}
                      onOpen={() => void loadLineage(selectedSession?.session_id ?? "", event.source_ref)}
                    />
                  ))}
                </div>
              </article>
            );
          })}
          {hiddenEventCount > 0 && (
            <div className="graph-muted-box">
              {t("graph.renderLimit", { shown: visibleEventCount, hidden: hiddenEventCount })}
            </div>
          )}
        </main>

        <aside className="work-panel graph-lineage-panel graph-inspector-panel">
          <GraphPanelTitle
            icon={<GitGraph className="h-4 w-4" />}
            title={t("graph.lineage")}
            action={selectedEvent?.event_type || t("graph.lineageAction")}
          />
          {!selectedRef && <div className="graph-muted-box">{t("graph.noneSelected")}</div>}
          {selectedRef && !lineage && busy === "lineage" && <div className="graph-muted-box">{t("graph.loading")}</div>}
          {selectedRef && !lineage && busy !== "lineage" && <div className="graph-muted-box">{t("graph.noLineage")}</div>}
          {lineage && (
            <>
              <div className="graph-selected-node">
                <span>{lineage.node.type}</span>
                <code>{lineage.ref}</code>
              </div>
              {selectedEvent && <SelectedEventBrief event={selectedEvent} graph={graph} viewMode={viewMode} />}
              {viewMode === "flow" ? (
                <>
                  <ConversationCoverage graph={graph} event={selectedEvent} />
                  <div className="graph-muted-box compact">{t("graph.flowHint")}</div>
                </>
              ) : (
                <>
                  <LineageList title={t("graph.upstream")} items={lineage.upstream} />
                  <LineageList title={t("graph.downstream")} items={lineage.downstream} />
                  <EvidencePack evidence={lineage.evidence} />
                  <SemanticRefs
                    toolCalls={selectedToolCalls}
                    files={selectedFiles}
                    tests={selectedTests}
                    errors={selectedErrors}
                    corrections={selectedCorrections}
                  />
                </>
              )}
            </>
          )}
        </aside>
      </section>
    </div>
  );
}

function SectionKicker({ title, action }: { title: string; action: string }) {
  return (
    <div className="graph-section-kicker">
      <strong>{title}</strong>
      <span>{action}</span>
    </div>
  );
}

function GraphPanelTitle({ icon, title, action }: { icon: ReactNode; title: string; action: string }) {
  return (
    <div className="graph-panel-title">
      <div>
        {icon}
        <h2>{title}</h2>
      </div>
      <span>{action}</span>
    </div>
  );
}

function GraphViewToggle({ value, onChange }: { value: GraphViewMode; onChange: (value: GraphViewMode) => void }) {
  const { t } = useI18n();
  const options: Array<{ value: GraphViewMode; label: string; icon: ReactNode }> = [
    { value: "flow", label: t("graph.view.flow"), icon: <MessageSquareText className="h-4 w-4" /> },
    { value: "evidence", label: t("graph.view.evidence"), icon: <Eye className="h-4 w-4" /> },
    { value: "raw", label: t("graph.view.raw"), icon: <Code2 className="h-4 w-4" /> },
  ];
  return (
    <div className="graph-view-toggle" role="tablist" aria-label={t("graph.viewMode")}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={option.value === value ? "active" : ""}
          onClick={() => onChange(option.value)}
          role="tab"
          aria-selected={option.value === value}
        >
          {option.icon}
          <span>{option.label}</span>
        </button>
      ))}
    </div>
  );
}

function GraphEventRow({
  event,
  graph,
  selected,
  viewMode,
  onOpen,
}: {
  event: GraphEvent;
  graph: TranscriptGraph;
  selected: boolean;
  viewMode: GraphViewMode;
  onOpen: () => void;
}) {
  const { t } = useI18n();
  const presentation = eventPresentation(event, graph, t);
  const userInput = eventUserInput(event);
  return (
    <button
      type="button"
      className={`${selected ? "graph-event-row active" : "graph-event-row"} ${viewMode}`}
      onClick={onOpen}
    >
      <span className={`graph-role-mark ${event.role || "unknown"}`}>{shortRole(event.role)}</span>
      <span className="graph-event-body">
        <span className="graph-event-header">
          <strong>{presentation.title}</strong>
          {userInput && <span className="graph-user-input-chip">{t("graph.userInput")}</span>}
          <small>{presentation.kicker}</small>
        </span>
        {viewMode === "raw" ? (
          <pre className="graph-event-raw-inline">{event.text_excerpt || t("common.noData")}</pre>
        ) : (
          <>
            <span className={presentation.dense ? "graph-event-excerpt folded" : "graph-event-excerpt"}>
              {presentation.summary}
            </span>
            {presentation.hiddenNote && (
              <span className="graph-collapse-note">
                <Braces className="h-3.5 w-3.5" />
                {presentation.hiddenNote}
              </span>
            )}
          </>
        )}
        {viewMode !== "flow" && <code>{event.source_ref}</code>}
        {viewMode === "evidence" && (
          <span className="graph-event-trace-meta">
            {event.event_type} / {event.kind} / {event.phase}
          </span>
        )}
      </span>
      <EventBadges eventId={event.event_id} graph={graph} />
    </button>
  );
}

function GraphChain({
  groups,
  graph,
  selectedEvent,
}: {
  groups: { turn: GraphRow; events: GraphEvent[] }[];
  graph: TranscriptGraph | null;
  selectedEvent: GraphEvent | null;
}) {
  const selectedTurnId = selectedEvent?.turn_id ?? "";
  return (
    <div className="graph-chain-map" aria-label="normalized trace graph">
      {groups.slice(0, 12).map(({ turn, events }) => {
        const turnId = text(turn, "turn_id");
        const markers = chainMarkers(events, graph);
        return (
          <div className={turnId === selectedTurnId ? "graph-chain-node active" : "graph-chain-node"} key={turnId}>
            <CircleDot className="h-4 w-4" />
            <span>T{text(turn, "turn_index")}</span>
            <small>{text(turn, "phase_hint", "phase")}</small>
            <div className="graph-chain-markers">
              {markers.map((marker) => (
                <i className={marker.className} key={marker.label} title={marker.label}>
                  {marker.count}
                </i>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ConversationCoverage({ graph, event }: { graph: TranscriptGraph | null; event: GraphEvent | null }) {
  const { t } = useI18n();
  if (!graph || !event) {
    return null;
  }
  const toolCalls = rowsForEvent(graph.tool_calls, event.event_id).length;
  const hasTool = hasToolSignal(event, graph);
  const files = rowsForEvent(graph.file_refs, event.event_id).length;
  const tests = rowsForEvent(graph.test_refs, event.event_id).length;
  const errors = rowsForEvent(graph.error_refs, event.event_id).length;
  const corrections = rowsForEvent(graph.user_corrections, event.event_id).length;
  const items = [
    { label: t("graph.flow.goal"), active: Boolean(eventUserInput(event)), value: event.role === "user" ? 1 : 0 },
    { label: t("graph.flow.assistant"), active: event.role === "assistant", value: event.role === "assistant" ? 1 : 0 },
    { label: t("graph.flow.tool"), active: hasTool, value: Math.max(toolCalls, hasTool ? 1 : 0) },
    { label: t("graph.flow.validation"), active: tests > 0, value: tests },
    { label: t("graph.flow.correction"), active: corrections > 0, value: corrections },
    { label: t("graph.errors"), active: errors > 0, value: errors },
    { label: t("graph.files"), active: files > 0, value: files },
  ];
  return (
    <div className="graph-coverage-list">
      <h3>{t("graph.chainContains")}</h3>
      <div>
        {items.map((item) => (
          <span className={item.active ? "active" : ""} key={item.label}>
            <strong>{item.value}</strong>
            {item.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function EventBadges({ eventId, graph }: { eventId: string; graph: TranscriptGraph }) {
  const files = rowsForEvent(graph.file_refs, eventId).length;
  const tests = rowsForEvent(graph.test_refs, eventId).length;
  const errors = rowsForEvent(graph.error_refs, eventId).length;
  const corrections = rowsForEvent(graph.user_corrections, eventId).length;
  const toolCalls = rowsForEvent(graph.tool_calls, eventId).length;
  const badges = [
    { label: "cmd", count: toolCalls, className: "tool" },
    { label: "file", count: files, className: "file" },
    { label: "test", count: tests, className: "test" },
    { label: "err", count: errors, className: "error" },
    { label: "fix", count: corrections, className: "correction" },
  ].filter((item) => item.count > 0);
  return (
    <span className="graph-event-badges">
      {badges.map((badge) => (
        <span className={`graph-mini-badge ${badge.className}`} key={badge.label}>
          {badge.count} {badge.label}
        </span>
      ))}
    </span>
  );
}

function LineageList({ title, items }: { title: string; items: LineageEndpoint[] }) {
  const { t } = useI18n();
  return (
    <div className="graph-lineage-list">
      <h3>{title}</h3>
      {items.length > 0 ? (
        items.map((item) => {
          const summary = lineageNodeSummary(item.node, item.id, t);
          return (
            <div className="graph-lineage-item" key={`${item.type}:${item.id}:${item.relation}`}>
              <span>{item.relation}</span>
              <strong>{lineageNodeType(item.type, t)}</strong>
              <em>{summary.primary}</em>
              {summary.secondary && <code>{summary.secondary}</code>}
            </div>
          );
        })
      ) : (
        <div className="graph-muted-box compact">{t("common.noData")}</div>
      )}
    </div>
  );
}

function SelectedEventBrief({ event, graph, viewMode }: { event: GraphEvent; graph: TranscriptGraph | null; viewMode: GraphViewMode }) {
  const { t } = useI18n();
  const presentation = eventPresentation(event, graph, t);
  const userInput = eventUserInput(event);
  const profile = contentProfile(eventDisplayText(event));
  return (
    <section className="graph-selected-brief">
      <div className="graph-selected-brief-title">
        <strong>{t("graph.selectedEvent")}</strong>
        <span>{presentation.kicker}</span>
      </div>
      {(!userInput || viewMode !== "flow") && <p>{presentation.summary}</p>}
      {userInput && (
        <div className="graph-user-input-panel">
          <span>{t("graph.userInput")}</span>
          <p>{userInput}</p>
        </div>
      )}
      {presentation.hiddenNote && (
        <div className="graph-collapse-note selected">
          <Braces className="h-3.5 w-3.5" />
          {presentation.hiddenNote}
        </div>
      )}
      {viewMode !== "flow" && (
        <details className="graph-raw-details">
          <summary>{t("graph.rawExcerpt")} · {profile.lines} lines · {profile.chars} chars</summary>
          <pre>{event.text_excerpt || t("common.noData")}</pre>
        </details>
      )}
    </section>
  );
}

function EvidencePack({ evidence }: { evidence: GraphRow[] }) {
  const { t } = useI18n();
  return (
    <div className="graph-evidence-pack">
      <h3>{t("graph.evidencePack")}</h3>
      {evidence.length > 0 ? (
        evidence.map((item) => <EvidenceItem key={text(item, "source_ref") || text(item, "event_id")} item={item} />)
      ) : (
        <div className="graph-muted-box compact">{t("common.noData")}</div>
      )}
    </div>
  );
}

function EvidenceItem({ item }: { item: GraphRow }) {
  const { t } = useI18n();
  const userInput = text(item, "user_input_text");
  const raw = userInput || text(item, "text_excerpt", t("common.noData"));
  const profile = contentProfile(raw);
  return (
    <blockquote>
      <strong>{text(item, "source_ref", t("common.unknown"))}</strong>
      {userInput && <span className="graph-user-input-chip evidence">{t("graph.userInput")}</span>}
      <p>{profile.dense ? contentHiddenNote(profile, t) : readableText(raw)}</p>
      {profile.dense && (
        <details className="graph-raw-details compact">
          <summary>{t("graph.rawExcerpt")}</summary>
          <pre>{raw}</pre>
        </details>
      )}
    </blockquote>
  );
}

function SemanticRefs({
  toolCalls,
  files,
  tests,
  errors,
  corrections,
}: {
  toolCalls: GraphRow[];
  files: GraphRow[];
  tests: GraphRow[];
  errors: GraphRow[];
  corrections: GraphRow[];
}) {
  const { t } = useI18n();
  const sections = [
    {
      title: t("graph.toolCalls"),
      icon: <TerminalSquare className="h-4 w-4" />,
      rows: toolCalls,
      render: (row: GraphRow) => `${text(row, "status", "unknown")} / ${text(row, "command", "")}`,
    },
    {
      title: t("graph.files"),
      icon: <FileCode2 className="h-4 w-4" />,
      rows: files,
      render: (row: GraphRow) => `${text(row, "path_role", "ref")} / ${text(row, "path", "")}`,
    },
    {
      title: t("graph.tests"),
      icon: <TestTube className="h-4 w-4" />,
      rows: tests,
      render: (row: GraphRow) => `${text(row, "status", "unknown")} / ${text(row, "framework", "test")}`,
    },
    {
      title: t("graph.errors"),
      icon: <AlertTriangle className="h-4 w-4" />,
      rows: errors,
      render: (row: GraphRow) => `${text(row, "error_type", "error")} / ${text(row, "message", "")}`,
    },
    {
      title: t("graph.corrections"),
      icon: <UserCheck className="h-4 w-4" />,
      rows: corrections,
      render: (row: GraphRow) => `${text(row, "correction_type", "correction")} / ${text(row, "summary", "")}`,
    },
  ];
  const visibleSections = sections.filter((section) => section.rows.length > 0);
  return (
    <div className="graph-semantic-refs">
      <h3>{t("graph.semanticRefs")}</h3>
      {visibleSections.length === 0 && <div className="graph-muted-box compact">{t("graph.noSemanticRefs")}</div>}
      {visibleSections.map((section) => (
        <div className="graph-ref-group" key={section.title}>
          <div className="graph-ref-title">
            {section.icon}
            <span>{section.title}</span>
            <strong>{section.rows.length}</strong>
          </div>
          {section.rows.slice(0, 5).map((row, index) => (
            <code key={`${section.title}:${index}`}>{section.render(row)}</code>
          ))}
        </div>
      ))}
    </div>
  );
}

function rowsForEvent(rows: GraphRow[], eventId: string): GraphRow[] {
  if (!eventId) {
    return [];
  }
  return rows.filter((row) => text(row, "event_id") === eventId);
}

function includeInConversationChain(event: GraphEvent, graph: TranscriptGraph | null): boolean {
  const userInput = eventUserInput(event);
  if (!userInput && looksLikeHarnessContext(event.text_excerpt)) {
    return false;
  }
  if (eventUserInput(event)) {
    return true;
  }
  if (event.role === "assistant" && readableText(event.text_excerpt).trim()) {
    return true;
  }
  if (graph && hasToolSignal(event, graph)) {
    return true;
  }
  if (event.event_type === "context" || event.phase === "context") {
    return false;
  }
  if (event.role === "system" || event.role === "developer") {
    return false;
  }
  if (!graph) {
    return Boolean(event.text_excerpt);
  }
  return (
    rowsForEvent(graph.file_refs, event.event_id).length > 0 ||
    rowsForEvent(graph.test_refs, event.event_id).length > 0 ||
    rowsForEvent(graph.error_refs, event.event_id).length > 0 ||
    rowsForEvent(graph.user_corrections, event.event_id).length > 0
  );
}

function chainMarkers(events: GraphEvent[], graph: TranscriptGraph | null): Array<{ label: string; className: string; count: number }> {
  const counts = {
    user: 0,
    assistant: 0,
    tool: 0,
    test: 0,
    error: 0,
    correction: 0,
  };
  for (const event of events) {
    if (eventUserInput(event)) {
      counts.user += 1;
    }
    if (event.role === "assistant") {
      counts.assistant += 1;
    }
    if (graph && hasToolSignal(event, graph)) {
      counts.tool += 1;
    }
    if (graph && rowsForEvent(graph.test_refs, event.event_id).length > 0) {
      counts.test += 1;
    }
    if (graph && rowsForEvent(graph.error_refs, event.event_id).length > 0) {
      counts.error += 1;
    }
    if (graph && rowsForEvent(graph.user_corrections, event.event_id).length > 0) {
      counts.correction += 1;
    }
  }
  return [
    { label: "user", className: "user", count: counts.user },
    { label: "assistant", className: "assistant", count: counts.assistant },
    { label: "tool", className: "tool", count: counts.tool },
    { label: "test", className: "test", count: counts.test },
    { label: "error", className: "error", count: counts.error },
    { label: "correction", className: "correction", count: counts.correction },
  ].filter((item) => item.count > 0);
}

function hasToolSignal(event: GraphEvent, graph: TranscriptGraph): boolean {
  const raw = event.text_excerpt || "";
  return (
    event.role === "tool" ||
    rowsForEvent(graph.tool_calls, event.event_id).length > 0 ||
    rowsForEvent(graph.tool_results, event.event_id).length > 0 ||
    /\b(command=|exit code:|process exited with code|apply_patch|tool_call|tool result|stdout|stderr)\b/i.test(raw)
  );
}

function looksLikeHarnessContext(value: string): boolean {
  const sample = (value || "").slice(0, 2500).toLowerCase();
  if (!sample) {
    return false;
  }
  if (
    sample.includes("<environment_context>") ||
    sample.includes("<permissions instructions>") ||
    sample.includes("you are codex") ||
    sample.includes("you and the user share one workspace") ||
    sample.includes("your job is to collaborate") ||
    sample.includes("<collaboration_mode>") ||
    sample.includes("# personality")
  ) {
    return true;
  }
  if (/^\s*(model|cwd|sandbox_mode|approval_policy)=/i.test(value) && /\bcwd=|\bmodel=|\bsandbox_mode=|\bapproval_policy=/i.test(value)) {
    return true;
  }
  const signals = [
    "knowledge cutoff",
    "filesystem sandboxing",
    "sandbox_mode",
    "workspace_roots",
    "current_date",
    "developer instructions",
    "skills_instructions",
    "approval",
  ];
  return signals.reduce((count, signal) => count + (sample.includes(signal) ? 1 : 0), 0) >= 2;
}

function projectPath(session: SessionRecord): string {
  return session.project_path || "(unknown)";
}

function projectName(session: SessionRecord): string {
  const path = projectPath(session);
  return session.project_name || (path === "(unknown)" ? "(unknown)" : path.split("/").filter(Boolean).pop() || path);
}

function eventPresentation(
  event: GraphEvent,
  graph: TranscriptGraph | null,
  t: (key: string, vars?: Record<string, string | number>) => string,
): { title: string; kicker: string; summary: string; hiddenNote: string; dense: boolean } {
  const toolCall = graph ? rowsForEvent(graph.tool_calls, event.event_id)[0] : undefined;
  const toolResult = graph ? rowsForEvent(graph.tool_results, event.event_id)[0] : undefined;
  const command = text(toolCall, "command");
  const status = text(toolCall, "status") || text(toolResult, "status");
  const displayText = eventDisplayText(event);
  const profile = contentProfile(displayText);
  const title = eventTitle(event, command, t);
  const kicker = [event.phase || event.event_type, event.event_type, status].filter(Boolean).join(" / ");
  if (command) {
    return {
      title,
      kicker,
      summary: t("graph.commandSummary", { status: status || "unknown", command: compactCommand(command) }),
      hiddenNote: profile.dense ? contentHiddenNote(profile, t) : "",
      dense: profile.dense,
    };
  }
  return {
    title,
    kicker,
    summary: profile.dense ? readableText(displayText) || contentHiddenNote(profile, t) : readableText(displayText),
    hiddenNote: profile.dense ? contentHiddenNote(profile, t) : "",
    dense: profile.dense,
  };
}

function eventDisplayText(event: GraphEvent): string {
  return eventUserInput(event) || event.text_excerpt || "";
}

function eventUserInput(event: GraphEvent): string {
  if (event.role !== "user") {
    return "";
  }
  return text(event, "user_input_text") || metadataText(event, "user_input_text") || metadataText(event, "codex_prompt");
}

function metadataText(event: GraphEvent, key: string): string {
  const raw = text(event, "metadata_json");
  if (!raw) {
    return "";
  }
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const value = parsed[key];
    return value === null || value === undefined ? "" : String(value);
  } catch {
    return "";
  }
}

function eventTitle(
  event: GraphEvent,
  command: string,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  if (command) {
    return t("graph.event.command");
  }
  if (event.event_type === "context" || (event.role === "user" && !eventUserInput(event))) {
    return t("graph.event.context");
  }
  if (event.role === "user") {
    return t("graph.event.userRequest");
  }
  if (event.role === "assistant") {
    return t("graph.event.assistant");
  }
  if (event.role === "tool") {
    return t("graph.event.toolResult");
  }
  if (event.role === "system") {
    return t("graph.event.system");
  }
  return labelize(event.phase || event.event_type || event.kind || "event");
}

function contentProfile(raw: string): { dense: boolean; kind: "code" | "json" | "log" | "text"; lines: number; chars: number } {
  const value = raw || "";
  const lines = value.split(/\r?\n/).filter((line) => line.trim()).length || (value ? 1 : 0);
  const chars = value.length;
  const trimmed = value.trim();
  const jsonLike = (trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"));
  const codeSignals = [
    "```",
    "diff --git",
    "@@",
    "Traceback",
    "Exception",
    "function ",
    "class ",
    "def ",
    "const ",
    "let ",
    "import ",
    "export ",
    "</",
    "<div",
    "SELECT ",
    "INSERT ",
  ];
  const signalCount = codeSignals.reduce((count, signal) => count + (value.includes(signal) ? 1 : 0), 0);
  const symbolCount = (value.match(/[{}[\];=<>]/g) ?? []).length;
  const symbolRatio = chars > 0 ? symbolCount / chars : 0;
  const dense = chars > 700 || lines > 10 || jsonLike || signalCount >= 2 || symbolRatio > 0.1;
  const kind = jsonLike ? "json" : signalCount >= 2 || symbolRatio > 0.1 ? "code" : lines > 8 ? "log" : "text";
  return { dense, kind, lines, chars };
}

function contentHiddenNote(
  profile: { kind: "code" | "json" | "log" | "text"; lines: number; chars: number },
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  const key = profile.kind === "json" ? "graph.hiddenJson" : profile.kind === "log" ? "graph.hiddenLog" : "graph.hiddenCode";
  return t(key, { lines: profile.lines, chars: profile.chars });
}

function readableText(raw: string): string {
  const value = raw || "";
  const lines = value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !looksLikeCodeLine(line));
  const firstPass = lines.join(" ");
  const text = firstPass || value.replace(/\s+/g, " ").trim();
  return limitText(text, 260);
}

function looksLikeCodeLine(line: string): boolean {
  return (
    line.startsWith("```") ||
    line.startsWith("@@") ||
    line.startsWith("diff --git") ||
    /^[+->]{1,3}\s/.test(line) ||
    /^[{}[\]);,]+$/.test(line) ||
    /^(import|export|const|let|var|function|class|def|from|return|if|for|while|try|except|public|private)\b/.test(line) ||
    /^(\d+\s*)?[{}[\]<>=]/.test(line)
  );
}

function compactCommand(command: string): string {
  return limitText(command.replace(/\s+/g, " ").trim(), 160);
}

function limitText(value: string, max: number): string {
  if (value.length <= max) {
    return value;
  }
  return `${value.slice(0, Math.max(0, max - 1)).trim()}...`;
}

function lineageNodeSummary(
  node: GraphRow | null | undefined,
  fallbackId: string,
  t: (key: string, vars?: Record<string, string | number>) => string,
): { primary: string; secondary: string } {
  if (!node) {
    return { primary: fallbackId, secondary: "" };
  }
  const command = text(node, "command");
  if (command) {
    return { primary: compactCommand(command), secondary: text(node, "status") };
  }
  const path = text(node, "path");
  if (path) {
    return { primary: path, secondary: text(node, "path_role") || text(node, "operation") };
  }
  const message =
    text(node, "user_input_text") ||
    text(node, "message") ||
    text(node, "summary") ||
    text(node, "stdout_preview") ||
    text(node, "text_excerpt") ||
    text(node, "raw_text_preview");
  const profile = contentProfile(message);
  return {
    primary: profile.dense ? contentHiddenNote(profile, t) : readableText(message) || fallbackId,
    secondary: text(node, "source_ref") || text(node, "event_type") || text(node, "error_type"),
  };
}

function lineageNodeType(type: string, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const labels: Record<string, string> = {
    raw_record: t("graph.node.rawRecord"),
    event: t("graph.node.event"),
    tool_call: t("graph.node.toolCall"),
    tool_result: t("graph.node.toolResult"),
    file_ref: t("graph.node.fileRef"),
    test_ref: t("graph.node.testRef"),
    error_ref: t("graph.node.errorRef"),
    user_correction: t("graph.node.correction"),
  };
  return labels[type] ?? type;
}

function text(row: GraphRow | null | undefined, key: string, fallback = ""): string {
  if (!row) {
    return fallback;
  }
  const value = row[key];
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  return String(value);
}

function shortRole(role: string): string {
  if (!role) {
    return "?";
  }
  return role.slice(0, 1).toUpperCase();
}

function labelize(value: string): string {
  return value.replaceAll("_", " ");
}
