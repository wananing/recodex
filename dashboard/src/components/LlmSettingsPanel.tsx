import { Activity, Bot, CheckCircle2, KeyRound, RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useI18n } from "@/lib/i18n";
import { getJson, postJson } from "@/lib/recodexClient";

type LlmProvider = "mock" | "openai" | "openai-compatible" | "volcengine" | "dashscope" | "siliconflow";
type PresetId = "volcengine" | "dashscope" | "siliconflow" | "openai-compatible" | "openai" | "mock";

type LlmSettings = {
  enabled: boolean;
  provider: LlmProvider;
  model: string;
  api_key_env: string;
  base_url: string;
  local_only: boolean;
  allow_cloud: boolean;
  api_key_configured?: boolean;
};

type LlmPreset = {
  id: PresetId;
  label: string;
  tag: string;
  provider: LlmProvider;
  model: string;
  baseUrl: string;
  apiKeyEnv: string;
  mode: string;
  editableModel?: boolean;
  editableBaseUrl?: boolean;
};

const presets: LlmPreset[] = [
  {
    id: "volcengine",
    label: "Ark",
    tag: "Doubao",
    provider: "volcengine",
    model: "doubao-seed-2-0-lite-260215",
    baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    apiKeyEnv: "ARK_API_KEY",
    mode: "Responses API",
    editableModel: true,
  },
  {
    id: "dashscope",
    label: "阿里百炼",
    tag: "Qwen",
    provider: "dashscope",
    model: "qwen-plus",
    baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    apiKeyEnv: "DASHSCOPE_API_KEY",
    mode: "OpenAI-compatible",
    editableModel: true,
  },
  {
    id: "siliconflow",
    label: "硅基流动",
    tag: "DeepSeek",
    provider: "siliconflow",
    model: "deepseek-ai/DeepSeek-V3.1",
    baseUrl: "https://api.siliconflow.cn/v1",
    apiKeyEnv: "SILICONFLOW_API_KEY",
    mode: "OpenAI-compatible",
    editableModel: true,
  },
  {
    id: "openai-compatible",
    label: "OpenAI 标准 API",
    tag: "Chat",
    provider: "openai-compatible",
    model: "gpt-5.5",
    baseUrl: "https://api.openai.com/v1",
    apiKeyEnv: "OPENAI_API_KEY",
    mode: "Chat Completions",
    editableModel: true,
    editableBaseUrl: true,
  },
  {
    id: "openai",
    label: "OpenAI Responses",
    tag: "Responses",
    provider: "openai",
    model: "gpt-5.5",
    baseUrl: "https://api.openai.com/v1",
    apiKeyEnv: "OPENAI_API_KEY",
    mode: "Responses API",
    editableModel: true,
    editableBaseUrl: true,
  },
  {
    id: "mock",
    label: "Mock",
    tag: "Local",
    provider: "mock",
    model: "mock-model",
    baseUrl: "",
    apiKeyEnv: "",
    mode: "Local fixture",
  },
];

const defaultPreset = presets[0];

