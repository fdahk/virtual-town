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
    if (!containerRef.current || gameRef.current) return;

    const scene = new TownScene();
    const game = new Phaser.Game({
      type: Phaser.AUTO,
      parent: containerRef.current,
      backgroundColor: "#0c1015",
      scale: {
        mode: Phaser.Scale.RESIZE,
        autoCenter: Phaser.Scale.CENTER_BOTH,
        parent: containerRef.current,
        width: "100%",
        height: "100%",
      },
      render: {
        pixelArt: false,
      },
      scene: [scene],
    });
    gameRef.current = game;

    const handleReady = () => {
      onReady(scene);
    };
    scene.events.once(Phaser.Scenes.Events.CREATE, handleReady);

    return () => {
      game.destroy(true);
      gameRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}
