import { useEffect, useState } from "react";
import { TownPage } from "../pages/TownPage";
import { CreatePlayerPage } from "../pages/CreatePlayerPage";
import { ObservabilityPage } from "../pages/observability/ObservabilityPage";
import { useWorldStore } from "../stores/worldStore";
import { api } from "../api";

/**
 * 应用入口：基于 path 做最小路由。
 *
 * - ``/observability``（含子路径）  → 观测平台（独立页面）。
 * - 其它 path                       → 小镇主页面（玩家体验）。
 *
 * 观测平台必须与玩家页面完全隔离（§14.2）。
 */
export function App() {
  const [ready, setReady] = useState(false);
  const [hasPlayer, setHasPlayer] = useState(false);
  const [route, setRoute] = useState<string>(() => window.location.pathname);
  const setPlayer = useWorldStore((s) => s.setPlayer);

  useEffect(() => {
    const handler = () => setRoute(window.location.pathname);
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);

  const isObservability = route.startsWith("/observability");

  useEffect(() => {
    if (isObservability) {
      setReady(true);
      setHasPlayer(true);
      return;
    }
    (async () => {
      try {
        const me = await api.getMe();
        setPlayer(me);
        if (me && me.name !== "旅人") {
          setHasPlayer(true);
        }
      } catch {
        // 没有玩家，进入创建页
      } finally {
        setReady(true);
      }
    })();
  }, [setPlayer, isObservability]);

  if (isObservability) {
    return <ObservabilityPage />;
  }
  if (!ready) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100%" }}>
        <div>正在连接小镇……</div>
      </div>
    );
  }
  if (!hasPlayer) {
    return <CreatePlayerPage onCreated={() => setHasPlayer(true)} />;
  }
  return <TownPage />;
}
