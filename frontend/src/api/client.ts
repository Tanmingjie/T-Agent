const BASE = "/api";
const TIMEOUT_MS = 30000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(`${BASE}${path}`, { ...init, signal: controller.signal });
    if (!r.ok) throw new Error(await r.text());
    if (r.status === 204 || r.headers.get("content-length") === "0") return undefined as T;
    return r.json();
  } finally {
    clearTimeout(timeout);
  }
}

export async function apiGet<T = unknown>(path: string): Promise<T> {
  return request<T>(path);
}

export async function apiPost<T = unknown>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : JSON.stringify(body),
  });
}

export async function apiPut<T = unknown>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function apiDelete(path: string): Promise<void> {
  await request<void>(path, { method: "DELETE" });
}

export function sseUrl(path: string): string {
  return `${BASE}${path}`;
}

export function safeParse(data: string | null): Record<string, unknown> | null {
  if (!data) return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}