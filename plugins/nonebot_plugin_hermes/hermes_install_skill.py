"""把 SKILL.md 复制到 <HERMES_HOME>/skills/nonebot-bridge/。

使用:
    uv run hermes-install-skill
    或
    python -m hermes_install_skill

多 profile 部署(gateway.multiplex_profiles / 多进程)下每个 profile 是一份独立的
HERMES_HOME,skill 要按 profile 分别装:

    HERMES_HOME=~/.hermes/profiles/team-a hermes-install-skill

此模块是独立入口点,故意不从 nonebot_plugin_hermes 导入任何内容,
以避免在 NoneBot 未初始化时(控制台工具场景)触发插件初始化。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

_SKILL_SUBDIR = ("skills", "nonebot-bridge")


def hermes_home() -> Path:
    """解析本次要写入的 HERMES_HOME。

    只认 HERMES_HOME 环境变量,与上游 hermes_constants._hermes_home_from_env() 同口径
    (空串 / 纯空白视作未设置)。不认 HERMES_PROFILE:上游的 env 解析也不认它,
    这里跟着上游走,免得两边对"当前是哪个 profile"给出不同答案。
    """
    val = os.environ.get("HERMES_HOME", "").strip()
    return Path(val) if val else Path.home() / ".hermes"


def default_dest() -> Path:
    """默认安装目录。随 HERMES_HOME 走,所以按 profile 装 skill 不需要额外参数。"""
    return hermes_home().joinpath(*_SKILL_SUBDIR)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install nonebot-bridge skill into <HERMES_HOME>/skills/ (HERMES_HOME defaults to ~/.hermes)"
    )
    parser.add_argument(
        "--dest",
        default=None,
        help="Target directory (default: <HERMES_HOME>/skills/nonebot-bridge)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite if exists")
    args = parser.parse_args()

    home = hermes_home()
    # HERMES_HOME 指向不存在的目录基本只有一种原因:打错了 / profile 还没建。
    # 闷头 mkdir -p 会造出一份永远不会被 Hermes 读到的 skills 目录,所以要出声。
    if args.dest is None and not home.is_dir():
        print(f"[install_skill] ⚠ HERMES_HOME 指向的目录不存在: {home}", file=sys.stderr)
        print("[install_skill] ⚠ 先 hermes profile create <name>,或检查路径拼写", file=sys.stderr)

    dest = Path(args.dest) if args.dest else default_dest()
    if dest.exists() and not args.force:
        print(f"[install_skill] target exists: {dest}", file=sys.stderr)
        print("[install_skill] re-run with --force to overwrite", file=sys.stderr)
        return 1

    # Locate SKILL.md relative to this file.
    # In both editable installs (project root) and wheel installs (site-packages),
    # nonebot_plugin_hermes/ sits alongside this file, so the path is always valid.
    src = Path(__file__).parent / "nonebot_plugin_hermes" / "skill" / "SKILL.md"
    if not src.exists():
        print(f"[install_skill] source not found: {src}", file=sys.stderr)
        return 2
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "SKILL.md"
    shutil.copy2(src, target)
    print(f"[install_skill] installed: {target}")
    print(f"[install_skill] now add to {home / 'config.yaml'}:")
    print("  mcp_servers:")
    print("    nonebot-bridge:")
    print("      url: http://127.0.0.1:8643/mcp")
    print('      headers: { Authorization: "Bearer <该 profile 自己的 API_SERVER_KEY>" }')
    return 0


if __name__ == "__main__":
    sys.exit(main())
