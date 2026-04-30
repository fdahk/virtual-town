// 所有 REST 请求都必须经过这里。严禁在组件里直接调用 fetch。

const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" } });
  return handle<T>(resp);
}

export async function apiPost<T>(
  path: string,
  body: unknown,
): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body ?? {}),
  });
  return handle<T>(resp);
}

export async function apiPatch<T>(
  path: string,
  body: unknown,
): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body ?? {}),
  });
  return handle<T>(resp);
}

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let err: ApiError;
    try {
      const body = await resp.json();
      err = {
        code: body.code ?? "INTERNAL_ERROR",
        message: body.message ?? resp.statusText,
        retryable: body.retryable ?? false,
        details: body.details,
      };
    } catch {
      err = {
        code: "INTERNAL_ERROR",
        message: resp.statusText,
        retryable: false,
      };
    }
    throw err;
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}
