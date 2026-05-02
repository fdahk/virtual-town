import { useState } from "react";
import { api } from "../api";
import { useWorldStore } from "../stores/worldStore";

/**
 * 存档按钮：调 ``GET /api/games/save`` 取回 JSON 快照后用 Blob 触发浏览器下载。
 *
 * 设计说明：
 * - 文件名带 simulation 当前 step 与本地日期，方便玩家区分多次存档：
 *   ``vt-save-2026-05-02-step1234.json``
 * - 不向后端上传任何东西；存盘是纯客户端动作，安全且不依赖文件系统权限。
 * - 故意用 anchor.click() 而非 window.open()：避免某些浏览器把
 *   大 JSON（>1MB）当作普通页面渲染。
 */
export function SaveGameButton() {
  const [busy, setBusy] = useState(false);
  const sim = useWorldStore((s) => s.simulation);

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const snapshot = await api.getSaveSnapshot();
      const date = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
      const step = sim?.current_step ?? 0;
      const fileName = `vt-save-${date}-step${step}.json`;
      const blob = new Blob([JSON.stringify(snapshot, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      try {
        const a = document.createElement("a");
        a.href = url;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        a.remove();
      } finally {
        // 异步释放：a.click 后浏览器开始下载流，立刻 revoke 会让某些浏览器
        // （Safari < 17）下载失败。500ms 足够让流建立。
        setTimeout(() => URL.revokeObjectURL(url), 500);
      }
    } catch (err) {
      console.warn("[save_game] failed", err);
      const msg = (err as { message?: string })?.message ?? String(err);
      alert(`存档失败：${msg}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      onClick={handleClick}
      disabled={busy}
      title="导出当前世界为 JSON 快照（保存到下载目录）"
      style={{
        padding: "3px 10px",
        fontSize: 11,
        background: busy ? "#3a3a3a" : "#1f4a85",
        color: "#eef0f2",
        border: "1px solid rgba(255,255,255,0.12)",
        borderRadius: 999,
        cursor: busy ? "wait" : "pointer",
        opacity: busy ? 0.7 : 1,
      }}
    >
      {busy ? "导出中…" : "存档"}
    </button>
  );
}
