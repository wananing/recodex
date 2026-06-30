import { AlertTriangle, FileCode2, MessageSquareText, Search, TerminalSquare, X } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState, SectionHeader } from "@/components/dashboardPrimitives";
import { DashboardSelect } from "@/components/DashboardSelect";
import { getJson } from "@/lib/recodexClient";
import type { ProjectRecord, SessionRecord, SessionSearchResult } from "@/lib/dashboardTypes";
import { groupSessionsByProject, projectPath } from "@/lib/dashboardUtils";
import { useI18n } from "@/lib/i18n";

type TranscriptEvent = {
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

type SessionGraph = {
  ok: boolean;
  events: TranscriptEvent[];
  tool_calls: Array<{ event_id: string; command?: string; status?: string }>;
  file_refs: Array<{ event_id: string; path?: string; path_role?: string }>;
  error_refs: Array<{ event_id: string; message?: string; error_type?: string }>;
};

export function SessionsPanel({
  sessions,
  projects,
}: {
  sessions: SessionRecord[];
  projects: ProjectRecord[];
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [selectedProject, setSelectedProject] = useState("all");
  const [contentResults, setContentResults] = useState<SessionSearchResult[]>([]);
  const [contentError, setContentError] = useState("");
  const [contentBusy, setContentBusy] = useState(false);
  const [drawerSession, setDrawerSession] = useState<SessionRecord | null>(null);
  const [drawerGraph, setDrawerGraph] = useState<SessionGraph | null>(null);
  const [drawerBusy, setDrawerBusy] = useState(false);
  const [drawerError, setDrawerError] = useState("");
  const filteredSessions = useMemo(() => {
    const cleaned = query.trim().toLowerCase();
    return sessions.filter((session) => {
      if (selectedProject !== "all" && projectPath(session) !== selectedProject) {
        return false;
      }
      if (!cleaned) {
        return true;
      }
      return [
        session.title,
        session.session_id,
        session.source ?? "",
        session.updated_at ?? "",
        projectPath(session),
        session.project_name ?? "",
      ].some((value) => value.toLowerCase().includes(cleaned));
    });
  }, [query, selectedProject, sessions]);
  const groupedSessions = useMemo(() => groupSessionsByProject(filteredSessions), [filteredSessions]);

  async function runContentSearch() {
    const cleaned = query.trim();
    if (!cleaned) {
      setContentResults([]);
      setContentError(t("sessions.searchEmpty"));
      return;
    }
    setContentBusy(true);
    setContentError("");
    const result = await getJson<{ ok: boolean; results: SessionSearchResult[] }>(
      `/sessions/search?q=${encodeURIComponent(cleaned)}`,
    );
    setContentBusy(false);
    if (!result.ok) {
      setContentError(result.message);
      return;
    }
    const rows = result.data.results ?? [];
    setContentResults(rows);
    if (rows.length === 0) {
      setContentError(t("sessions.contentEmpty"));
    }
  }

  async function openSessionDrawer(session: SessionRecord) {
    setDrawerSession(session);
    setDrawerGraph(null);
    setDrawerError("");
    setDrawerBusy(true);
    const result = await getJson<SessionGraph>(`/transcripts/${encodeURIComponent(session.session_id)}/graph`);
    setDrawerBusy(false);
    if (!result.ok) {
      setDrawerError(result.message);
      return;
    }
    setDrawerGraph(result.data);
  }

  function closeSessionDrawer() {
    setDrawerSession(null);
    setDrawerGraph(null);
    setDrawerError("");
    setDrawerBusy(false);
  }

  return (
    <div className="content-stack">
      <section className="work-panel">
        <SectionHeader title={t("sessions.index")} action={t("sessions.index.action")} />
        <div className="session-filter-grid">
          <div className="field">
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
          <div className="search-strip">
            <Search className="h-4 w-4" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void runContentSearch();
                }
              }}
              placeholder={t("sessions.search")}
            />
          </div>
        </div>
        <div className="command-row">
          <button
            type="button"
            className="secondary-command"
            disabled={contentBusy}
            onClick={() => void runContentSearch()}
          >
            <Search className="h-4 w-4" />
            {t("sessions.contentSearch")}
          </button>
        </div>
        {(contentResults.length > 0 || contentError) && (
          <div className="session-search-results">
            {contentResults.length > 0 ? (
              contentResults.map((result) => (
                <button
                  type="button"
                  className="session-search-result session-search-result-button"
                  key={result.session.session_id}
                  onClick={() => void openSessionDrawer(result.session)}
                >
                  <div>
                    <div className="line-title">{result.session.title}</div>
                    <div className="line-subtitle">
                      {result.session.session_id} / {result.session.project_name ?? t("common.unknown")}
                    </div>
                  </div>
                  <div className="session-match-list">
                    {result.matches.slice(0, 3).map((match) => (
                      <div className="session-match" key={`${result.session.session_id}-${match.event_index}`}>
                        <span>{match.role}/{match.kind} #{match.event_index}</span>
                        <p>{match.text}</p>
                      </div>
                    ))}
                  </div>
                </button>
              ))
            ) : (
              <EmptyState label={contentError} />
            )}
          </div>
        )}
        <div className="session-list">
          {groupedSessions.length > 0 ? (
            groupedSessions.map((group) => (
              <section className="project-session-group" key={group.projectPath}>
                <div className="project-group-header">
                  <div>
                    <strong>{group.projectName}</strong>
                    <span>{group.projectPath}</span>
                  </div>
                  <b>{group.sessions.length}</b>
                </div>
                {group.sessions.map((session) => (
                  <button
                    type="button"
                    className={drawerSession?.session_id === session.session_id ? "session-item session-item-button active" : "session-item session-item-button"}
                    key={session.session_id}
                    onClick={() => void openSessionDrawer(session)}
                  >
                    <div>
                      <div className="line-title">{session.title}</div>
                      <div className="line-subtitle">
                        {session.session_id} / {session.source ?? t("common.unknown")} /{" "}
                        {session.updated_at ?? t("common.unknown")}
                      </div>
                    </div>
                    <div className="session-stats">
                      <span>
                        {session.command_count} {t("sessions.cmd")}
                      </span>
                      <span>
                        {session.error_count} {t("sessions.err")}
                      </span>
                    </div>
                  </button>
                ))}
              </section>
            ))
          ) : (
            <EmptyState label={sessions.length > 0 ? t("sessions.filteredEmpty") : t("common.noSessions")} />
          )}
        </div>
      </section>
      {drawerSession && (
        <SessionContentDrawer
          session={drawerSession}
          graph={drawerGraph}
          busy={drawerBusy}
          error={drawerError}
          onClose={closeSessionDrawer}
        />
      )}
    </div>
  );
}

