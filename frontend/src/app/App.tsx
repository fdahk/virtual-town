import type { CSSProperties } from "react";
import { useEffect, useState } from "react";
import { TownPage } from "../pages/TownPage";
import { StartPage } from "../pages/StartPage";
import { ObservabilityPage } from "../pages/observability/ObservabilityPage";
import { useWorldStore } from "../stores/worldStore";
import { api } from "../api";

/**
 * 应用入口：基于 path 做最小路由。
 *
 * - ``/observability``（含子路径）  → 观测平台（独立页面）。
 * - 其它 path                       → 小镇玩家流程：
 *     - 默认显示 StartPage（新游戏 / 读档 / 继续）；
 *     - 仿真已 running 且玩家点了"继续"或刚完成"新游戏" → TownPage。
 *
 * 观测平台必须与玩家页面完全隔离（§14.2）。
 */

/** body 为 overflow:hidden（全镇页面防双滚动条）；开始流程单独在此容器内纵向滚动。 */
const startFlowShell: CSSProperties = {
  height: "100%",
  overflowY: "auto",
  overflowX: "hidden",
  WebkitOverflowScrolling: "touch",
};

export function App() {
  const [route, setRoute] = useState<string>(() => window.location.pathname);
  const [inTown, setInTown] = useState(false);
  const setPlayer = useWorldStore((s) => s.setPlayer);

  useEffect(() => {
    const handler = () => setRoute(window.location.pathname);
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);

  const isObservability = route.startsWith("/observability");

  // 进入 TownPage 前先尝试拉一次 player 以预热 worldStore
  const enterTown = async () => {
    try {
      const me = await api.getMe();
      setPlayer(me);
    } catch {
      // 玩家不存在不阻断入场；TownPage 会自行处理
    }
    setInTown(true);
  };

  if (isObservability) {
    return <ObservabilityPage />;
  }
  if (inTown) {
    return <TownPage />;
  }
  return (
    <div style={startFlowShell}>
      <StartPage onEnterTown={enterTown} />
    </div>
  );
}
