import { useMemo } from "react";

import { navItems } from "@/lib/dashboardConfig";
import type { PanelId } from "@/lib/dashboardTypes";
import { useI18n } from "@/lib/i18n";

type AppSidebarProps = {
  activePanel: PanelId;
  onPanelChange: (panel: PanelId) => void;
};

export function AppSidebar({ activePanel, onPanelChange }: AppSidebarProps) {
  const { t } = useI18n();
  const primaryNavItems = useMemo(
    () => navItems.filter((item) => ["overview", "sessions", "graph", "reports", "evidence", "artifacts"].includes(item.id)),
    [],
  );
  const utilityNavItems = useMemo(
    () => navItems.filter((item) => ["ingest", "providers", "skills", "llm", "settings"].includes(item.id)),
    [],
  );

  return (
    <aside className="recodex-sidebar">
      <div className="brand-lockup">
        <span className="brand-lockup-logo" aria-hidden="true">
          <img src="/logo/logolight.png?v=20260621" alt="" />
        </span>
        <div className="brand-copy">
          <div className="brand-title">{t("app.title")}</div>
          <div className="brand-subtitle">{t("app.subtitle")}</div>
        </div>
      </div>

      <nav className="nav-stack" aria-label="Primary">
        <div className="nav-section">
          <div className="nav-section-label">{t("nav.group.workflow")}</div>
          {primaryNavItems.map((item, index) => {
            const Icon = item.icon;
            const label = t(item.labelKey);
            return (
              <button
                key={item.id}
                type="button"
                className={item.id === activePanel ? "nav-button active" : "nav-button"}
                onClick={() => onPanelChange(item.id)}
                title={t(item.hintKey)}
              >
                <Icon className="h-4 w-4" />
                <span>{label}</span>
                <small>{index === 0 ? t(item.hintKey) : `${index}. ${t(item.hintKey)}`}</small>
              </button>
            );
          })}
        </div>

        <div className="nav-section nav-section-compact">
          <div className="nav-section-label">{t("nav.group.advanced")}</div>
          <div className="nav-utility-grid">
            {utilityNavItems.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={item.id === activePanel ? "nav-utility active" : "nav-utility"}
                  onClick={() => onPanelChange(item.id)}
                  title={t(item.hintKey)}
                >
                  <Icon className="h-4 w-4" />
                  <span>{t(item.labelKey)}</span>
                </button>
              );
            })}
          </div>
        </div>
      </nav>

      <div className="sidebar-footer">
        <div className="system-heading">
          <span className="status-dot ok" />
          <span>System Status</span>
        </div>
        <p className="system-copy">{t("status.cli")}</p>
        <button type="button" className="status-button" onClick={() => onPanelChange("settings")}>
          View Status
        </button>
        <div className="sidebar-version">{t("app.version")} / {t("app.mode")}</div>
      </div>
    </aside>
  );
}
