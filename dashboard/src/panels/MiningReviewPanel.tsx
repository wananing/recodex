import {
  ArrowRight,
  CheckCircle2,
  FileText,
  GitGraph,
  History,
  RefreshCw,
  Search,
  Sparkles,
  TerminalSquare,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  EmptyState,
  SectionHeader,
  SettingLine,
} from "@/components/dashboardPrimitives";
import type { MiningReviewPayload } from "@/lib/dashboardTypes";
import { formatCount, formatScore } from "@/lib/dashboardUtils";
import { useI18n } from "@/lib/i18n";

export function MiningReviewPanel({
  review,
  selectedClusterId,
  busy,
  onClusterChange,
  onRefresh,
}: {
  review: MiningReviewPayload | null;
  selectedClusterId: string;
  busy: string | null;
  onClusterChange: (value: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [readinessFilter, setReadinessFilter] = useState("all");
  const clusters = review?.clusters ?? [];
  const selectedCluster =
    clusters.find((cluster) => cluster.cluster_id === selectedClusterId) ?? review?.selected_cluster ?? null;
  const readinessOptions = [
    { value: "all", label: t("evidence.filter.all") },
    { value: "ready_for_draft", label: t("evidence.filter.draft") },
    { value: "ready_for_review", label: t("evidence.filter.review") },
    { value: "needs_more_evidence", label: t("evidence.filter.more") },
  ];
  const filteredClusters = useMemo(() => {
    const cleaned = query.trim().toLowerCase();
    return clusters.filter((cluster) => {
      if (readinessFilter !== "all" && cluster.readiness !== readinessFilter) {
        return false;
      }
      if (!cleaned) {
        return true;
      }
      return [
        cluster.title,
        cluster.cluster_type,
        cluster.common_pattern,
        cluster.readiness,
        cluster.recommended_destinations.join(" "),
        cluster.affected_repos.join(" "),
      ]
        .join(" ")
        .toLowerCase()
        .includes(cleaned);
    });
  }, [clusters, query, readinessFilter]);

  if (!review) {
    return (
      <div className="content-stack evidence-page">
        <section className="work-panel">
          <SectionHeader title={t("evidence.review")} action={t("evidence.review.action")} />
          <EmptyState label={t("evidence.loading")} />
        </section>
      </div>
    );
  }

  if (!review.exists) {
    return (
      <div className="content-stack evidence-page">
        <section className="work-panel">
          <SectionHeader title={t("evidence.review")} action={t("evidence.review.action")} />
          <EmptyState label={t("evidence.missing")} />
          <div className="terminal-block">
            <TerminalSquare className="h-4 w-4" />
            <code>PYTHONPATH=src python3 -m recodex mine</code>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="content-stack evidence-page evidence-workbench">
      <section className="evidence-command-center">
        <div className="evidence-focus">
          <div className="focus-kicker">
            <span className="status-dot ok" />
            {t("evidence.focus")}
          </div>
          <h2>{selectedCluster?.title ?? t("evidence.selected")}</h2>
          <p>{selectedCluster?.common_pattern ?? t("evidence.emptyFocus")}</p>
          <div className="focus-meta-row">
            <span>{selectedCluster?.readiness ?? t("common.noData")}</span>
            <span>{t("evidence.frequencyValue", { count: selectedCluster?.frequency ?? 0 })}</span>
            <span>{t("evidence.priorityValue", { score: selectedCluster?.priority_score ?? 0 })}</span>
          </div>
        </div>
        <div className="evidence-stat-strip" aria-label={t("evidence.coverage")}>
          <div>
            <History className="h-4 w-4" />
            <strong>{formatCount(review.coverage.sessions)}</strong>
            <span>{t("evidence.metric.sessions")}</span>
          </div>
          <div>
            <GitGraph className="h-4 w-4" />
            <strong>{formatCount(review.coverage.episodes)}</strong>
            <span>{t("evidence.metric.episodes")}</span>
          </div>
          <div>
            <FileText className="h-4 w-4" />
            <strong>{formatCount(review.coverage.analysis_cards)}</strong>
            <span>{t("evidence.metric.cards")}</span>
          </div>
          <div>
            <Sparkles className="h-4 w-4" />
            <strong>{formatCount(review.coverage.clusters ?? clusters.length)}</strong>
            <span>{t("evidence.metric.clusters")}</span>
          </div>
          <div>
            <CheckCircle2 className="h-4 w-4" />
            <strong>{formatCount(review.coverage.ready_for_review_clusters)}</strong>
            <span>{t("evidence.metric.ready")}</span>
          </div>
        </div>
      </section>

      <section className="work-panel evidence-toolbar-panel">
        <SectionHeader title={t("evidence.review")} action={review.base_dir} />
        <div className="evidence-toolbar">
          <div className="search-strip">
            <Search className="h-4 w-4" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("evidence.searchClusters")}
            />
          </div>
          <div className="readiness-filter" aria-label={t("evidence.filters")}>
            {readinessOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className={readinessFilter === option.value ? "active" : ""}
                onClick={() => setReadinessFilter(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="secondary-command"
            disabled={busy === "mining-review-load"}
            onClick={() => void onRefresh()}
          >
            <RefreshCw className="h-4 w-4" />
            {t("common.refresh")}
          </button>
        </div>
      </section>

      <section className="evidence-review-grid">
        <div className="work-panel evidence-queue-panel">
          <SectionHeader title={t("evidence.clusters")} action={t("evidence.clusters.action")} />
          <div className="evidence-cluster-list">
            {filteredClusters.length > 0 ? (
              filteredClusters.map((cluster, index) => (
                <button
                  key={cluster.cluster_id}
                  type="button"
                  className={
                    cluster.cluster_id === selectedCluster?.cluster_id
                      ? "evidence-cluster-row active"
                      : "evidence-cluster-row"
                  }
                  onClick={() => onClusterChange(cluster.cluster_id)}
                >
                  <span className="review-rank">{index + 1}</span>
                  <div>
                    <div className="line-title">{cluster.title}</div>
                    <div className="line-subtitle">
                      {cluster.common_pattern}
                    </div>
                  </div>
                  <div className="evidence-cluster-score">
                    <strong>{cluster.priority_score}</strong>
                    <span>{cluster.frequency}x</span>
                  </div>
                  <ArrowRight className="h-4 w-4" />
                  <div className="evidence-cluster-tags">
                    <span>{cluster.readiness}</span>
                    <span>{cluster.cluster_type}</span>
                    {cluster.recommended_destinations.slice(0, 3).map((destination) => (
                      <span key={destination}>{destination}</span>
                    ))}
                  </div>
                </button>
              ))
            ) : (
              <EmptyState label={t("evidence.noClusters")} />
            )}
          </div>
        </div>

        <div className="work-panel evidence-detail-panel">
          <SectionHeader
            title={selectedCluster?.title ?? t("evidence.selected")}
            action={selectedCluster?.readiness ?? t("common.noData")}
          />
          {selectedCluster ? (
            <div className="evidence-detail-stack">
              <div className="evidence-pattern">
                <strong>{t("evidence.commonPattern")}</strong>
                <p>{selectedCluster.common_pattern}</p>
              </div>
              <div className="destination-strip">
                {selectedCluster.recommended_destinations.map((destination) => (
                  <span key={destination}>{destination}</span>
                ))}
              </div>
              <div className="evidence-meta-grid">
                <SettingLine label={t("evidence.frequency")} value={String(selectedCluster.frequency)} />
                <SettingLine label={t("evidence.priority")} value={String(selectedCluster.priority_score)} />
                <SettingLine label={t("evidence.cards")} value={String(review.cards.length)} />
                <SettingLine
                  label={t("evidence.repos")}
                  value={String(selectedCluster.affected_repos.length)}
                />
              </div>
              <div className="evidence-card-list">
                <SectionHeader
                  title={t("evidence.cards")}
                  action={t("evidence.cards.action", { count: review.cards.length })}
                />
                {review.cards.length > 0 ? (
                  review.cards.map((card) => (
                    <article className="evidence-card" key={card.card_id}>
                      <div className="evidence-card-header">
                        <div>
                          <div className="line-title">{card.title}</div>
                          <div className="line-subtitle">
                            {card.card_id} / {card.card_type} / {card.candidate_destination}
                          </div>
                        </div>
                        <span>{formatScore(card.quality_score ?? card.confidence)}</span>
                      </div>
                      <div className="evidence-card-body">
                        <div>
                          <strong>{t("evidence.observed")}</strong>
                          <p>{card.observed_fact}</p>
                        </div>
                        <div>
                          <strong>{t("evidence.inferred")}</strong>
                          <p>{card.inferred_problem}</p>
                        </div>
                      </div>
                      <div className="event-id-row">
                        <span>{t("evidence.eventIds")}</span>
                        {card.evidence_event_ids.slice(0, 4).map((eventId) => (
                          <code key={eventId}>{eventId}</code>
                        ))}
                      </div>
                    </article>
                  ))
                ) : (
                  <EmptyState label={t("evidence.noCards")} />
                )}
              </div>
            </div>
          ) : (
            <EmptyState label={t("evidence.noClusters")} />
          )}
        </div>
      </section>
    </div>
  );
}
