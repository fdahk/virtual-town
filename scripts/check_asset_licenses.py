#!/usr/bin/env python3
"""
资源 manifest license 校验脚本（阶段 18.4）。

目标：CI 阶段阻止未登记资源进入 ``frontend/public/assets/``。

规则：
1. 扫描 ``frontend/public/assets/`` 下所有二进制资源（排除 ``manifest/`` 目录自身）。
2. 解析 ``frontend/public/assets/manifest/*.manifest.json``，收集所有"被登记的路径"。
3. 对比：
   - 若磁盘上存在文件但 manifest 未登记 → 警告；``--strict`` 时视为失败。
   - 若 manifest 登记的路径磁盘不存在 → 失败。
4. 每个 manifest 必须至少包含 ``license`` 字段（顶层或每个子条目上）。

退出码：
- 0：通过
- 1：发现问题

用法：

.. code-block:: bash

   python scripts/check_asset_licenses.py                # 宽松模式
   python scripts/check_asset_licenses.py --strict       # CI 默认
   python scripts/check_asset_licenses.py --root frontend/public/assets
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCAN_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".wav",
    ".mp3",
    ".ogg",
    ".json",
    ".tmj",
}


def _collect_files(root: Path) -> set[str]:
    files: set[str] = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _SCAN_EXTS:
            continue
        # 排除 manifest 自己
        rel_parts = p.relative_to(root).parts
        if rel_parts and rel_parts[0] == "manifest":
            continue
        files.add(str(p.relative_to(root)).replace("\\", "/"))
    return files


def _walk_for_paths(node: Any, out: set[str]) -> None:
    """递归收集 manifest 字典里所有看起来像资源路径的字符串。"""
    if isinstance(node, str):
        if node.startswith("/assets/"):
            out.add(node.removeprefix("/assets/"))
        return
    if isinstance(node, dict):
        for v in node.values():
            _walk_for_paths(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_for_paths(v, out)


def _has_license(manifest: Any) -> bool:
    """递归检查 manifest 任意层级是否包含非空 license 字段。"""
    if isinstance(manifest, dict):
        if manifest.get("license"):
            return True
        return any(_has_license(v) for v in manifest.values())
    if isinstance(manifest, list):
        return any(_has_license(v) for v in manifest)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Asset license checker")
    parser.add_argument(
        "--root",
        default="frontend/public/assets",
        help="资源根目录（相对仓库根）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="未登记资源也视为失败",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    root = (repo_root / args.root).resolve()
    if not root.exists():
        print(f"[license] asset root not found: {root}")
        return 1

    manifest_dir = root / "manifest"
    if not manifest_dir.exists():
        print(f"[license] manifest directory missing: {manifest_dir}")
        return 1

    on_disk = _collect_files(root)
    in_manifest: set[str] = set()
    errors: list[str] = []

    for mf in sorted(manifest_dir.glob("*.manifest.json")):
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"manifest {mf.name} parse failed: {exc}")
            continue
        if not _has_license(data):
            errors.append(f"manifest {mf.name} missing license field")
        _walk_for_paths(data, in_manifest)

    missing_on_disk = sorted(p for p in in_manifest if p not in on_disk)
    unregistered = sorted(p for p in on_disk if p not in in_manifest)

    if missing_on_disk:
        for p in missing_on_disk:
            errors.append(f"manifest references missing file: {p}")
    if unregistered:
        tag = "ERROR" if args.strict else "WARN"
        for p in unregistered:
            print(f"[license:{tag}] file not registered in manifest: {p}")
            if args.strict:
                errors.append(f"unregistered asset: {p}")

    if errors:
        print("[license] check FAILED")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(
        f"[license] OK: {len(on_disk)} files, "
        f"{len(in_manifest)} registered entries"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
