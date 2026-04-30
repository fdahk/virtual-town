import { useEffect, useState } from "react";
import { TownPage } from "../pages/TownPage";
import { CreatePlayerPage } from "../pages/CreatePlayerPage";
import { useWorldStore } from "../stores/worldStore";
import { api } from "../api";

export function App() {
  const [ready, setReady] = useState(false);
  const [hasPlayer, setHasPlayer] = useState(false);
  const setPlayer = useWorldStore((s) => s.setPlayer);

  useEffect(() => {
    (async () => {
      try {
        const me = await api.getMe();
        setPlayer(me);
        // 如果是默认种子玩家，仍让用户自定义一次
        if (me && me.name !== "旅人") {
          setHasPlayer(true);
        }
      } catch {
        // 没有玩家，进入创建页
      } finally {
        setReady(true);
      }
    })();
  }, [setPlayer]);

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
