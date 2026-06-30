import type { LucideIcon } from "lucide-react";

export function Metric({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="metric-tile">
      <Icon className="h-5 w-5" />
      <div>
        <div className="metric-value">{value}</div>
        <div className="metric-label">{label}</div>
      </div>
      <span>{detail}</span>
    </div>
  );
}

export function SectionHeader({ title, action }: { title: string; action: string }) {
  return (
    <div className="section-header">
      <h2>{title}</h2>
      <span title={action}>{action}</span>
    </div>
  );
}

export function StatusPill({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
}) {
  return (
    <div className="status-pill">
      <Icon className="h-4 w-4" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function SettingLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="setting-line">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function EmptyState({ label }: { label: string }) {
  return <div className="empty-state">{label}</div>;
}
