import { useState } from "react";
import type { CSSProperties } from "react";

/** 剪贴板写入（支持非 HTTPS localhost 场景的 execCommand 回退）。 */
export async function copyObservabilityText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

const BTN_STYLE: CSSProperties = {
  background: "transparent",
  border: "none",
  padding: 0,
  margin: 0,
  cursor: "pointer",
  color: "#79c0ff",
  fontFamily: "monospace",
  fontSize: "inherit",
  textAlign: "left",
  textDecoration: "underline",
  textUnderlineOffset: 2,
  maxWidth: "100%",
};

type Props = {
  id: string;
  /** 省略显示时保留前 N 个字符；``full`` 为 true 时忽略 */
  previewChars?: number;
  /** 不省略，整段可点复制 */
  full?: boolean;
};

/**
 * 观测台里 trace / request 等长 ID：默认显示前缀 + …，点击复制完整值。
 */
export function CopyableTruncatedId({ id, previewChars = 12, full = false }: Props) {
  const [copied, setCopied] = useState(false);
  const truncated = !full && id.length > previewChars;
  const label = truncated ? `${id.slice(0, previewChars)}…` : id;

  async function onClick(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    const ok = await copyObservabilityText(id);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      title={`${id}\n点击复制完整 ID`}
      style={{
        ...BTN_STYLE,
        color: copied ? "#7bd389" : BTN_STYLE.color,
        overflow: truncated ? "hidden" : undefined,
        textOverflow: truncated ? "ellipsis" : undefined,
        whiteSpace: truncated ? "nowrap" : "pre-wrap",
        wordBreak: full ? "break-all" : undefined,
      }}
    >
      {copied ? "✓ 已复制" : label}
    </button>
  );
}
