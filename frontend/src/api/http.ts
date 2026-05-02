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
      // 兼容 FastAPI 默认错误形态 {"detail": "..."} / {"detail": [...]}：
      // 业务错误用 body.message + body.code（自定义异常处理器），但
      // HTTPException 直接返回 detail 字段，前端要显示具体错误（如"存档主版本号不兼容"）
      // 而不是统一的 statusText。
      const detail = typeof body.detail === "string" ? body.detail : null;
      err = {
        code: body.code ?? "INTERNAL_ERROR",
        message: body.message ?? detail ?? resp.statusText,
        retryable: body.retryable ?? false,
        details: body.details ?? (Array.isArray(body.detail) ? { errors: body.detail } : undefined),
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
