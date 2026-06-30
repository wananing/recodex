import { RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState, SectionHeader } from "@/components/dashboardPrimitives";
import { DashboardSelect } from "@/components/DashboardSelect";
import { providerAssetOptions, providerCapabilityDefinitions } from "@/lib/dashboardConfig";
import type { ProviderAsset, ProviderAssetType, ProviderRecord } from "@/lib/dashboardTypes";
import {
  formatBytes,
  providerAssetIcon,
  providerAssetSearchText,
  providerCapabilityCount,
} from "@/lib/dashboardUtils";
import { useI18n } from "@/lib/i18n";

export function ProvidersPanel({
  providers,
  assets,
  selectedProviderId,
  assetType,
  busy,
  onProviderChange,
  onAssetTypeChange,
  onLoadProviders,
  onLoadAssets,
}: {
  providers: ProviderRecord[];
  assets: ProviderAsset[];
  selectedProviderId: string;
  assetType: ProviderAssetType;
  busy: string | null;
  onProviderChange: (value: string) => void;
  onAssetTypeChange: (value: ProviderAssetType) => void;
  onLoadProviders: () => Promise<void>;
  onLoadAssets: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const selectedProvider = providers.find((provider) => provider.id === selectedProviderId) ?? providers[0];
  const capabilityRows = selectedProvider
    ? providerCapabilityDefinitions.map((definition) => ({
        ...definition,
        enabled: selectedProvider.capabilities[definition.key],
      }))
    : [];
  const visibleAssets = useMemo(() => {
    const cleaned = query.trim().toLowerCase();
    if (!cleaned) {
      return assets;
    }
    return assets.filter((asset) => providerAssetSearchText(asset).includes(cleaned));
  }, [assets, query]);

  return (
    <div className="content-stack providers-page">
      <section className="work-panel">
        <SectionHeader title={t("providers.discovery")} action={t("providers.discovery.action")} />
        <div className="form-grid provider-form">
          <div className="field">
            <span>{t("providers.provider")}</span>
            <DashboardSelect
              value={selectedProviderId}
              options={providers.map((provider) => ({ value: provider.id, label: provider.name }))}
              onChange={onProviderChange}
              ariaLabel={t("providers.provider")}
            />
          </div>
          <div className="field">
            <span>{t("providers.assetType")}</span>
            <DashboardSelect
              value={assetType}
              options={providerAssetOptions.map((option) => ({
                value: option,
                label: t(`providers.asset.${option}`),
              }))}
              onChange={(value) => onAssetTypeChange(value as ProviderAssetType)}
              ariaLabel={t("providers.assetType")}
            />
          </div>
          <div className="command-row provider-refresh-row">
            <button
              type="button"
              className="secondary-command"
              disabled={busy === "providers-load"}
              onClick={() => void onLoadProviders()}
            >
              <RefreshCw className="h-4 w-4" />
              {t("providers.refreshProviders")}
            </button>
            <button
              type="button"
              className="primary-command"
              disabled={busy === "provider-assets-load"}
              onClick={() => void onLoadAssets()}
            >
              <Search className="h-4 w-4" />
              {t("providers.loadAssets")}
            </button>
          </div>
        </div>
        {selectedProvider ? (
          <div className="provider-summary-strip">
            <span className={`badge ${selectedProvider.detected ? "accepted" : "rejected"}`}>
              {selectedProvider.detected ? t("providers.detected") : t("providers.notDetected")}
            </span>
            <code title={selectedProvider.home_path}>{selectedProvider.home_path}</code>
            <strong>
              {providerCapabilityCount(selectedProvider)} {t("providers.capabilities")}
            </strong>
          </div>
        ) : (
          <EmptyState label={t("providers.empty")} />
        )}
        {capabilityRows.length > 0 && (
          <div className="provider-capability-strip">
            {capabilityRows.map((capability) => (
              <span
                key={capability.key}
                className={capability.enabled ? "capability-pill enabled" : "capability-pill"}
              >
                {t(capability.labelKey)}
              </span>
            ))}
          </div>
        )}
      </section>

      <section className="split-grid provider-grid">
        <div className="work-panel">
          <SectionHeader title={t("providers.list")} action={t("providers.list.action")} />
          <div className="provider-list">
            {providers.length > 0 ? (
              providers.map((provider) => (
                <button
                  key={provider.id}
                  type="button"
                  className={provider.id === selectedProviderId ? "provider-row active" : "provider-row"}
                  onClick={() => onProviderChange(provider.id)}
                >
                  <div>
                    <div className="line-title">{provider.name}</div>
                    <div className="line-subtitle" title={provider.home_path}>
                      {provider.home_path}
                    </div>
                  </div>
                  <span className={`badge ${provider.detected ? "accepted" : "rejected"}`}>
                    {provider.detected ? t("providers.detected") : t("providers.notDetected")}
                  </span>
                  <small>
                    {providerCapabilityCount(provider)} {t("providers.capabilities")}
                  </small>
                </button>
              ))
            ) : (
              <EmptyState label={t("providers.empty")} />
            )}
          </div>
        </div>

        <div className="work-panel">
          <SectionHeader title={t("providers.assets")} action={t("providers.assets.action")} />
          <div className="provider-asset-toolbar">
            <div className="search-strip">
              <Search className="h-4 w-4" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("providers.searchAssets")}
              />
            </div>
            <span>
              {visibleAssets.length}/{assets.length}
            </span>
          </div>
          <div className="provider-asset-list">
            {visibleAssets.length > 0 ? (
              visibleAssets.map((asset) => {
                const AssetIcon = providerAssetIcon(asset.asset_type);
                return (
                  <article className="provider-asset-item" key={asset.id}>
                    <div className="provider-asset-icon">
                      <AssetIcon className="h-4 w-4" />
                    </div>
                    <div className="provider-asset-body">
                      <div className="provider-asset-title">
                        <strong>{asset.name}</strong>
                        <span>{t(`providers.asset.${asset.asset_type}`)}</span>
                      </div>
                      {asset.description && <p>{asset.description}</p>}
                      <code title={asset.path ?? ""}>{asset.path ?? t("common.notGenerated")}</code>
                      <div className="provider-asset-meta">
                        <span>{asset.scope}</span>
                        {asset.project_path && <span title={asset.project_path}>{asset.project_path}</span>}
                        {asset.modified_at && <span>{asset.modified_at}</span>}
                        {asset.size_bytes !== null && <span>{formatBytes(asset.size_bytes)}</span>}
                      </div>
                    </div>
                    <div className="provider-asset-tags">
                      {asset.tags.slice(0, 4).map((tag) => (
                        <span key={tag}>{tag}</span>
                      ))}
                    </div>
                  </article>
                );
              })
            ) : (
              <EmptyState label={assets.length > 0 ? t("providers.filteredEmpty") : t("providers.noAssets")} />
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
