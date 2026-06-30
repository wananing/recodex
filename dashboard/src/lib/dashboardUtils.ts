import {
  Database,
  FileText,
  Settings,
  TerminalSquare,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { providerCapabilityDefinitions } from "@/lib/dashboardConfig";
import type { ProviderAsset, ProviderRecord, SessionRecord } from "@/lib/dashboardTypes";

export function providerCapabilityCount(provider: ProviderRecord): number {
  return providerCapabilityDefinitions.filter((definition) => provider.capabilities[definition.key]).length;
}

export function providerAssetSearchText(asset: ProviderAsset): string {
  return [
    asset.name,
    asset.asset_type,
    asset.scope,
    asset.path ?? "",
    asset.project_path ?? "",
    asset.description ?? "",
    asset.tags.join(" "),
  ].join(" ").toLowerCase();
}

export function providerAssetIcon(assetType: string): LucideIcon {
  if (assetType === "skills") {
    return Zap;
  }
  if (assetType === "mcp" || assetType === "commands" || assetType === "hooks") {
    return TerminalSquare;
  }
  if (assetType === "config" || assetType === "rules") {
    return Settings;
  }
  if (assetType === "memories" || assetType === "plans") {
    return Database;
  }
  return FileText;
}

export function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  const kb = value / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(kb >= 10 ? 0 : 1)} KB`;
  }
  const mb = kb / 1024;
  return `${mb.toFixed(mb >= 10 ? 0 : 1)} MB`;
}

export function formatScore(value: number | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  return value.toFixed(value >= 10 ? 0 : 1);
}

export function formatCount(value: number | string | undefined): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) {
    return "0";
  }
  if (numeric >= 1_000_000) {
    return `${(numeric / 1_000_000).toFixed(1)}m`;
  }
  if (numeric >= 10_000) {
    return `${(numeric / 1_000).toFixed(1)}k`;
  }
  return String(numeric);
}

export function projectPath(session: SessionRecord): string {
  return session.project_path || "(unknown)";
}

export function projectName(session: SessionRecord): string {
  const path = projectPath(session);
  if (session.project_name) {
    return session.project_name;
  }
  return path === "(unknown)" ? "(unknown)" : path.split("/").filter(Boolean).pop() || path;
}

export function groupSessionsByProject(sessions: SessionRecord[]): Array<{
  projectPath: string;
  projectName: string;
  sessions: SessionRecord[];
}> {
  const groups = new Map<string, { projectPath: string; projectName: string; sessions: SessionRecord[] }>();
  for (const session of sessions) {
    const key = projectPath(session);
    const group = groups.get(key) ?? { projectPath: key, projectName: projectName(session), sessions: [] };
    group.sessions.push(session);
    groups.set(key, group);
  }
  return Array.from(groups.values());
}

export function statusClass(status: string): string {
  if (status === "accepted") {
    return "accepted";
  }
  if (status === "rejected") {
    return "rejected";
  }
  return "proposed";
}
