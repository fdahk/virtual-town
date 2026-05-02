// 阶段 19：富气泡组件管理器。
// 每个 NPC 头顶一个 Container：圆角矩形背景 + 状态 emoji + 可选短文字。
// 取代原先单一 STATE_EMOJI Text 节点。
//
// 行为：
// - persistent 状态（CHATTING / WORKING / EATING / RESTING / SLEEPING / AWAITING_RESPONSE）：
//   持续显示，不淡出。
// - transient 状态（BUSY_REFUSING + recent dialogue snippet）：3 秒后淡出。

import Phaser from "phaser";

export type BubbleVariant =
  | "chat" // 💬 对话中
  | "work" // 💼 工作中
  | "eat" // 🍴 吃饭
  | "rest" // ☕ 休息
  | "sleep" // 💤 睡眠
  | "thinking" // 💭 思考
  | "awaiting" // ❓ 等待对方响应
  | "refused" // ❌ 软拒绝
  | "interact" // 💡 一般交互
  | "danger" // ⚠️ 异常
  | "blocked" // 🚧 被阻挡
  | "drowning" // 🆘 溺水
  | "speech"; // 💬 临时台词气泡（NPC-NPC 对话）

const VARIANT_ICON: Record<BubbleVariant, string> = {
  chat: "💬",
  work: "💼",
  eat: "🍴",
  rest: "☕",
  sleep: "💤",
  thinking: "💭",
  awaiting: "❓",
  refused: "❌",
  interact: "💡",
  danger: "⚠️",
  blocked: "🚧",
  drowning: "🆘",
  speech: "💬",
};

const VARIANT_PERSISTENT: Record<BubbleVariant, boolean> = {
  chat: true,
  work: true,
  eat: true,
  rest: true,
  sleep: true,
  thinking: true,
  awaiting: true,
  refused: false,
  interact: false,
  danger: true,
  blocked: false,
  drowning: true,
  speech: false,
};

interface BubbleNode {
  container: Phaser.GameObjects.Container;
  bg: Phaser.GameObjects.Graphics;
  icon: Phaser.GameObjects.Text;
  text: Phaser.GameObjects.Text;
  variant: BubbleVariant | null;
  hideTimer: Phaser.Time.TimerEvent | null;
}

const PADDING_X = 6;
const PADDING_Y = 3;
const ICON_GAP = 4;

/** 把一段长文本截断到指定字符数。 */
function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max - 1) + "…";
}

export class BubbleManager {
  private scene: Phaser.Scene;
  private nodes = new Map<string, BubbleNode>();
  private layer: Phaser.GameObjects.Container;

  constructor(scene: Phaser.Scene, layer: Phaser.GameObjects.Container) {
    this.scene = scene;
    this.layer = layer;
  }

  /** 给某个 agent 创建或获取气泡节点（默认隐藏）。 */
  ensure(agentId: string, x: number, y: number): BubbleNode {
    let node = this.nodes.get(agentId);
    if (node) return node;

    const bg = this.scene.add.graphics();
    const icon = this.scene.add.text(0, 0, "", {
      fontSize: "13px",
      color: "#ffffff",
    });
    icon.setOrigin(0, 0.5);
    const text = this.scene.add.text(0, 0, "", {
      fontSize: "11px",
      color: "#ffffff",
    });
    text.setOrigin(0, 0.5);
    const container = this.scene.add.container(x, y, [bg, icon, text]);
    container.setVisible(false);
    container.setDepth(10);
    this.layer.add(container);

    node = {
      container,
      bg,
      icon,
      text,
      variant: null,
      hideTimer: null,
    };
    this.nodes.set(agentId, node);
    return node;
  }

  setPosition(agentId: string, x: number, y: number): void {
    const node = this.nodes.get(agentId);
    if (!node) return;
    node.container.setPosition(x, y);
  }

  /** 用 tween 平滑移动到目标位置（与 sprite tween 同步）。 */
  tweenTo(agentId: string, x: number, y: number, duration = 260): void {
    const node = this.nodes.get(agentId);
    if (!node) return;
    this.scene.tweens.killTweensOf(node.container);
    this.scene.tweens.add({
      targets: node.container,
      x,
      y,
      duration,
      ease: "Cubic.easeOut",
    });
  }

