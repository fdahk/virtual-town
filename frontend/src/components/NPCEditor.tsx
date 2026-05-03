import { useMemo, useState } from "react";

export interface NPCDraft {
  id: string;
  name: string;
  entity_type: "human" | "animal" | "player";
  age?: number | null;
  gender?: "male" | "female" | null;
  species?: string | null;
  occupation?: string | null;
  personality: string[];
  background: string;
  lifestyle?: string | null;
  long_term_goals: string[];
  schedule_id?: string | null;
  art: Record<string, unknown> & {
    kind?: string;
    sheet_id?: string;
    layers?: Record<string, string>;
    species?: string;
    color?: string;
  };
  accent_color: string;
  has_home: boolean;
  upstairs_of: string | null;
  cohabits_with: string | null;
  preferred_district: "north" | "center" | "south";
  owner_id?: string | null;
  notes?: string;
  portrait_url?: string | null;
}

interface ArtCatalog {
  lpc_layers: Record<string, { id: string; label: string; file: string }[]>;
  animal_colors: Record<string, { id: string; label: string; file: string }[]>;
  occupation_presets: Record<string, { workplace: string | null; schedule: string }>;
  schedule_templates: Record<string, Record<string, unknown>[]>;
}

interface Props {
  kind: "human" | "animal" | "player";
  draft: NPCDraft;
  artCatalog: ArtCatalog;
  onCancel: () => void;
  onSave: (draft: NPCDraft) => void;
}

/**
 * NPC 编辑器（单页表单）。
 *
 * 字段分组：
 *  - 基础信息（姓名 / 年龄 / 性别 / 偏好区域）
 *  - 人格 & 背景（personality 多选 + background / lifestyle / long_term_goals 文本）
 *  - 职业 & schedule（仅 human）
 *  - 美术（人类：LPC 各层下拉；动物：物种 + 颜色）
 *  - 居住（cohabits_with / upstairs_of / has_home）
 *
 * 实时预览：用 accent_color + LPC 层组合拼出一个色块和层文本提示，
 * 真实 LPC 合成图由后端 ``POST /api/games/preview-sprite`` 返回（暂未实现，
 * 本组件中以颜色 + 层 ID 摘要替代）。
 */
