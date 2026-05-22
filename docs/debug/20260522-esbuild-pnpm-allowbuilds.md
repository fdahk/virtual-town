# esbuild 是什么？为何换机 `pnpm install` 才报 `ERR_PNPM_IGNORED_BUILDS`？

## 1. 一句话

**esbuild** 是一个极快的 JavaScript/TypeScript **打包与转译**工具（用 Go 实现）。本仓库前端使用 **Vite**，Vite 在开发与生产构建里会依赖 **esbuild**；**esbuild 安装后需要跑自带的安装脚本**，把对应操作系统与 CPU 架构的**原生二进制**下载/落地到 `node_modules`。  
从 **pnpm 10+ / 11** 起，出于供应链安全，**默认不再随便执行依赖的这类脚本**，除非在配置里**显式放行**——因此会出现 `Ignored build scripts: esbuild` / `ERR_PNPM_IGNORED_BUILDS`。

---

## 2. esbuild 在本项目中的角色

| 层级 | 说明 |
|------|------|
| 你直接装的 | `vite`（见 `frontend/package.json` 的 `devDependencies`） |
| 间接依赖 | Vite 会拉取 **esbuild**（版本由 lockfile 锁定，例如 `esbuild@0.21.x`） |
| 实际作用 | 开发时快速依赖预构建、部分语法的低层转换等（你通常**不直接写** esbuild 配置） |
| 为何有「脚本」 | esbuild 通过 `postinstall`（或同类生命周期）执行 `node install.js`，为当前平台准备**原生可执行文件** |

因此：**即使业务代码里从不 import `esbuild`，只要用 Vite，就几乎一定会装到它。**

---

## 3. 为什么「最开始开发」没事，换电脑克隆仓库就有事？

常见原因往往**同时**成立，下面按概率从高到低说明。

### 3.1 pnpm 版本与策略升级（最常见）

pnpm 在 **v9** 后期到 **v10 / v11** 逐步加强了 **「依赖构建脚本」** 的管控（与 `strictDepBuilds`、允许列表等概念相关）。**旧版 pnpm 默认更宽松**：esbuild 的安装脚本会直接跑，看起来像「从没出过问题」。  
新电脑若通过 **Corepack** 安装了较新的 **pnpm 11**，或全局升级了 pnpm，**第一次在干净目录执行 `pnpm install`**，就会按新规则检查脚本 → **未被放行的 esbuild → 报错**。

**结论：** 不是你的业务代码变了，而是 **同一套依赖在新版包管理器的安全策略下行为不同**。

### 3.2 旧机器的 `node_modules` 缓存「掩盖」了问题

若旧环境很久没删过 `frontend/node_modules`，esbuild 二进制**早已就位**，你只是反复跑 `pnpm run dev`，**可能不会再次触发完整的「被拒脚本」路径**（或当时用的仍是旧 pnpm）。  
新机 **`git clone` + 全新安装**，没有这层缓存，问题就**一次性暴露**。

### 3.3 Node / 操作系统不同

esbuild 会为 **darwin / linux / win32** 以及 **arm64 / x64** 等准备不同二进制；换 ARM Mac、换 Linux CI、换 WSL，都会走一遍安装脚本。若在「脚本被禁用」的状态下安装，就会失败或留下不完整安装——**表现形式仍是「脚本未执行」这一类错误**。

### 3.4 曾一度存在无效占位配置（本项目历史情况）

若在 `pnpm-workspace.yaml` 里写入了**非布尔**的占位内容（例如把说明文字当成了 `allowBuilds` 的值），pnpm **不会**把它当成「已允许构建」，仍然会拒掉 esbuild。  
这就更容易出现：**某台机器从未拉过这份错误配置 vs 新机拉最新代码后立即失败**。

---

## 4. 本项目推荐的修复方式（已落库）

在 **`frontend/pnpm-workspace.yaml`** 中显式放行 esbuild：

```yaml
allowBuilds:
  esbuild: true
```

含义：**仅声明「允许执行 esbuild 这个依赖自带的构建/安装脚本」**，与业务源码无关；这是 **Vite + esbuild + pnpm 新版本** 下的常规做法。

