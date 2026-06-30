import type { LucideIcon } from "lucide-react";

export type PanelId =
  | "overview"
  | "ingest"
  | "providers"
  | "sessions"
  | "graph"
  | "evidence"
  | "reports"
  | "artifacts"
  | "skills"
  | "llm"
  | "settings";

export type SourceType = "auto" | "codex" | "claude-code" | "cursor";
export type SkillTarget = "project" | "codex" | "cursor" | "last" | "custom";
export type ConflictPolicy = "rename" | "skip" | "overwrite";
export type ArtifactType = "skill" | "markdown" | "agents" | "checklist" | "ci";
export type ProviderAssetType =
  | "all"
  | "instructions"
  | "skills"
  | "mcp"
  | "config"
  | "rules"
  | "hooks"
  | "commands"
  | "plans"
  | "memories";

export type NavItem = {
  id: PanelId;
  labelKey: string;
  hintKey: string;
  groupKey: string;
  icon: LucideIcon;
};

export type OverviewPayload = {
  ok: boolean;
  sessions: number;
  catalog_sessions?: number;
  projects: number;
  catalog_projects?: number;
  improvements: {
    proposed: number;
    accepted: number;
  };
  watch_sources: number;
};

export type SessionRecord = {
  session_id: string;
  source: string | null;
  title: string;
  updated_at: string | null;
  command_count: number;
  error_count: number;
  imported?: boolean;
  started_at?: string | null;
  model?: string | null;
  source_path?: string | null;
  file_size?: number;
  project_id?: string;
  project_path?: string | null;
  project_name?: string | null;
};

export type SessionSearchMatch = {
  event_index: number;
  role: string;
  kind: string;
  created_at: string | null;
  text: string;
};

export type SessionSearchResult = {
  session: SessionRecord;
  matches: SessionSearchMatch[];
};

export type ProjectRecord = {
  project_id: string;
  project_path: string;
  project_name: string;
  session_count: number;
  catalog_session_count?: number;
  command_count: number;
  error_count: number;
  total_bytes?: number;
  latest_at: string | null;
  sources: string[];
};

export type ProviderCapabilities = {
  has_sessions: boolean;
  has_instructions: boolean;
  has_config: boolean;
  has_skills: boolean;
  has_plans: boolean;
  has_hooks: boolean;
  has_commands: boolean;
  has_rules: boolean;
  has_memories: boolean;
  has_session_search: boolean;
  has_mcp_servers: boolean;
};

export type ProviderRecord = {
  id: string;
  name: string;
  home_path: string;
  detected: boolean;
  capabilities: ProviderCapabilities;
};

export type ProviderAsset = {
  id: string;
  provider_id: string;
  asset_type: string;
  name: string;
  path: string | null;
  scope: string;
  project_path: string | null;
  description: string | null;
  modified_at: string | null;
  size_bytes: number | null;
  tags: string[];
  metadata: Record<string, unknown>;
};

export type MiningCluster = {
  cluster_id: string;
  cluster_type: string;
  title: string;
  common_pattern: string;
  frequency: number;
  priority_score: number;
  readiness: string;
  recommended_destinations: string[];
  affected_repos: string[];
  card_ids: string[];
  card_count?: number;
};

export type MiningCard = {
  card_id: string;
  title: string;
  card_type: string;
  observed_fact: string;
  inferred_problem: string;
  candidate_destination: string;
  evidence_event_ids: string[];
  quality_score?: number;
  confidence?: number;
};

export type MiningReviewPayload = {
  ok: boolean;
  exists: boolean;
  base_dir: string;
  coverage: Record<string, string | number>;
  clusters: MiningCluster[];
  review_queue: Array<Record<string, unknown>>;
  selected_cluster: MiningCluster | null;
  cards: MiningCard[];
  coverage_report: string;
};

export type WatchSourceRecord = {
  id: number;
  source: string;
  path: string;
  scope: string | null;
  enabled: boolean;
  last_sync_at: string | null;
  last_imported: number;
  last_skipped: number;
  last_failed: number;
  last_error: string | null;
};

export type ImprovementRecord = {
  id: number;
  fingerprint: string;
  session_id: string | null;
  mechanism: string;
  title: string;
  evidence: string;
  recommendation: string;
  status: string;
  created_at: string;
  reviewed_at: string | null;
};

export type ArtifactFile = {
  path: string;
  content: string;
};

export type ArtifactPreview = {
  ok: boolean;
  artifact_type: ArtifactType | string;
  improvement_id: number | null;
  files: ArtifactFile[];
};

export type ExportResponse = {
  ok: boolean;
  artifact_type: string;
  paths: string[];
};