  /** 显示一个气泡变体（覆盖之前的）。可选附加台词文字。 */
  show(
    agentId: string,
    variant: BubbleVariant,
    options?: { text?: string; durationMs?: number },
  ): void {
    const node = this.nodes.get(agentId);
    if (!node) return;

    const icon = VARIANT_ICON[variant];
    const label = options?.text ? truncate(options.text, 14) : "";

    node.icon.setText(icon);
    node.text.setText(label);
    node.variant = variant;

    // 测量宽高 → 重绘背景
    const iconW = node.icon.width;
    const textW = node.text.width;
    const totalW = iconW + (label ? ICON_GAP + textW : 0);
    const totalH = Math.max(node.icon.height, node.text.height);
    const bgW = totalW + PADDING_X * 2;
    const bgH = totalH + PADDING_Y * 2;

    node.bg.clear();
    node.bg.fillStyle(0x000000, 0.62);
    node.bg.fillRoundedRect(-bgW / 2, -bgH, bgW, bgH, 6);
    // 小尾巴（指向下方角色头顶）
    node.bg.fillTriangle(-3, 0, 3, 0, 0, 5);

    // icon + text 居中布局
    const startX = -totalW / 2;
    const midY = -bgH / 2;
    node.icon.setPosition(startX, midY);
    node.text.setPosition(startX + iconW + (label ? ICON_GAP : 0), midY);

    node.container.setVisible(true);
    node.container.setAlpha(1);

    // 取消之前的隐藏定时器
    if (node.hideTimer) {
      node.hideTimer.remove(false);
      node.hideTimer = null;
    }

    const persistent = VARIANT_PERSISTENT[variant];
    const duration = options?.durationMs ?? (persistent ? 0 : 3000);
    if (duration > 0) {
      node.hideTimer = this.scene.time.delayedCall(duration, () => {
        this.fadeOut(agentId);
      });
    }
  }

  /** 显示临时台词气泡（NPC-NPC 对话场景使用）。 */
  showSpeech(agentId: string, text: string, durationMs = 4000): void {
    this.show(agentId, "speech", { text, durationMs });
  }

  /** 隐藏气泡（淡出后销毁可见性）。 */
  fadeOut(agentId: string): void {
    const node = this.nodes.get(agentId);
    if (!node) return;
    if (node.hideTimer) {
      node.hideTimer.remove(false);
      node.hideTimer = null;
    }
    this.scene.tweens.add({
      targets: node.container,
      alpha: 0,
      duration: 260,
      onComplete: () => {
        node.container.setVisible(false);
        node.variant = null;
      },
    });
  }

  /** 销毁某个 agent 的气泡（agent 离开场景）。 */
  destroyAgent(agentId: string): void {
    const node = this.nodes.get(agentId);
    if (!node) return;
    if (node.hideTimer) node.hideTimer.remove(false);
    node.container.destroy(true);
    this.nodes.delete(agentId);
  }

  /** 销毁全部气泡（场景切换时调用）。 */
  destroyAll(): void {
    for (const [, node] of this.nodes) {
      if (node.hideTimer) node.hideTimer.remove(false);
      node.container.destroy(true);
    }
    this.nodes.clear();
  }
}

/** 把后端 AgentState 字符串映射到气泡 variant。返回 null 表示不显示气泡。 */
export function mapStateToBubble(state: string): BubbleVariant | null {
  switch (state) {
    case "CHATTING":
      return "chat";
    case "WORKING":
      return "work";
    case "EATING":
      return "eat";
    case "RESTING":
      return "rest";
    case "SLEEPING":
      return "sleep";
    case "THINKING":
    case "PLANNING_PATH":
      return "thinking";
    case "AWAITING_RESPONSE":
      return "awaiting";
    case "BUSY_REFUSING":
      return "refused";
    case "INTERACTING":
      return "interact";
    case "DROWNING":
      return "drowning";
    case "BLOCKED":
      return "blocked";
    case "PANIC":
      return "danger";
    case "MOVING":
    case "WAITING":
    case "IDLE":
    default:
      return null;
  }
}
