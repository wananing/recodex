export type ApiResult = {
  ok: boolean;
  message: string;
};

export type JsonResult<T> =
  | {
      ok: true;
      data: T;
    }
  | {
      ok: false;
      message: string;
    };

const API_BASE = import.meta.env.VITE_RECODEX_API_BASE ?? "";

export async function postAction(path: string, payload: unknown): Promise<ApiResult> {
  const result = await postJson<unknown>(path, payload);
  if (!result.ok) {
    return result;
  }
  return { ok: true, message: resultMessage(result.data) };
}

export async function getJson<T>(path: string): Promise<JsonResult<T>> {
  return requestJson<T>(path, { method: "GET" });
}

export async function postJson<T>(path: string, payload: unknown): Promise<JsonResult<T>> {
  return requestJson<T>(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function requestJson<T>(path: string, init: RequestInit): Promise<JsonResult<T>> {
  try {
    const response = await fetch(`${API_BASE}${path}`, init);
    const text = await response.text();
    const payload = parsePayload(text);
    if (!response.ok || hasApiFailure(payload)) {
      return {
        ok: false,
        message: errorMessage(payload, text) || `${response.status} ${response.statusText}`,
      };
    }
    return { ok: true, data: payload as T };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "request failed",
    };
  }
}

function parsePayload(text: string): unknown {
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function hasApiFailure(payload: unknown): boolean {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "ok" in payload &&
    (payload as { ok?: unknown }).ok === false
  );
}

function errorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === "object" && payload !== null) {
    const maybeError = payload as { error?: unknown; message?: unknown };
    if (typeof maybeError.error === "string") {
      return maybeError.error;
    }
    if (typeof maybeError.message === "string") {
      return maybeError.message;
    }
  }
  return fallback;
}

function resultMessage(payload: unknown): string {
  if (typeof payload !== "object" || payload === null) {
    return typeof payload === "string" && payload ? payload : "completed";
  }
  const data = payload as Record<string, unknown>;
  if (typeof data.message === "string") {
    return data.message;
  }
  if (Array.isArray(data.paths)) {
    const first = typeof data.paths[0] === "string" ? `: ${data.paths[0]}` : "";
    return `wrote ${data.paths.length} file${data.paths.length === 1 ? "" : "s"}${first}`;
  }
  if (typeof data.cataloged === "number") {
    return `cataloged ${data.cataloged}, scanned ${data.scanned ?? 0}, failed ${data.failed ?? 0}`;
  }
  if (typeof data.imported === "number" || typeof data.skipped === "number" || typeof data.failed === "number") {
    return `imported ${data.imported ?? 0}, skipped ${data.skipped ?? 0}, failed ${data.failed ?? 0}`;
  }
  if (Array.isArray(data.results)) {
    return `completed ${data.results.length} sync run${data.results.length === 1 ? "" : "s"}`;
  }
  if (typeof data.target === "string" && data.target) {
    return `completed: ${data.target}`;
  }
  return "completed";
}
