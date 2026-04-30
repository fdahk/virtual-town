import { useEffect, useRef } from "react";
import Phaser from "phaser";
import { TownScene } from "./scenes/TownScene";

interface Props {
  onReady(scene: TownScene): void;
}

export function PhaserGame({ onReady }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const gameRef = useRef<Phaser.Game | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || gameRef.current) return;

    const createGame = () => {
      if (gameRef.current) return;

      const scene = new TownScene();
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
          pixelArt: false,
        },
        scene: [scene],
      });
      gameRef.current = game;

      // scene.events (shortcut to sys.events) is only wired up after Phaser
      // finishes booting (ScenePlugin.boot). Wait for the READY event before
      // subscribing to scene lifecycle events.
      game.events.once(Phaser.Core.Events.READY, () => {
        scene.events.once(Phaser.Scenes.Events.CREATE, () => onReady(scene));
      });
    };

    // With Scale.RESIZE, Phaser immediately tries to create a WebGL framebuffer
    // sized to the parent element. If the container is still 0×0 (not yet laid
    // out), the framebuffer is incomplete and throws. Use ResizeObserver to
    // defer initialization until the container has real dimensions.
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
  }, []);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}
