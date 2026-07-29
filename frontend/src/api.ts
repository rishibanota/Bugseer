import type {
  FileRisk,
  GraphPayload,
  ImpactResult,
  ReportSummary,
} from "./types";

// In dev, Vite proxies /api to the Python server on 8420.
const BASE = "";

async function get<T>(url: string): Promise<T> {
  const response = await fetch(`${BASE}${url}`);
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText);
    throw new Error(`${response.status}: ${detail.slice(0, 200)}`);
  }
  return response.json() as Promise<T>;
}

async function post<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(`${BASE}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText);
    throw new Error(`${response.status}: ${detail.slice(0, 200)}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  summary: () => get<ReportSummary>("/api/summary"),
  files: (params: { band?: string; q?: string; min_score?: number } = {}) => {
    const search = new URLSearchParams();
    if (params.band) search.set("band", params.band);
    if (params.q) search.set("q", params.q);
    if (params.min_score) search.set("min_score", String(params.min_score));
    const qs = search.toString();
    return get<FileRisk[]>(`/api/files${qs ? `?${qs}` : ""}`);
  },
  file: (path: string) => get<FileRisk>(`/api/file?path=${encodeURIComponent(path)}`),
  graph: () => get<GraphPayload>("/api/graph"),
  impact: (files: string[], hops = 3) =>
    post<ImpactResult>("/api/impact", { files, hops, limit: 25 }),
  refresh: () => get<unknown>("/api/report?refresh=true"),
  narrate: (path: string) =>
    post<{ ok: boolean; provider: string; text: string; error: string }>(
      `/api/narrate?path=${encodeURIComponent(path)}`,
    ),
  train: (labelWindow = 180) =>
    post<Record<string, unknown>>(`/api/train?label_window=${labelWindow}`),
};
