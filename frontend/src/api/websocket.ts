// WebSocket 接入：单例连接 + 指数退避重连 + 断线后 REST 快照恢复（§14.5）。

type Handler = (type: string, payload: unknown) => void;

const BASE = (import.meta.env.VITE_WS_BASE_URL ?? "") as string;

interface ReconnectState {
  attempts: number;
  nextDelayMs: number;
  timer: number | null;
}

export interface WsStatus {
  connected: boolean;
  reconnecting: boolean;
  attempt: number;
  lastSeq: number | null;
  droppedSeqs: number;
}

export type StatusHandler = (status: WsStatus) => void;
export type ReconnectHandler = () => Promise<void> | void;

/**
 * 通用的 WebSocket 客户端：
 * - 自动重连，延迟按 ``min(base * 2^attempts, max)`` 指数退避。
 * - 可注入 ``onReconnect`` 钩子：重连成功后由调用方拉 REST 快照做状态重建。
 * - 解析 ``simulation.delta`` 中的 ``seq`` 字段，检测乱序与丢失，通过 status 广播。
 */
export class ReliableSocket {
  private ws: WebSocket | null = null;
  private url: string | null = null;
  private closedByUser = false;

  private reconnect: ReconnectState = {
    attempts: 0,
    nextDelayMs: 500,
    timer: null,
  };

  private handlers = new Set<Handler>();
  private statusHandlers = new Set<StatusHandler>();
  private onReconnect: ReconnectHandler | null = null;

  private status: WsStatus = {
    connected: false,
    reconnecting: false,
    attempt: 0,
    lastSeq: null,
    droppedSeqs: 0,
  };

  private readonly minDelayMs = 500;
  private readonly maxDelayMs = 15_000;

  setReconnectHook(fn: ReconnectHandler | null): void {
    this.onReconnect = fn;
  }

  subscribe(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  onStatusChange(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    handler(this.status);
    return () => this.statusHandlers.delete(handler);
  }

  getStatus(): WsStatus {
    return this.status;
  }

  connect(url: string): void {
    if (this.ws && this.url === url) return;
    this.close();
    this.closedByUser = false;
    this.url = url;
    this.openOnce();
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnect.timer !== null) {
      window.clearTimeout(this.reconnect.timer);
      this.reconnect.timer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
    this.emitStatus({ connected: false, reconnecting: false, attempt: 0 });
  }

  private openOnce(): void {
    if (!this.url) return;
    const ws = new WebSocket(this.url);
    this.ws = ws;

    ws.onopen = async () => {
      const wasReconnect = this.reconnect.attempts > 0;
      this.reconnect.attempts = 0;
      this.reconnect.nextDelayMs = this.minDelayMs;
      this.emitStatus({ connected: true, reconnecting: false, attempt: 0 });
      if (wasReconnect && this.onReconnect) {
        try {
          await this.onReconnect();
        } catch (err) {
          console.warn("[ws] reconnect hook failed", err);
        }
      }
    };

    ws.onmessage = (ev) => {
      let data: { type?: string; payload?: unknown };
      try {
        data = JSON.parse(ev.data);
      } catch (err) {
        console.warn("[ws] parse error", err);
        return;
      }
      // 检查 seq（只对 simulation.delta）
      if (data.type === "simulation.delta") {
        const seq = (data.payload as { seq?: number | null } | undefined)?.seq;
        if (typeof seq === "number") {
          if (this.status.lastSeq !== null && seq !== this.status.lastSeq + 1) {
            const missing = Math.max(seq - (this.status.lastSeq ?? 0) - 1, 0);
            this.emitStatus({
              lastSeq: seq,
              droppedSeqs: this.status.droppedSeqs + missing,
            });
            if (missing > 0) {
              console.warn(
                `[ws] detected seq gap: expected ${this.status.lastSeq ?? 0 + 1}, got ${seq}`,
              );
            }
          } else {
            this.emitStatus({ lastSeq: seq });
          }
        }
      }
      this.handlers.forEach((h) => h(data.type ?? "", data.payload));
    };

    ws.onerror = (e) => {
      console.warn("[ws] error", e);
    };

    ws.onclose = () => {
      this.ws = null;
      this.emitStatus({ connected: false });
      if (this.closedByUser) return;
      // 退避重连
      const attempt = this.reconnect.attempts + 1;
      const delay = Math.min(
        this.minDelayMs * 2 ** this.reconnect.attempts,
        this.maxDelayMs,
      );
      this.reconnect.attempts = attempt;
      this.reconnect.nextDelayMs = delay;
      this.emitStatus({ reconnecting: true, attempt });
      this.reconnect.timer = window.setTimeout(() => this.openOnce(), delay);
    };
  }

  private emitStatus(patch: Partial<WsStatus>): void {
    this.status = { ...this.status, ...patch };
    this.statusHandlers.forEach((h) => h(this.status));
  }
}

// ---------------------------------------------------------------------------
// 仿真 WebSocket
// ---------------------------------------------------------------------------

export class SimulationSocket {
  private inner = new ReliableSocket();
  private simulationId: string | null = null;

  connect(simulationId: string): void {
    if (this.inner.getStatus().connected && this.simulationId === simulationId) {
      return;
    }
    this.simulationId = simulationId;
    const url = `${BASE || inferWsBase()}/ws/simulations/${simulationId}`;
    this.inner.connect(url);
  }

  close(): void {
    this.inner.close();
    this.simulationId = null;
  }

  setReconnectHook(fn: ReconnectHandler | null): void {
    this.inner.setReconnectHook(fn);
  }

  subscribe(handler: Handler): () => void {
    return this.inner.subscribe(handler);
  }

  onStatusChange(handler: StatusHandler): () => void {
    return this.inner.onStatusChange(handler);
  }

  getStatus(): WsStatus {
    return this.inner.getStatus();
  }
}

// ---------------------------------------------------------------------------
// Observability WebSocket
// ---------------------------------------------------------------------------

export class ObservabilitySocket {
  private inner = new ReliableSocket();
  private simulationId: string | null = null;

  connect(simulationId: string | "*" = "*"): void {
    this.simulationId = simulationId;
    const url = `${BASE || inferWsBase()}/ws/observability/${simulationId}`;
    this.inner.connect(url);
  }

  close(): void {
    this.inner.close();
    this.simulationId = null;
  }

  subscribe(handler: Handler): () => void {
    return this.inner.subscribe(handler);
  }

  onStatusChange(handler: StatusHandler): () => void {
    return this.inner.onStatusChange(handler);
  }

  getStatus(): WsStatus {
    return this.inner.getStatus();
  }
}

function inferWsBase(): string {
  const { protocol, host } = window.location;
  const wsProto = protocol === "https:" ? "wss" : "ws";
  return `${wsProto}://${host}`;
}

export const simulationSocket = new SimulationSocket();
export const observabilitySocket = new ObservabilitySocket();