function SessionContentDrawer({
  session,
  graph,
  busy,
  error,
  onClose,
}: {
  session: SessionRecord;
  graph: SessionGraph | null;
  busy: boolean;
  error: string;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const visibleEvents = (graph?.events ?? []).filter(isUsefulSessionEvent).slice(0, 240);
  const stats = [
    { icon: MessageSquareText, label: t("sessions.drawer.events"), value: graph?.events.length ?? 0 },
    { icon: TerminalSquare, label: t("graph.toolCalls"), value: graph?.tool_calls.length ?? 0 },
    { icon: FileCode2, label: t("graph.files"), value: graph?.file_refs.length ?? 0 },
    { icon: AlertTriangle, label: t("graph.errors"), value: graph?.error_refs.length ?? 0 },
  ];
  return (
    <div className="session-drawer-layer" role="presentation">
      <button type="button" className="session-drawer-scrim" aria-label={t("sessions.drawer.close")} onClick={onClose} />
      <aside className="session-drawer" role="dialog" aria-modal="true" aria-labelledby="session-drawer-title">
        <header className="session-drawer-header">
          <div>
            <span>{t("sessions.drawer.title")}</span>
            <h2 id="session-drawer-title">{session.title || session.session_id}</h2>
            <p>{session.session_id} / {session.source ?? t("common.unknown")} / {session.updated_at ?? t("common.unknown")}</p>
          </div>
          <button type="button" className="icon-command" onClick={onClose} aria-label={t("sessions.drawer.close")}>
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="session-drawer-stats">
          {stats.map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.label}>
                <Icon className="h-4 w-4" />
                <strong>{item.value}</strong>
                <span>{item.label}</span>
              </div>
            );
          })}
        </div>

        <div className="session-drawer-body">
          {busy && <EmptyState label={t("sessions.drawer.loading")} />}
          {!busy && error && <EmptyState label={error} />}
          {!busy && !error && visibleEvents.length === 0 && <EmptyState label={t("sessions.drawer.empty")} />}
          {!busy && !error && visibleEvents.length > 0 && (
            <div className="session-message-list">
              {visibleEvents.map((event) => (
                <SessionMessage event={event} graph={graph} key={event.event_id} />
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function SessionMessage({ event, graph }: { event: TranscriptEvent; graph: SessionGraph | null }) {
  const { t } = useI18n();
  const tools = rowsForEvent(graph?.tool_calls ?? [], event.event_id);
  const files = rowsForEvent(graph?.file_refs ?? [], event.event_id);
  const errors = rowsForEvent(graph?.error_refs ?? [], event.event_id);
  const text = eventDisplayText(event);
  const profile = textProfile(text);
  return (
    <article className={`session-message role-${roleClass(event.role)}`}>
      <div className="session-message-meta">
        <span>{event.role || t("common.unknown")}</span>
        <strong>#{event.event_index}</strong>
        <small>{event.phase || event.kind || event.event_type}</small>
      </div>
      <div className="session-message-content">
        <p>{profile.dense ? compactText(text, 900) : text}</p>
        {(tools.length > 0 || files.length > 0 || errors.length > 0) && (
          <div className="session-message-tags">
            {tools.slice(0, 2).map((row, index) => (
              <code key={`tool-${index}`}>{row.command || row.status || t("graph.toolCalls")}</code>
            ))}
            {files.slice(0, 3).map((row, index) => (
              <code key={`file-${index}`}>{row.path || row.path_role || t("graph.files")}</code>
            ))}
            {errors.slice(0, 2).map((row, index) => (
              <code className="error" key={`error-${index}`}>{row.message || row.error_type || t("graph.errors")}</code>
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

function rowsForEvent<T extends { event_id: string }>(rows: T[], eventId: string): T[] {
  return rows.filter((row) => row.event_id === eventId);
}

function eventDisplayText(event: TranscriptEvent): string {
  return event.user_input_text || event.text_excerpt || "";
}

function isUsefulSessionEvent(event: TranscriptEvent): boolean {
  const text = eventDisplayText(event);
  if (!text.trim()) {
    return false;
  }
  if (event.role === "system" || event.role === "developer") {
    return false;
  }
  if (event.event_type === "context" && looksLikeHarnessContext(text)) {
    return false;
  }
  return !looksLikeHarnessContext(text);
}

function looksLikeHarnessContext(value: string): boolean {
  const sample = value.slice(0, 2500).toLowerCase();
  if (
    sample.includes("<environment_context>") ||
    sample.includes("<permissions instructions>") ||
    sample.includes("you are codex") ||
    sample.includes("you and the user share one workspace") ||
    sample.includes("<collaboration_mode>") ||
    sample.includes("# personality")
  ) {
    return true;
  }
  if (/^\s*(model|cwd|sandbox_mode|approval_policy)=/i.test(value) && /\bcwd=|\bmodel=|\bsandbox_mode=|\bapproval_policy=/i.test(value)) {
    return true;
  }
  return ["knowledge cutoff", "filesystem sandboxing", "sandbox_mode", "workspace_roots", "developer instructions"].filter((signal) =>
    sample.includes(signal),
  ).length >= 2;
}

function compactText(value: string, max: number): string {
  const cleaned = value.replace(/\s+/g, " ").trim();
  return cleaned.length <= max ? cleaned : `${cleaned.slice(0, max - 1).trim()}…`;
}

function textProfile(value: string): { dense: boolean } {
  const lines = value.split(/\r?\n/).length;
  return { dense: value.length > 1200 || lines > 16 };
}

function roleClass(role: string): string {
  return role ? role.replace(/[^a-z0-9_-]/gi, "-").toLowerCase() : "unknown";
}
