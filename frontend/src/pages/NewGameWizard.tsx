import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { NPCEditor, type NPCDraft } from "../components/NPCEditor";

interface Props {
  onCancel: () => void;
  onCreated: () => void;
}

type Step = "world" | "npcs" | "player" | "submitting";

interface ArtCatalog {
  lpc_layers: Record<string, { id: string; label: string; file: string }[]>;
  animal_colors: Record<string, { id: string; label: string; file: string }[]>;
  occupation_presets: Record<string, { workplace: string | null; schedule: string }>;
  schedule_templates: Record<string, Record<string, unknown>[]>;
}

/**
 * 新游戏三步向导：
 *   Step 1 - 世界参数（seed、地图尺寸）
 *   Step 2 - NPC 清单（默认 22 NPC，可删除/编辑/复制/新增）
 *   Step 3 - 玩家角色（同样用 NPCEditor 配置）
 *   提交  - POST /api/games/new → 等待生成 → onCreated()
 */
export function NewGameWizard({ onCancel, onCreated }: Props) {
  const [step, setStep] = useState<Step>("world");

  // 世界参数
  const [seed, setSeed] = useState<number>(42);
  const [width, setWidth] = useState<number>(80);
  const [height, setHeight] = useState<number>(60);

  // 模板与 art 目录
  const [art, setArt] = useState<ArtCatalog | null>(null);
  const [humans, setHumans] = useState<NPCDraft[]>([]);
  const [animals, setAnimals] = useState<NPCDraft[]>([]);
  const [player, setPlayer] = useState<NPCDraft | null>(null);

  // 编辑状态
  const [editing, setEditing] = useState<{
    kind: "human" | "animal" | "player";
    index: number;
  } | null>(null);

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [tpl, catalog, worldDef] = await Promise.all([
          api.getNpcTemplates(),
          api.getArtCatalog(),
          api.getWorldDefaults(),
        ]);
        setHumans(tpl.humans as unknown as NPCDraft[]);
        setAnimals(tpl.animals as unknown as NPCDraft[]);
        setPlayer(tpl.player as unknown as NPCDraft);
        setArt(catalog as unknown as ArtCatalog);
        setWidth(worldDef.outdoor_width);
        setHeight(worldDef.outdoor_height);
      } catch (err) {
        setError((err as { message?: string }).message ?? "无法加载默认模板");
      }
    })();
  }, []);

  const totalNpcCount = humans.length + animals.length;

  const submit = async () => {
    setStep("submitting");
    setError(null);
    try {
      await api.postNewGame({
        seed,
        outdoor_width: width,
        outdoor_height: height,
        humans: humans as unknown as Record<string, unknown>[],
        animals: animals as unknown as Record<string, unknown>[],
        player: player as unknown as Record<string, unknown>,
        auto_start: true,
      });
      onCreated();
    } catch (err) {
      setError((err as { message?: string }).message ?? "新游戏创建失败");
      setStep("player");
    }
  };

  if (editing && art) {
    let draft: NPCDraft | null = null;
    if (editing.kind === "human") draft = humans[editing.index];
    else if (editing.kind === "animal") draft = animals[editing.index];
    else if (editing.kind === "player") draft = player;
    if (!draft) return null;
    return (
      <NPCEditor
        kind={editing.kind}
        draft={draft}
        artCatalog={art}
        onCancel={() => setEditing(null)}
        onSave={(next) => {
          if (editing.kind === "human") {
            const copy = humans.slice();
            copy[editing.index] = next;
            setHumans(copy);
          } else if (editing.kind === "animal") {
            const copy = animals.slice();
            copy[editing.index] = next;
            setAnimals(copy);
          } else {
            setPlayer(next);
          }
          setEditing(null);
        }}
      />
    );
  }

  return (
    <div style={styles.page}>
      <header style={styles.header}>
        <button style={styles.cancelBtn} onClick={onCancel}>
          ← 返回开始页
        </button>
        <div style={styles.steps}>
          <StepDot active={step === "world"} label="1 · 世界" />
          <StepDot active={step === "npcs"} label="2 · 居民" />
          <StepDot active={step === "player"} label="3 · 玩家" />
        </div>
        <div style={{ width: 130 }} />
      </header>

      <div style={styles.body}>
        {step === "world" && (
          <WorldStep
            seed={seed}
            width={width}
            height={height}
            onSeed={setSeed}
            onWidth={setWidth}
            onHeight={setHeight}
            onNext={() => setStep("npcs")}
          />
        )}
        {step === "npcs" && (
          <NpcsStep
            humans={humans}
            animals={animals}
            onEdit={(kind, index) => setEditing({ kind, index })}
            onAddHuman={() => {
              const draft = makeBlankHuman(humans.length);
              setHumans([...humans, draft]);
              setEditing({ kind: "human", index: humans.length });
            }}
            onAddAnimal={() => {
              const draft = makeBlankAnimal(animals.length);
              setAnimals([...animals, draft]);
              setEditing({ kind: "animal", index: animals.length });
            }}
            onRemoveHuman={(idx) => setHumans(humans.filter((_, i) => i !== idx))}
            onRemoveAnimal={(idx) => setAnimals(animals.filter((_, i) => i !== idx))}
            onCopyHuman={(idx) => {
              const src = humans[idx];
              const copy = {
                ...src,
                id: `${src.id}_copy_${Date.now().toString(36)}`,
                name: `${src.name}（副本）`,
              };
              setHumans([...humans, copy]);
            }}
            onBack={() => setStep("world")}
            onNext={() => setStep("player")}
          />
        )}
        {step === "player" && player && (
          <PlayerStep
            player={player}
            onEdit={() => setEditing({ kind: "player", index: 0 })}
            onBack={() => setStep("npcs")}
            onSubmit={submit}
            totalNpcCount={totalNpcCount}
          />
        )}
        {step === "submitting" && (
          <div style={styles.submittingBox}>
            <h2>正在创建世界…</h2>
            <p>地图生成器正在铺开 {width}×{height} 的小镇，并放置 {totalNpcCount} 位居民与他们的家。</p>
          </div>
        )}

        {error && <div style={styles.errorBox}>{error}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 子步骤
// ---------------------------------------------------------------------------

function WorldStep(props: {
  seed: number;
  width: number;
  height: number;
  onSeed: (n: number) => void;
  onWidth: (n: number) => void;
  onHeight: (n: number) => void;
  onNext: () => void;
}) {
  return (
    <section style={styles.section}>
      <h2 style={styles.h2}>世界参数</h2>
      <p style={styles.help}>
        地图遵循"北区河岸住宅 · 中区商业广场 · 南区农场森林"参数化模板，
        RNG seed 控制装饰物（树木、花草、蘑菇）的位置。
      </p>
      <div style={styles.row}>
        <label style={styles.field}>
          <span>RNG Seed</span>
          <input
            type="number"
            value={props.seed}
            onChange={(e) => props.onSeed(Number(e.target.value) || 0)}
            style={styles.input}
          />
        </label>
        <label style={styles.field}>
          <span>地图宽（格）</span>
          <input
            type="number"
            value={props.width}
            min={40}
            max={200}
            onChange={(e) => props.onWidth(Number(e.target.value) || 120)}
            style={styles.input}
          />
        </label>
        <label style={styles.field}>
          <span>地图高（格）</span>
          <input
            type="number"
            value={props.height}
            min={30}
            max={200}
            onChange={(e) => props.onHeight(Number(e.target.value) || 90)}
            style={styles.input}
          />
        </label>
      </div>
      <div style={styles.actions}>
        <button style={styles.primaryBtn} onClick={props.onNext}>
          下一步：编辑居民
        </button>
      </div>
    </section>
  );
}

function NpcsStep(props: {
  humans: NPCDraft[];
  animals: NPCDraft[];
  onEdit: (kind: "human" | "animal" | "player", index: number) => void;
  onAddHuman: () => void;
  onAddAnimal: () => void;
  onRemoveHuman: (i: number) => void;
  onRemoveAnimal: (i: number) => void;
  onCopyHuman: (i: number) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  return (
    <section style={styles.section}>
      <h2 style={styles.h2}>居民</h2>
      <p style={styles.help}>
        默认 18 位人类 NPC + 4 只动物。你可以删除不喜欢的、复制后修改、或新增完全自定义的角色。
      </p>

      <h3 style={styles.h3}>人类（{props.humans.length}）</h3>
      <div style={styles.npcGrid}>
        {props.humans.map((h, idx) => (
          <NpcCard
            key={h.id}
            draft={h}
            onEdit={() => props.onEdit("human", idx)}
            onRemove={() => props.onRemoveHuman(idx)}
            onCopy={() => props.onCopyHuman(idx)}
          />
        ))}
        <button style={styles.addCard} onClick={props.onAddHuman}>
          + 新增人类
        </button>
      </div>

      <h3 style={styles.h3}>动物（{props.animals.length}）</h3>
      <div style={styles.npcGrid}>
        {props.animals.map((a, idx) => (
          <NpcCard
            key={a.id}
            draft={a}
            onEdit={() => props.onEdit("animal", idx)}
            onRemove={() => props.onRemoveAnimal(idx)}
          />
        ))}
        <button style={styles.addCard} onClick={props.onAddAnimal}>
          + 新增动物
        </button>
      </div>

      <div style={styles.actions}>
        <button style={styles.secondaryBtn} onClick={props.onBack}>
          上一步
        </button>
        <button style={styles.primaryBtn} onClick={props.onNext}>
          下一步：玩家
        </button>
      </div>
    </section>
  );
}

function PlayerStep(props: {
  player: NPCDraft;
  onEdit: () => void;
  onBack: () => void;
  onSubmit: () => void;
  totalNpcCount: number;
}) {
  return (
    <section style={styles.section}>
      <h2 style={styles.h2}>玩家</h2>
      <p style={styles.help}>
        玩家也是一名 Agent，会被 NPC 感知与记忆。
      </p>
      <div style={styles.npcGrid}>
        <NpcCard draft={props.player} onEdit={props.onEdit} />
      </div>
      <p style={styles.help}>
        即将创建：{props.totalNpcCount} 位居民 · 1 位玩家。
      </p>
      <div style={styles.actions}>
        <button style={styles.secondaryBtn} onClick={props.onBack}>
          上一步
        </button>
        <button style={styles.primaryBtn} onClick={props.onSubmit}>
          创建小镇并进入
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 小组件
// ---------------------------------------------------------------------------

function StepDot({ active, label }: { active: boolean; label: string }) {
  return (
    <span style={{
      ...styles.stepDot,
      background: active ? "#4b6fff" : "transparent",
      color: active ? "white" : "#94a0b3",
      borderColor: active ? "#4b6fff" : "rgba(255,255,255,0.15)",
    }}>
      {label}
    </span>
  );
}

function NpcCard({
  draft,
  onEdit,
  onRemove,
  onCopy,
}: {
  draft: NPCDraft;
  onEdit?: () => void;
  onRemove?: () => void;
  onCopy?: () => void;
}) {
  const color = draft.accent_color || "#cccccc";
  const subtitleParts = useMemo(() => {
    const parts: string[] = [];
    if (draft.entity_type === "human") {
      if (draft.age) parts.push(`${draft.age}岁`);
      if (draft.occupation) parts.push(draft.occupation);
    } else if (draft.entity_type === "animal") {
      parts.push(draft.species ?? "动物");
      if (draft.art?.color) parts.push(String(draft.art.color));
    } else {
      parts.push("玩家");
    }
    return parts.filter(Boolean).join(" · ");
  }, [draft]);

  return (
    <div style={styles.card}>
      <div style={{ ...styles.avatar, background: color }} />
      <div style={styles.cardBody}>
        <div style={styles.cardName}>{draft.name}</div>
        <div style={styles.cardSub}>{subtitleParts}</div>
        <div style={styles.cardActions}>
          <button style={styles.miniBtn} onClick={onEdit}>编辑</button>
          {onCopy && <button style={styles.miniBtn} onClick={onCopy}>复制</button>}
          {onRemove && <button style={styles.miniDanger} onClick={onRemove}>删除</button>}
        </div>
      </div>
    </div>
  );
}

function makeBlankHuman(index: number): NPCDraft {
  const id = `npc_custom_${Date.now().toString(36)}_${index}`;
  return {
    id,
    name: `自定义居民 ${index + 1}`,
    entity_type: "human",
    age: 25,
    gender: "female",
    occupation: "程序员",
    personality: ["友善"],
    background: "",
    lifestyle: "",
    long_term_goals: [],
    schedule_id: "remote_worker",
    art: {
      kind: "lpc_human",
      sheet_id: id,
      layers: {
        body: "female_light",
        head: "female_light",
        hair: "long_black",
        torso: "ls_fem_bluegray",
        legs: "pants_fem_black",
        feet: "shoes_fem_brown",
      },
    },
    accent_color: "#cccccc",
    has_home: true,
    upstairs_of: null,
    cohabits_with: null,
    preferred_district: "north",
    owner_id: null,
    notes: "",
    portrait_url: null,
    species: null,
  };
}

function makeBlankAnimal(index: number): NPCDraft {
  const id = `animal_custom_${Date.now().toString(36)}_${index}`;
  return {
    id,
    name: `小动物 ${index + 1}`,
    entity_type: "animal",
    age: null,
    gender: null,
    species: "cat",
    occupation: null,
    personality: ["亲人"],
    background: "",
    lifestyle: null,
    long_term_goals: [],
    schedule_id: null,
    art: {
      kind: "lpc_animal",
      species: "cat",
      color: "white",
      sheet_id: "lpc_cats",
    },
    accent_color: "#f0f0f0",
    has_home: false,
    upstairs_of: null,
    cohabits_with: null,
    preferred_district: "north",
    owner_id: null,
    notes: "",
    portrait_url: null,
  };
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100%",
    boxSizing: "border-box",
    background:
      "radial-gradient(ellipse at top, #1d2736 0%, #0b0f14 60%, #070a10 100%)",
    color: "#e8edf5",
    paddingBottom: "max(40px, env(safe-area-inset-bottom, 0px))",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 24px",
    borderBottom: "1px solid rgba(255,255,255,0.05)",
    background: "rgba(11, 15, 20, 0.92)",
    backdropFilter: "blur(10px)",
    position: "sticky",
    top: 0,
    zIndex: 40,
  },
  cancelBtn: {
    background: "transparent",
    border: "none",
    color: "#cbd2db",
    fontSize: 14,
    cursor: "pointer",
  },
  steps: {
    display: "flex",
    gap: 8,
  },
  stepDot: {
    padding: "6px 14px",
    borderRadius: 999,
    fontSize: 13,
    fontWeight: 600,
    border: "1px solid",
  },
  body: {
    maxWidth: 1100,
    margin: "0 auto",
    padding: "32px 24px max(48px, env(safe-area-inset-bottom, 0px))",
    paddingTop: 24,
  },
  section: {
    background: "rgba(20, 25, 32, 0.7)",
    borderRadius: 12,
    border: "1px solid rgba(255,255,255,0.05)",
    padding: 28,
  },
  h2: {
    margin: "0 0 12px 0",
    fontSize: 22,
  },
  h3: {
    margin: "26px 0 12px 0",
    fontSize: 15,
    color: "#a4b2c5",
    fontWeight: 600,
  },
  help: {
    marginTop: 0,
    fontSize: 13,
    color: "#94a0b3",
    lineHeight: 1.6,
  },
  row: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
    gap: 12,
    marginTop: 14,
  },
  field: {
    display: "grid",
    gap: 6,
    fontSize: 13,
    color: "#cbd2db",
  },
  input: {
    padding: "9px 12px",
    borderRadius: 8,
    background: "#0d1117",
    color: "#f5f5f5",
    border: "1px solid rgba(255,255,255,0.1)",
    outline: "none",
    fontSize: 14,
  },
  actions: {
    marginTop: 24,
    display: "flex",
    justifyContent: "flex-end",
    gap: 12,
  },
  primaryBtn: {
    padding: "10px 18px",
    border: "none",
    borderRadius: 8,
    background: "linear-gradient(120deg, #4b6fff, #8151ff)",
    color: "white",
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  },
  secondaryBtn: {
    padding: "10px 18px",
    border: "1px solid rgba(255,255,255,0.15)",
    borderRadius: 8,
    background: "transparent",
    color: "#cbd2db",
    fontSize: 14,
    cursor: "pointer",
  },
  npcGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
    gap: 12,
  },
  card: {
    display: "flex",
    gap: 12,
    background: "rgba(13, 17, 23, 0.7)",
    border: "1px solid rgba(255,255,255,0.06)",
    borderRadius: 10,
    padding: 12,
  },
  avatar: {
    width: 44,
    height: 44,
    borderRadius: 8,
    flexShrink: 0,
  },
  cardBody: {
    display: "grid",
    gap: 4,
    flex: 1,
    minWidth: 0,
  },
  cardName: {
    fontSize: 14,
    fontWeight: 600,
  },
  cardSub: {
    fontSize: 12,
    color: "#8b96a8",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  cardActions: {
    display: "flex",
    gap: 6,
    marginTop: 4,
  },
  miniBtn: {
    padding: "4px 10px",
    fontSize: 12,
    borderRadius: 6,
    border: "1px solid rgba(255,255,255,0.1)",
    background: "rgba(255,255,255,0.04)",
    color: "#cbd2db",
    cursor: "pointer",
  },
  miniDanger: {
    padding: "4px 10px",
    fontSize: 12,
    borderRadius: 6,
    border: "1px solid rgba(255, 96, 96, 0.3)",
    background: "rgba(255, 96, 96, 0.08)",
    color: "#ffacac",
    cursor: "pointer",
  },
  addCard: {
    padding: 12,
    borderRadius: 10,
    border: "1px dashed rgba(255,255,255,0.2)",
    background: "transparent",
    color: "#7d8aa0",
    cursor: "pointer",
    fontSize: 14,
    minHeight: 70,
  },
  errorBox: {
    marginTop: 16,
    background: "rgba(255, 96, 96, 0.15)",
    color: "#ffacac",
    padding: "10px 14px",
    borderRadius: 8,
    border: "1px solid rgba(255, 96, 96, 0.3)",
    fontSize: 13,
  },
  submittingBox: {
    textAlign: "center",
    padding: 60,
    fontSize: 16,
    color: "#cbd2db",
  },
};