export function LlmSettingsPanel() {
  const { t } = useI18n();
  const [settings, setSettings] = useState<LlmSettings>(settingsFromPreset(defaultPreset, false));
  const [presetId, setPresetId] = useState<PresetId>(defaultPreset.id);
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);

  const preset = useMemo(
    () => presets.find((item) => item.id === presetId) ?? defaultPreset,
    [presetId],
  );
  const isCloud = settings.provider !== "mock";

  useEffect(() => {
    void loadSettings(false);
  }, []);

  async function loadSettings(showNotice = true) {
    setBusy("load");
    const result = await getJson<{ ok: boolean; settings: LlmSettings }>("/settings/llm");
    setBusy(null);
    if (!result.ok) {
      setNotice({ ok: false, message: result.message });
      return;
    }
    const loaded = result.data.settings;
    const loadedPreset = presetForSettings(loaded);
    setPresetId(loadedPreset.id);
    setSettings({
      ...settingsFromPreset(loadedPreset, loaded.enabled),
      ...loaded,
      model: loaded.model || loadedPreset.model,
      base_url: loaded.base_url || loadedPreset.baseUrl,
      api_key_env: loaded.api_key_env || loadedPreset.apiKeyEnv,
    });
    if (showNotice) {
      setNotice({ ok: true, message: t("message.llmLoaded") });
    }
  }

  async function saveSettings(enabled = settings.enabled) {
    setBusy("save");
    const result = await postJson<{ ok: boolean; settings: LlmSettings }>("/settings/llm", settingsPayload(enabled));
    setBusy(null);
    if (!result.ok) {
      setNotice({ ok: false, message: result.message });
      return;
    }
    setSettings(result.data.settings);
    setApiKey("");
    setClearApiKey(false);
    setNotice({ ok: true, message: enabled ? t("message.llmSaved") : t("message.llmDisabled") });
  }

  function selectPreset(nextId: PresetId) {
    const nextPreset = presets.find((item) => item.id === nextId) ?? defaultPreset;
    setPresetId(nextPreset.id);
    setClearApiKey(false);
    setSettings((current) => ({
      ...settingsFromPreset(nextPreset, current.enabled),
      api_key_configured: current.api_key_configured,
    }));
  }

  function updateSettings(patch: Partial<LlmSettings>) {
    setSettings((current) => ({ ...current, ...patch }));
  }

  function settingsPayload(enabled: boolean): Record<string, unknown> {
    const cloud = settings.provider !== "mock";
    return {
      enabled,
      provider: settings.provider,
      model: settings.model || preset.model,
      api_key_env: settings.api_key_env || undefined,
      base_url: settings.base_url || undefined,
      local_only: !cloud,
      allow_cloud: cloud && enabled,
      api_key: apiKey || undefined,
      clear_api_key: clearApiKey || undefined,
    };
  }

  return (
    <div className="content-stack llm-page">
      <section className="work-panel llm-hero-panel">
        <div className="section-header">
          <h2>{t("llm.provider")}</h2>
          <span>{settings.enabled ? t("common.enabled") : t("common.off")}</span>
        </div>

        {notice && (
          <div className={notice.ok ? "inline-notice ok" : "inline-notice error"}>
            {notice.ok ? <CheckCircle2 className="h-4 w-4" /> : <Activity className="h-4 w-4" />}
            <span>{notice.message}</span>
          </div>
        )}

        <div className="llm-provider-grid" aria-label={t("llm.presets")}>
          {presets.map((item) => (
            <button
              key={item.id}
              type="button"
              className={item.id === presetId ? "llm-provider-card active" : "llm-provider-card"}
              aria-pressed={item.id === presetId}
              onClick={() => selectPreset(item.id)}
            >
              <span>{item.label}</span>
              <strong>{item.tag}</strong>
              <small>{item.mode}</small>
            </button>
          ))}
        </div>
      </section>

      <section className="work-panel">
        <div className="settings-llm-heading">
          <div className="report-section-title">
            <Bot className="h-4 w-4" />
            <h3>{preset.label}</h3>
          </div>
          <div className="command-row">
            <button type="button" className="secondary-command" disabled={busy === "load"} onClick={() => void loadSettings()}>
              <RefreshCw className="h-4 w-4" />
              {t("common.load")}
            </button>
            <button type="button" className="secondary-command" disabled={busy === "save"} onClick={() => void saveSettings(false)}>
              <ShieldCheck className="h-4 w-4" />
              {t("common.disable")}
            </button>
            <button type="button" className="primary-command" disabled={busy === "save"} onClick={() => void saveSettings(true)}>
              <Save className="h-4 w-4" />
              {t("common.save")}
            </button>
          </div>
        </div>

        <div className="llm-settings-grid">
          <label>
            <span>{t("llm.provider")}</span>
            <input value={settings.provider} readOnly />
          </label>

          <label className="wide">
            <span>{t("llm.model")}</span>
            <input
              value={settings.model}
              readOnly={!preset.editableModel}
              onChange={(event) => updateSettings({ model: event.target.value })}
            />
          </label>

          <label className="wide">
            <span>{t("llm.baseUrl")}</span>
            <input
              value={settings.base_url}
              readOnly={!preset.editableBaseUrl}
              onChange={(event) => updateSettings({ base_url: event.target.value })}
              placeholder={preset.baseUrl || t("common.notRequired")}
            />
          </label>

          <label>
            <span>{t("llm.keyEnv")}</span>
            <input value={settings.api_key_env} readOnly placeholder={t("common.notRequired")} />
          </label>

          <label className="wide">
            <span>{t("llm.apiKey")}</span>
            <div className="report-secret-field">
              <KeyRound className="h-4 w-4" />
              <input
                type="password"
                value={apiKey}
                disabled={!isCloud}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={settings.api_key_configured ? t("common.configured") : isCloud ? t("llm.pasteKey") : t("common.notRequired")}
              />
            </div>
          </label>

          <label className="report-check-field">
            <input
              type="checkbox"
              checked={clearApiKey}
              disabled={!settings.api_key_configured}
              onChange={(event) => setClearApiKey(event.target.checked)}
            />
            <span>{t("llm.clearKey")}</span>
          </label>
        </div>
      </section>
    </div>
  );
}

function settingsFromPreset(preset: LlmPreset, enabled: boolean): LlmSettings {
  const cloud = preset.provider !== "mock";
  return {
    enabled,
    provider: preset.provider,
    model: preset.model,
    base_url: preset.baseUrl,
    api_key_env: preset.apiKeyEnv,
    local_only: !cloud,
    allow_cloud: cloud && enabled,
    api_key_configured: false,
  };
}

function presetForSettings(settings: LlmSettings): LlmPreset {
  const baseUrl = settings.base_url.toLowerCase();
  if (settings.provider === "volcengine") {
    return presets[0];
  }
  if (settings.provider === "dashscope" || baseUrl.includes("dashscope.aliyuncs.com")) {
    return presets[1];
  }
  if (settings.provider === "siliconflow" || baseUrl.includes("siliconflow.cn")) {
    return presets[2];
  }
  if (settings.provider === "openai") {
    return presets[4];
  }
  if (settings.provider === "mock") {
    return presets[5];
  }
  return presets[3];
}
