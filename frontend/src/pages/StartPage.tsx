import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Simulation } from "../types/domain";
import { NewGameWizard } from "./NewGameWizard";

type Mode = "menu" | "new_game";

interface Props {
  /** 当世界已就绪、玩家可进入 TownPage 时调用。 */
  onEnterTown: () => void;
}

/**
 * 应用入口"开始页"。
 *
 * - 探查后端 simulation 状态：
 *   - status=running / paused → 显示「继续当前世界」入口
 *     （冷启动会被收敛到 paused，玩家点击后由 TownPage 顶部的时间控制恢复）
 *   - idle / stopped / 无       → 隐藏入口
 * - 始终显示「新游戏」+「读档」两条主路径
 *
 * 「新游戏」打开 NewGameWizard 三步向导；「读档」用隐藏的 file picker
 * 读取 JSON 后调 POST /api/games/load。
 */
export function StartPage({ onEnterTown }: Props) {
  const [mode, setMode] = useState<Mode>("menu");
  const [sim, setSim] = useState<Simulation | null>(null);
  const [loadingSave, setLoadingSave] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const s = await api.getCurrentSimulation();
        setSim(s);
      } catch {
        setSim(null);
      }
    })();
  }, []);

  // running / paused 都允许「继续」：后端冷启动后会把 status 收敛为 paused
  // （SIMULATION_AUTOSTART=false 的预期语义），玩家进入小镇后再恢复。
  const continueAvailable = sim != null && (sim.status === "running" || sim.status === "paused");
  const continueIsPaused = sim?.status === "paused";

  /**
   * 读档主流程：
   * 1. 用 FileReader 把 .json 读成文本 → JSON.parse
   * 2. 校验 version 字段存在（更详细的版本号校验交给后端）
   * 3. POST /api/games/load → 后端清空 DB 后写入 + reload 引擎
   * 4. 跳转 TownPage（status=paused，玩家从顶部「时间控制」恢复）
   */
  const handleFileSelected = async (file: File) => {
    setLoadError(null);
    setLoadingSave(true);
    try {
      const text = await file.text();
      let snapshot: Record<string, unknown>;
      try {
        snapshot = JSON.parse(text);
      } catch (parseErr) {
        throw new Error(`存档不是合法 JSON：${(parseErr as Error).message}`);
      }
      if (typeof snapshot.version !== "string") {
        throw new Error("存档缺少 version 字段，无法识别格式版本");
      }
      const result = await api.postLoadSave(snapshot);
      console.info("[load_save] imported", result);
      onEnterTown();
    } catch (err) {
      const msg = (err as { message?: string })?.message ?? String(err);
      console.warn("[load_save] failed", err);
      setLoadError(msg);
    } finally {
      setLoadingSave(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  if (mode === "new_game") {
    return (
      <NewGameWizard
        onCancel={() => setMode("menu")}
        onCreated={() => onEnterTown()}
      />
    );
  }

  return (
    <div style={styles.page}>
      <div style={styles.titleBlock}>
        <h1 style={styles.title}>AI 小镇</h1>
        <p style={styles.subtitle}>
          一个由大型语言模型驱动的小镇模拟。每位居民都有自己的记忆、关系与日程。
        </p>
      </div>

      <div style={styles.menu}>
        {continueAvailable && (
          <button style={styles.continueBtn} onClick={() => onEnterTown()}>
            {continueIsPaused ? "继续当前世界（已暂停）" : "继续当前世界"}
            <span style={styles.btnSub}>
              已运行 {sim?.current_step ?? 0} 步 · 速度 ×{sim?.speed_multiplier ?? 1}
              {continueIsPaused ? " · 进入后从顶部时间控制恢复" : ""}
            </span>
          </button>
        )}
        <button style={styles.primaryBtn} onClick={() => setMode("new_game")}>
          开始新游戏
          <span style={styles.btnSub}>
            22 个默认 NPC，或自定义你的小镇人口
          </span>
        </button>
        <button
          style={{
            ...styles.secondaryBtn,
            opacity: loadingSave ? 0.5 : 1,
            cursor: loadingSave ? "wait" : "pointer",
          }}
          onClick={() => fileInputRef.current?.click()}
          disabled={loadingSave}
        >
          {loadingSave ? "正在导入存档…" : "读取存档（上传 JSON）"}
          <span style={styles.btnSub}>
            从 GET /api/games/save 导出的快照恢复世界
          </span>
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/json,.json"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFileSelected(file);
          }}
        />
        {loadError && (
          <div style={styles.errorBanner} role="alert">
            读档失败：{loadError}
          </div>
        )}
      </div>

      <div style={styles.footer}>
        <a href="/observability" style={styles.footerLink} target="_blank">
          研发观测台 →
        </a>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    width: "100%",
    minHeight: "100%",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "flex-start",
    gap: 28,
    padding: "clamp(28px, 6vh, 72px) 20px max(32px, env(safe-area-inset-bottom, 0px))",
    background:
      "radial-gradient(ellipse at top, #1d2736 0%, #0b0f14 60%, #070a10 100%)",
    color: "#e8edf5",
    boxSizing: "border-box",
  },
  titleBlock: {
    textAlign: "center",
    maxWidth: 640,
  },
  title: {
    margin: 0,
    fontSize: 56,
    fontWeight: 700,
    letterSpacing: 2,
    background: "linear-gradient(120deg, #6da7ff, #c596ff)",
    WebkitBackgroundClip: "text",
    color: "transparent",
  },
  subtitle: {
    marginTop: 14,
    fontSize: 15,
    lineHeight: 1.6,
    opacity: 0.75,
  },
  menu: {
    display: "grid",
    gap: 14,
    width: "100%",
    maxWidth: 480,
  },
  continueBtn: {
    display: "grid",
    gap: 6,
    padding: "16px 22px",
    border: "1px solid rgba(143, 199, 255, 0.4)",
    borderRadius: 12,
    background: "rgba(70, 130, 200, 0.18)",
    color: "#d6eaff",
    fontSize: 16,
    fontWeight: 600,
    cursor: "pointer",
    textAlign: "left",
  },
  primaryBtn: {
    display: "grid",
    gap: 6,
    padding: "18px 24px",
    border: "none",
    borderRadius: 12,
    background: "linear-gradient(120deg, #4b6fff, #8151ff)",
    color: "white",
    fontSize: 17,
    fontWeight: 700,
    cursor: "pointer",
    textAlign: "left",
    boxShadow: "0 8px 28px rgba(75, 111, 255, 0.4)",
  },
  secondaryBtn: {
    display: "grid",
    gap: 6,
    padding: "16px 22px",
    border: "1px solid rgba(255,255,255,0.15)",
    borderRadius: 12,
    background: "rgba(255,255,255,0.04)",
    color: "#cbd2db",
    fontSize: 15,
    fontWeight: 600,
    cursor: "pointer",
    textAlign: "left",
  },
  btnSub: {
    fontSize: 12,
    fontWeight: 400,
    opacity: 0.7,
  },
  errorBanner: {
    padding: "10px 14px",
    borderRadius: 8,
    background: "rgba(220, 80, 80, 0.12)",
    border: "1px solid rgba(220, 80, 80, 0.4)",
    color: "#ffb4b4",
    fontSize: 13,
    lineHeight: 1.5,
    whiteSpace: "pre-wrap",
  },
  footer: {
    marginTop: 24,
    fontSize: 12,
  },
  footerLink: {
    color: "#7c8ca3",
    textDecoration: "none",
  },
};