export function NPCEditor({ kind, draft: initial, artCatalog, onCancel, onSave }: Props) {
  const [draft, setDraft] = useState<NPCDraft>(initial);

  const update = <K extends keyof NPCDraft>(key: K, value: NPCDraft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  const updateLayer = (slot: string, value: string) =>
    setDraft((d) => ({
      ...d,
      art: {
        ...d.art,
        layers: { ...((d.art.layers as Record<string, string>) || {}), [slot]: value },
      },
    }));

  const updateArt = <K extends keyof NPCDraft["art"]>(key: K, value: NPCDraft["art"][K]) =>
    setDraft((d) => ({ ...d, art: { ...d.art, [key]: value } }));

  const personalityText = draft.personality.join("、");
  const goalsText = draft.long_term_goals.join("\n");

  return (
    <div style={styles.modal}>
      <div style={styles.dialog}>
        <header style={styles.header}>
          <h2 style={{ margin: 0 }}>
            编辑 · {kind === "player" ? "玩家" : kind === "animal" ? "动物" : "人类"} · {draft.name}
          </h2>
          <div style={{ display: "flex", gap: 8 }}>
            <button style={styles.secondaryBtn} onClick={onCancel}>取消</button>
            <button style={styles.primaryBtn} onClick={() => onSave(draft)}>保存</button>
          </div>
        </header>

        <div style={styles.body}>
          <div style={styles.column}>
            <Section title="基础信息">
              <Field label="姓名">
                <input
                  style={styles.input}
                  value={draft.name}
                  onChange={(e) => update("name", e.target.value)}
                  maxLength={32}
                />
              </Field>
              <Field label="ID（不可改）">
                <input style={styles.inputDisabled} value={draft.id} disabled />
              </Field>
              {kind !== "animal" && (
                <Row>
                  <Field label="年龄">
                    <input
                      type="number"
                      style={styles.input}
                      value={draft.age ?? ""}
                      onChange={(e) => update("age", Number(e.target.value) || null)}
                    />
                  </Field>
                  <Field label="性别">
                    <select
                      style={styles.input}
                      value={draft.gender ?? ""}
                      onChange={(e) => update("gender", (e.target.value || null) as NPCDraft["gender"])}
                    >
                      <option value="">未指定</option>
                      <option value="female">女</option>
                      <option value="male">男</option>
                    </select>
                  </Field>
                </Row>
              )}
              <Field label="偏好区域（影响住宅分配）">
                <select
                  style={styles.input}
                  value={draft.preferred_district}
                  onChange={(e) => update("preferred_district", e.target.value as NPCDraft["preferred_district"])}
                >
                  <option value="north">北区（河岸住宅）</option>
                  <option value="center">中区（商业广场）</option>
                  <option value="south">南区（农场森林）</option>
                </select>
              </Field>
            </Section>

            <Section title="人格 & 背景">
              <Field label="性格标签（顿号或逗号分隔）">
                <input
                  style={styles.input}
                  value={personalityText}
                  onChange={(e) =>
                    update("personality", e.target.value.split(/[、,，]/).map((s) => s.trim()).filter(Boolean))
                  }
                />
              </Field>
              <Field label="背景故事 (background)">
                <textarea
                  style={{ ...styles.input, minHeight: 80, resize: "vertical" }}
                  value={draft.background}
                  onChange={(e) => update("background", e.target.value)}
                />
              </Field>
              {kind !== "animal" && (
                <Field label="生活方式 (lifestyle)">
                  <input
                    style={styles.input}
                    value={draft.lifestyle ?? ""}
                    onChange={(e) => update("lifestyle", e.target.value)}
                  />
                </Field>
              )}
              <Field label="长期目标（每行一个）">
                <textarea
                  style={{ ...styles.input, minHeight: 60, resize: "vertical" }}
                  value={goalsText}
                  onChange={(e) => update("long_term_goals", e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))}
                />
              </Field>
            </Section>

            {kind === "human" && (
              <Section title="职业 & 日程">
                <Field label="职业">
                  <select
                    style={styles.input}
                    value={draft.occupation ?? ""}
                    onChange={(e) => {
                      const occ = e.target.value || null;
                      update("occupation", occ);
                      const preset = occ ? artCatalog.occupation_presets[occ] : null;
                      if (preset) update("schedule_id", preset.schedule);
                    }}
                  >
                    <option value="">未指定</option>
                    {Object.keys(artCatalog.occupation_presets).map((o) => (
                      <option key={o} value={o}>{o}</option>
                    ))}
                  </select>
                </Field>
                <Field label="作息模板 (schedule)">
                  <select
                    style={styles.input}
                    value={draft.schedule_id ?? ""}
                    onChange={(e) => update("schedule_id", e.target.value || null)}
                  >
                    <option value="">不参与作息</option>
                    {Object.keys(artCatalog.schedule_templates).map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                </Field>
              </Section>
            )}
          </div>

          <div style={styles.column}>
            <Section title={kind === "animal" ? "美术（动物）" : "美术（LPC 分层）"}>
              <ArtPreview draft={draft} />
              {kind === "animal" ? (
                <>
                  <Field label="物种">
                    <select
                      style={styles.input}
                      value={draft.species ?? "cat"}
                      onChange={(e) => {
                        const sp = e.target.value;
                        update("species", sp);
                        updateArt("species", sp);
                        updateArt("sheet_id", sp === "dog" ? "lpc_dogs" : "lpc_cats");
                      }}
                    >
                      <option value="cat">猫</option>
                      <option value="dog">狗</option>
                    </select>
                  </Field>
                  <Field label="毛色">
                    <select
                      style={styles.input}
                      value={(draft.art.color as string) ?? "white"}
                      onChange={(e) => updateArt("color", e.target.value)}
                    >
                      {(artCatalog.animal_colors[draft.species ?? "cat"] || []).map((opt) => (
                        <option key={opt.id} value={opt.id}>{opt.label}</option>
                      ))}
                    </select>
                  </Field>
                </>
              ) : (
                Object.keys(artCatalog.lpc_layers).map((slot) => (
                  <Field key={slot} label={LAYER_LABEL[slot] ?? slot}>
                    <select
                      style={styles.input}
                      value={(draft.art.layers as Record<string, string> | undefined)?.[slot] ?? ""}
                      onChange={(e) => updateLayer(slot, e.target.value)}
                    >
                      {artCatalog.lpc_layers[slot].map((opt) => (
                        <option key={opt.id} value={opt.id}>{opt.label}</option>
                      ))}
                    </select>
                  </Field>
                ))
              )}
              <Field label="主题色（兜底渲染 / UI 强调色）">
                <input
                  type="color"
                  style={{ ...styles.input, padding: 4, height: 40 }}
                  value={draft.accent_color}
                  onChange={(e) => update("accent_color", e.target.value)}
                />
              </Field>
            </Section>

            {kind === "human" && (
              <Section title="居住">
                <Field label="是否拥有独立住宅">
                  <select
                    style={styles.input}
                    value={String(draft.has_home)}
                    onChange={(e) => update("has_home", e.target.value === "true")}
                  >
                    <option value="true">是</option>
                    <option value="false">否（住公共建筑楼上 / 与人同住）</option>
                  </select>
                </Field>
                <Field label="住在公共建筑楼上 (upstairs_of)">
                  <input
                    style={styles.input}
                    placeholder="bakery / tavern / cafe（留空表示否）"
                    value={draft.upstairs_of ?? ""}
                    onChange={(e) => update("upstairs_of", e.target.value || null)}
                  />
                </Field>
                <Field label="与某 NPC 同住 (cohabits_with)">
                  <input
                    style={styles.input}
                    placeholder="如 npc_chenbo（留空表示无）"
                    value={draft.cohabits_with ?? ""}
                    onChange={(e) => update("cohabits_with", e.target.value || null)}
                  />
                </Field>
              </Section>
            )}

            {kind === "animal" && (
              <Section title="主人">
                <Field label="主人 NPC ID">
                  <input
                    style={styles.input}
                    placeholder="如 npc_xiaofang"
                    value={draft.owner_id ?? ""}
                    onChange={(e) => update("owner_id", e.target.value || null)}
                  />
                </Field>
              </Section>
            )}

            <Section title="备注">
              <Field label="设计/PM 备注（不影响 LLM）">
                <textarea
                  style={{ ...styles.input, minHeight: 50, resize: "vertical" }}
                  value={draft.notes ?? ""}
                  onChange={(e) => update("notes", e.target.value)}
                />
              </Field>
            </Section>
          </div>
        </div>
      </div>
    </div>
  );
}

const LAYER_LABEL: Record<string, string> = {
  body: "身体",
  head: "头部",
  hair: "发型",
  torso: "上衣",
  legs: "裤子",
  feet: "鞋",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={styles.section}>
      <h3 style={styles.sectionTitle}>{title}</h3>
      <div style={{ display: "grid", gap: 10 }}>{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={styles.field}>
      <span style={styles.fieldLabel}>{label}</span>
      {children}
    </label>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div style={styles.fieldRow}>{children}</div>;
}

function ArtPreview({ draft }: { draft: NPCDraft }) {
  const layersText = useMemo(() => {
    if (draft.art.kind === "lpc_animal") {
      return `${draft.species ?? "cat"} · ${draft.art.color ?? "white"}`;
    }
    const layers = (draft.art.layers as Record<string, string> | undefined) ?? {};
    return Object.entries(layers)
      .map(([k, v]) => `${LAYER_LABEL[k] ?? k}=${v}`)
      .join(" · ");
  }, [draft]);

  return (
    <div style={styles.preview}>
      <div style={{ ...styles.previewBlock, background: draft.accent_color }} />
      <div>
        <div style={{ fontSize: 13, color: "#cbd2db" }}>{draft.name}</div>
        <div style={{ fontSize: 11, color: "#8b96a8", marginTop: 2 }}>
          {layersText || "(暂无层)"}
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  modal: {
    position: "fixed",
    inset: 0,
    background: "rgba(7, 10, 16, 0.7)",
    display: "grid",
    placeItems: "center",
    zIndex: 9999,
    padding: 20,
    backdropFilter: "blur(4px)",
  },
  dialog: {
    width: "min(1100px, 96vw)",
    maxHeight: "94vh",
    background: "rgba(20, 25, 32, 0.95)",
    borderRadius: 14,
    border: "1px solid rgba(255,255,255,0.08)",
    color: "#e8edf5",
    overflow: "hidden",
    display: "grid",
    gridTemplateRows: "auto 1fr",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "14px 20px",
    borderBottom: "1px solid rgba(255,255,255,0.05)",
  },
  body: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 18,
    padding: 18,
    overflowY: "auto",
  },
  column: {
    display: "flex",
    flexDirection: "column",
    gap: 16,
    minWidth: 0,
  },
  section: {
    background: "rgba(13, 17, 23, 0.6)",
    border: "1px solid rgba(255,255,255,0.05)",
    borderRadius: 10,
    padding: 14,
  },
  sectionTitle: {
    margin: "0 0 10px 0",
    fontSize: 14,
    color: "#a4b2c5",
    fontWeight: 600,
  },
  field: {
    display: "grid",
    gap: 6,
  },
  fieldLabel: {
    fontSize: 12,
    color: "#94a0b3",
  },
  fieldRow: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 10,
  },
  input: {
    padding: "8px 10px",
    borderRadius: 6,
    background: "#0d1117",
    color: "#f5f5f5",
    border: "1px solid rgba(255,255,255,0.1)",
    outline: "none",
    fontSize: 13,
  },
  inputDisabled: {
    padding: "8px 10px",
    borderRadius: 6,
    background: "#0d1117",
    color: "#7c8ca3",
    border: "1px solid rgba(255,255,255,0.1)",
    fontSize: 13,
  },
  primaryBtn: {
    padding: "8px 16px",
    border: "none",
    borderRadius: 6,
    background: "linear-gradient(120deg, #4b6fff, #8151ff)",
    color: "white",
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
  },
  secondaryBtn: {
    padding: "8px 16px",
    border: "1px solid rgba(255,255,255,0.15)",
    borderRadius: 6,
    background: "transparent",
    color: "#cbd2db",
    fontSize: 13,
    cursor: "pointer",
  },
  preview: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: 12,
    borderRadius: 8,
    background: "rgba(255,255,255,0.03)",
    border: "1px solid rgba(255,255,255,0.05)",
  },
  previewBlock: {
    width: 56,
    height: 56,
    borderRadius: 8,
    flexShrink: 0,
    boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.2)",
  },
};
