// API/SSE 根路径:dev 走 Vite 代理(同源 /api,免 CORS),生产由反代统一服务。
import { authHeaders } from "../lib/session";

const BASE = "/api";
const TIMEOUT_MS = 30000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
  // 调用方传入的 signal(用于取消被取代的请求)→ 触发内部 controller.abort,
  // 让被取代的 /result、/code 立刻释放连接,避免在 HTTP/1.1 连接池上堆积。
  if (init?.signal) {
    if (init.signal.aborted) controller.abort();
    else init.signal.addEventListener("abort", () => controller.abort());
  }
  try {
    const r = await fetch(`${BASE}${path}`, {
      ...init,
      // 注入鉴权 header(X-User);平台模式做 RBAC,单机模式忽略(隐式放行)。
      headers: { ...authHeaders(), ...(init?.headers || {}) },
      signal: controller.signal,
    });
    if (!r.ok) throw new Error(await r.text());
    if (r.status === 204 || r.headers.get("content-length") === "0")
      return undefined as T;
    return r.json();
  } finally {
    clearTimeout(timeout);
  }
}

export async function apiGet<T = unknown>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  return request<T>(path, { signal });
}

export async function apiPost<T = unknown>(
  path: string,
  body?: unknown,
): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers:
      body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : JSON.stringify(body),
  });
}

export async function apiPut<T = unknown>(
  path: string,
  body?: unknown,
): Promise<T> {
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
