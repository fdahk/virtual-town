import { useEffect, useState } from "react";
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
 *   - status=running → 显示「继续当前世界」入口
 *   - 其它 / 无 → 默认隐藏入口
 * - 始终显示「新游戏」+「读档」两条主路径
 *
 * 「新游戏」打开 NewGameWizard 三步向导；「读档」选 JSON 文件后
 * （后续接 POST /api/games/load；当前先 stub）。
 */
export function StartPage({ onEnterTown }: Props) {
  const [mode, setMode] = useState<Mode>("menu");
  const [sim, setSim] = useState<Simulation | null>(null);

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

  const continueAvailable = sim != null && sim.status === "running";

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
            继续当前世界
            <span style={styles.btnSub}>
              已运行 {sim?.current_step ?? 0} 步 · 速度 ×{sim?.speed_multiplier ?? 1}
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
          style={styles.secondaryBtn}
          onClick={() => alert("读档功能开发中：稍后可上传 JSON 快照恢复世界。")}
        >
          读取存档（上传 JSON）
          <span style={styles.btnSub}>
            从导出的 ``GET /api/games/save`` 快照中恢复
          </span>
        </button>
      </div>

      <div style={styles.footer}>
        <a href="/observability" style={styles.footerLink}>
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
  footer: {
    marginTop: 24,
    fontSize: 12,
  },
  footerLink: {
    color: "#7c8ca3",
    textDecoration: "none",
  },
};
