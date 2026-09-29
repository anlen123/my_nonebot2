"""把 SKILL.md 复制到 ~/.hermes/skills/nonebot-bridge/。

使用(覆盖已装版本要带 --force,否则报 target exists 并退出):
    uv run hermes-install-skill --force
    或
    uv run python -m hermes_install_skill --force

注:控制台入口点(见 pyproject [project.scripts])指向根模块 hermes_install_skill,
本模块只是同一逻辑的包内副本。**不要**用
`python -m nonebot_plugin_hermes.scripts.install_skill` —— `-m` 会连带导入
nonebot_plugin_hermes/__init__.py,在没有 NoneBot 进程的场景下 require() 直接抛错。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Install nonebot-bridge skill into ~/.hermes/skills/")
    parser.add_argument(
        "--dest",
        default=str(Path.home() / ".hermes" / "skills" / "nonebot-bridge"),
        help="Target directory (default: ~/.hermes/skills/nonebot-bridge)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite if exists")
    args = parser.parse_args()

    dest = Path(args.dest)
    if dest.exists() and not args.force:
        print(f"[install_skill] target exists: {dest}", file=sys.stderr)
        print("[install_skill] re-run with --force to overwrite", file=sys.stderr)
        return 1

    # Locate SKILL.md relative to this file's location on the filesystem.
    # Using __file__ (rather than importlib.resources) avoids importing the parent
    # package's __init__.py, which would trigger NoneBot initialisation and fail
    # when run outside a NoneBot process (e.g. as a standalone console_script).
    src = Path(__file__).parent.parent / "skill" / "SKILL.md"
    if not src.exists():
        print(f"[install_skill] source not found: {src}", file=sys.stderr)
        return 2
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "SKILL.md"
    shutil.copy2(src, target)
    print(f"[install_skill] installed: {target}")
    print("[install_skill] now add to ~/.hermes/config.yaml:")
    print("  mcp_servers:")
    print("    nonebot-bridge:")
    print("      url: http://127.0.0.1:8643/mcp")
    print('      headers: { Authorization: "Bearer <HERMES_API_KEY>" }')
    return 0


if __name__ == "__main__":
    sys.exit(main())
