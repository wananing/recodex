import {
  Bot,
  Eye,
  FileText,
  FolderInput,
  Gauge,
  GitGraph,
  History,
  PackageCheck,
  Settings,
  TerminalSquare,
  Zap,
} from "lucide-react";

import type {
  ArtifactType,
  ConflictPolicy,
  NavItem,
  PanelId,
  ProviderAssetType,
  ProviderCapabilities,
  SkillTarget,
  SourceType,
} from "@/lib/dashboardTypes";

export const navItems: NavItem[] = [
  { id: "overview", labelKey: "nav.overview", hintKey: "nav.overview.hint", groupKey: "nav.group.review", icon: Gauge },
  { id: "evidence", labelKey: "nav.evidence", hintKey: "nav.evidence.hint", groupKey: "nav.group.review", icon: Eye },
  { id: "providers", labelKey: "nav.providers", hintKey: "nav.providers.hint", groupKey: "nav.group.sources", icon: TerminalSquare },
  { id: "sessions", labelKey: "nav.sessions", hintKey: "nav.sessions.hint", groupKey: "nav.group.sources", icon: History },
  { id: "graph", labelKey: "nav.graph", hintKey: "nav.graph.hint", groupKey: "nav.group.sources", icon: GitGraph },
  { id: "reports", labelKey: "nav.reports", hintKey: "nav.reports.hint", groupKey: "nav.group.outputs", icon: FileText },
  { id: "artifacts", labelKey: "nav.artifacts", hintKey: "nav.artifacts.hint", groupKey: "nav.group.outputs", icon: PackageCheck },
  { id: "skills", labelKey: "nav.skills", hintKey: "nav.skills.hint", groupKey: "nav.group.outputs", icon: Zap },
  { id: "ingest", labelKey: "nav.ingest", hintKey: "nav.ingest.hint", groupKey: "nav.group.setup", icon: FolderInput },
  { id: "llm", labelKey: "nav.llm", hintKey: "nav.llm.hint", groupKey: "nav.group.setup", icon: Bot },
  { id: "settings", labelKey: "nav.settings", hintKey: "nav.settings.hint", groupKey: "nav.group.setup", icon: Settings },
];

export const panelIds: PanelId[] = navItems.map((item) => item.id);
export const sourceOptions: SourceType[] = ["auto", "codex", "claude-code", "cursor"];
export const targetOptions: SkillTarget[] = ["project", "codex", "cursor", "last", "custom"];
export const conflictOptions: ConflictPolicy[] = ["rename", "skip", "overwrite"];
export const providerAssetOptions: ProviderAssetType[] = [
  "all",
  "instructions",
  "skills",
  "mcp",
  "config",
  "rules",
  "hooks",
  "commands",
  "plans",
  "memories",
];

export const providerCapabilityDefinitions: Array<{
  key: keyof ProviderCapabilities;
  labelKey: string;
}> = [
  { key: "has_sessions", labelKey: "providers.cap.sessions" },
  { key: "has_session_search", labelKey: "providers.cap.search" },
  { key: "has_instructions", labelKey: "providers.cap.instructions" },
  { key: "has_skills", labelKey: "providers.cap.skills" },
  { key: "has_mcp_servers", labelKey: "providers.cap.mcp" },
  { key: "has_config", labelKey: "providers.cap.config" },
  { key: "has_rules", labelKey: "providers.cap.rules" },
  { key: "has_hooks", labelKey: "providers.cap.hooks" },
  { key: "has_commands", labelKey: "providers.cap.commands" },
  { key: "has_plans", labelKey: "providers.cap.plans" },
  { key: "has_memories", labelKey: "providers.cap.memories" },
];

export const artifactOptions: { value: ArtifactType; label: string }[] = [
  { value: "skill", label: "SKILL.md" },
  { value: "markdown", label: "Markdown" },
  { value: "agents", label: "AGENTS patch" },
  { value: "checklist", label: "Checklist" },
  { value: "ci", label: "CI rule" },
];
