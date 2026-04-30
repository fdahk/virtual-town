import { useEffect, useRef, useState } from "react";
import Phaser from "phaser";
import { TownScene } from "./scenes/TownScene";
import { loadManifests, type LoadedManifests } from "./assets/manifest";

interface Props {
  onReady(scene: TownScene): void;
}

// 使用一个 sentinel 对象代替 null 区分"尚未尝试"与"已尝试但失败"
const MANIFEST_MISSING = Symbol("manifest-missing");
type ManifestResult = LoadedManifests | typeof MANIFEST_MISSING;

export function PhaserGame({ onReady }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const gameRef = useRef<Phaser.Game | null>(null);
  const [manifests, setManifests] = useState<ManifestResult | null>(null);
  const [manifestError, setManifestError] = useState<string | null>(null);

  // Manifests 必须先加载：preload() 依赖它来注册 spritesheet。
  // 失败时退化为 MANIFEST_MISSING，场景走"纯色矩形兜底"。
  useEffect(() => {
    let cancelled = false;
    loadManifests()
      .then((m) => {
        if (!cancelled) setManifests(m);
      })
      .catch((e) => {
        console.warn("[manifest] load failed; using placeholder rectangles", e);
        if (!cancelled) {
          setManifests(MANIFEST_MISSING);
          setManifestError("资源 manifest 未就绪，暂用占位方块显示");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || gameRef.current || manifests === null) return;

    const createGame = () => {
      if (gameRef.current) return;

      const scene = new TownScene();
      // 必须在 Phaser 启动场景生命周期之前把 manifests 挂上去，
      // 让 preload() 能直接读取、一次性注册所有 spritesheet。
      scene.setManifests(manifests === MANIFEST_MISSING ? null : manifests);

      const game = new Phaser.Game({
        type: Phaser.AUTO,
        parent: container,
        backgroundColor: "#0c1015",
        scale: {
          mode: Phaser.Scale.RESIZE,
          autoCenter: Phaser.Scale.CENTER_BOTH,
          parent: container,
          width: "100%",
          height: "100%",
        },
        render: {
          // LPC 与 Kenney 都是像素艺术，关闭抗锯齿避免模糊
          pixelArt: true,
          antialias: false,
        },
        scene: [scene],
      });
      gameRef.current = game;

      // onReady 在 TownScene.create() 末尾调用，所有容器已就绪。
      // Guard：React Strict Mode 下 useEffect 可能挂载两次，清理旧 game1 后
      // 异步 boot 仍可能完成；判断当前 game 仍是激活实例再触发。
      scene.setCreateCallback(() => {
        if (gameRef.current !== game) return;
        onReady(scene);
      });
    };

    // Scale.RESIZE 在容器 0×0 时会抛出 WebGL 帧缓冲错误。
    // 用 ResizeObserver 等待真实尺寸。
    let ro: ResizeObserver | null = null;
    if (container.clientWidth > 0 && container.clientHeight > 0) {
      createGame();
    } else {
      ro = new ResizeObserver(() => {
        if (container.clientWidth > 0 && container.clientHeight > 0) {
          ro?.disconnect();
          ro = null;
          createGame();
        }
      });
      ro.observe(container);
    }

    return () => {
      ro?.disconnect();
      gameRef.current?.destroy(true);
      gameRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifests]);

  return (
    <div
      ref={containerRef}
      style={{ width: "100%", height: "100%", position: "relative" }}
    >
      {manifestError && (
        <div
          style={{
            position: "absolute",
            top: 6,
            right: 6,
            background: "rgba(220,100,100,0.78)",
            color: "#fff",
            fontSize: 11,
            padding: "2px 8px",
            borderRadius: 4,
            zIndex: 10,
            pointerEvents: "none",
          }}
        >
          {manifestError}
        </div>
      )}
    </div>
  );
}
