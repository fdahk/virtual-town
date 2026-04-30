// WebSocket 接入：单例连接，维护自动重连与事件分发。

type Handler = (type: string, payload: unknown) => void;

const BASE = (import.meta.env.VITE_WS_BASE_URL ?? "") as string;

export class SimulationSocket {
  private ws: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private handlers = new Set<Handler>();
  private simulationId: string | null = null;
  private closedByUser = false;

  connect(simulationId: string): void {
    if (this.ws && this.simulationId === simulationId) return;
    this.close();
    this.closedByUser = false;
    this.simulationId = simulationId;
    const url = `${BASE || inferWsBase()}/ws/simulations/${simulationId}`;
    this.ws = new WebSocket(url);
    this.ws.onopen = () => {
      console.debug("[ws] open", url);
    };
    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        this.handlers.forEach((h) => h(data.type, data.payload));
      } catch (err) {
        console.warn("[ws] parse error", err);
      }
    };
    this.ws.onerror = (e) => {
      console.warn("[ws] error", e);
    };
    this.ws.onclose = () => {
      if (this.closedByUser) return;
      this.reconnectTimer = window.setTimeout(() => {
        if (this.simulationId) this.connect(this.simulationId);
      }, 1500);
    };
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
  }

  subscribe(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }
}

function inferWsBase(): string {
  const { protocol, host } = window.location;
  const wsProto = protocol === "https:" ? "wss" : "ws";
  // dev 场景 vite 会代理 /ws/* 到后端
  return `${wsProto}://${host}`;
}

export const simulationSocket = new SimulationSocket();