官方对工作区与各字段的说明见：[pnpm-workspace.yaml](https://pnpm.io/pnpm-workspace_yaml)。

若在别的机器仍异常，可按顺序尝试：

1. `cd frontend && rm -rf node_modules && pnpm install`
2. 确认 `pnpm -v`（本仓库常见问题多出现在 **pnpm 11.x**）
3. 报错若提示其它带构建脚本的包，再按需把包名加到 `allowBuilds`（**只加你信任的依赖**）

---

## 5. 与「pnpm approve-builds」的关系

交互命令 `pnpm approve-builds` 会在本机写入/更新允许策略，效果和 **在仓库里提交 `pnpm-workspace.yaml`** 的目标类似：都是**把某些依赖标为允许执行脚本**。  
**团队开发更推荐把允许列表放进仓库（如本项目）**，这样所有人 clone 下来行为一致，不依赖各人本机是否已经点过 approve。

---

## 6. 换用 npm / yarn 会不会也出现「这个问题」？

要分两层说：**报错形式** vs **根因是否还存在**。

### 6.1 报错是否会一样？

不会。**`ERR_PNPM_IGNORED_BUILDS`** 以及「Must run `pnpm approve-builds`」这类提示，是 **pnpm（尤其 v10/v11）在「依赖脚本显式放行」策略下**才有的检查与错误码。

用 **npm** 或 **yarn** 安装依赖时，**不会出现这条具体的 pnpm 报错**。

### 6.2 根因——esbuild 仍需要跑安装脚本——在 npm/yarn 下还存在吗？

**存在。** Vite 仍然依赖 esbuild；esbuild **仍然要通过生命周期脚本**为当前平台准备二进制。区别在于：**各家包管理器默认「允不允许跑这些脚本」的策略不同**，因此「像不像本项目在 pnpm 11 里那样被拦」也不同。

| 工具 | 典型默认行为（安装依赖时） | 在什么情况下会像「pnpm 拒脚本」那样出问题 |
|------|---------------------------|------------------------------------------|
| **npm** | 一般会执行依赖的 `postinstall` 等脚本 | 使用了 **`npm install --ignore-scripts`**、或在 `.npmrc` 等处关闭了脚本执行；极少数企业镜像/策略也会统一关脚本 |
| **Yarn v1（Classic）** | 一般与 npm 类似，会跑脚本 | 使用了 **`yarn install --ignore-scripts`** |
| **Yarn Berry（v2+，含 v3/v4）** | 若在 `.yarnrc.yml` 里 **`enableScripts: true`**（常见），会为依赖运行脚本 | 若配置为 **`enableScripts: false`**，则会整体禁止依赖脚本，同样可能导致 esbuild **未装好二进制**，现象上接近「安装了包但 vite/esbuild 起不来」（错误信息不会是 pnpm 那一条） |

### 6.3 和本仓库的关系

本前端目录使用 **pnpm** 与 **`pnpm-lock.yaml`**；若在未改锁文件的前提下强行改用 npm/yarn，属于**另一类工作流**，且 **lockfile / 幽灵依赖扁平化差异**会带来额外漂移，文档不展开。**团队仍应以仓库既定方式（pnpm）为准**；本节仅回答「换成 npm/yarn 会不会撞上同一类痛点」：**不会有 pnpm 专有的报错名，但若关脚本仍可能摊上同一类「esbuild 没装好」**。

---

## 7. 参考

- esbuild：https://esbuild.github.io/  
- Vite（依赖 esbuild）：https://vitejs.dev/  
- pnpm workspace 配置：https://pnpm.io/pnpm-workspace_yaml  
- 供对照的部署侧说明（同源问题不同场景）：`docs/debug/deployment-retrospective-2026-05.md`、`README.md` 中关于 demo 端口与本地访问的说明  

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-05-22 | 解释 esbuild、Vite/pnpm 关系与环境差异；`allowBuilds.esbuild`；补充 npm/yarn 下报错名与「关脚本」类比。 |

