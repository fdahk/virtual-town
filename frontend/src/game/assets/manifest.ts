// 资源 manifest 类型 + 加载器。严禁在业务代码里硬编码具体图片 URL。

export interface TilesetManifest {
  version: string;
  tilesets: {
    kenney_tiny_town: {
      id: string;
      url: string;
      tile_width: number;
      tile_height: number;
      columns: number;
      rows: number;
      total: number;
      display_scale: number;
      license: string;
      source: string;
      author: string;
    };
  };
  terrain_map: Record<string, number>;
  overlay_tiles: Record<string, number>;
}

export interface HumanAnimationDef {
  frames: number[];
  frameRate: number;
}

export interface SpritesManifest {
  version: string;
  humans: {
    frame_width: number;
    frame_height: number;
    columns: number;
    rows: number;
    display_scale: number;
    animations: Record<string, HumanAnimationDef>;
    license: string;
    source: string;
    sheets: Record<string, string>;
  };
  cats: AnimalSheetManifest;
  dogs: AnimalSheetManifest;
}

export interface AnimalSheetManifest {
  sheet_url: string;
  frame_width: number;
  frame_height: number;
  display_scale: number;
  colors: Record<string, { col_offset: number; row_offset: number }>;
  direction_rows: Record<"up" | "down" | "left" | "right", number>;
  walk_frame_count: number;
  license: string;
  source: string;
  assignments: Record<string, string>;
}

export interface PortraitsManifest {
  version: string;
  license: string;
  humans: Record<string, string>;
  animals: Record<string, string>;
}

export interface LoadedManifests {
  tilesets: TilesetManifest;
  sprites: SpritesManifest;
  portraits: PortraitsManifest;
}

const ENDPOINT = {
  tilesets: "/assets/manifest/tilesets.manifest.json",
  sprites: "/assets/manifest/sprites.manifest.json",
  portraits: "/assets/manifest/portraits.manifest.json",
};

let cached: LoadedManifests | null = null;

export async function loadManifests(): Promise<LoadedManifests> {
  if (cached) return cached;
  const [tilesets, sprites, portraits] = await Promise.all([
    fetch(ENDPOINT.tilesets).then((r) => r.json() as Promise<TilesetManifest>),
    fetch(ENDPOINT.sprites).then((r) => r.json() as Promise<SpritesManifest>),
    fetch(ENDPOINT.portraits).then((r) => r.json() as Promise<PortraitsManifest>),
  ]);
  cached = { tilesets, sprites, portraits };
  return cached;
}

/** 判断 manifest 中是否有具名资源；不存在时调用方应该走占位兜底。 */
export function resolveHumanSprite(
  m: LoadedManifests,
  agentId: string,
): string | null {
  return m.sprites.humans.sheets[agentId] ?? null;
}

export function resolveAnimalColor(
  m: LoadedManifests,
  agentId: string,
): { kind: "cat" | "dog"; color: string } | null {
  if (agentId in m.sprites.cats.assignments) {
    return { kind: "cat", color: m.sprites.cats.assignments[agentId] };
  }
  if (agentId in m.sprites.dogs.assignments) {
    return { kind: "dog", color: m.sprites.dogs.assignments[agentId] };
  }
  return null;
}

export function resolvePortrait(m: LoadedManifests, id: string): string | null {
  return m.portraits.humans[id] ?? m.portraits.animals[id] ?? null;
}
