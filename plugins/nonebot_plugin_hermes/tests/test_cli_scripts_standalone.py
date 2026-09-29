"""顶层 CLI 脚本必须能脱离插件环境运行。

三个工具操作的库分属不同主机:`hermes-purge-media` 动 bot 那台的 messages.db,
`hermes-install-skill` / `hermes-repair-sessions` 动 Hermes 那台的 ~/.hermes。
分机部署时 Hermes 那台通常没装本插件,用法是 git clone 后直接 `python3 <脚本>.py` ——
所以这些模块只能用标准库,且绝不能 import 本包(包的 __init__ 里的 require() 在
没有 NoneBot 进程时直接抛错)。加一条依赖就会让那条路径失效,故用测试钉住。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_CLI_MODULES = [
    "hermes_install_skill.py",
    "hermes_optimize_state.py",
    "hermes_purge_media.py",
    "hermes_repair_sessions.py",
]
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _imported_roots(path: Path) -> set[str]:
    """模块里出现过的所有顶层包名(含函数内的延迟 import)。"""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # 相对 import(level>0)只可能指向本包,单文件跑必炸,一并拦下
            roots.add(node.module.split(".")[0] if node.level == 0 and node.module else ".")
    return roots


@pytest.mark.parametrize("module_name", _CLI_MODULES)
def test_cli_script_imports_only_stdlib(module_name: str):
    path = _REPO_ROOT / module_name
    assert path.exists(), f"{module_name} 不在仓库根目录"

    non_stdlib = sorted(r for r in _imported_roots(path) if r not in sys.stdlib_module_names)

    assert not non_stdlib, (
        f"{module_name} 引入了非标准库依赖 {non_stdlib};"
        "它必须能在没装本插件的 Hermes 主机上用 `python3 <脚本>.py` 直接跑"
    )


@pytest.mark.parametrize("module_name", _CLI_MODULES)
def test_cli_script_is_registered_as_console_script(module_name: str):
    """同机部署要能直接敲命令 —— [project.scripts] 与 py-modules 必须同时登记。"""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    stem = module_name.removesuffix(".py")

    assert f'"{stem}:main"' in pyproject, f"{stem} 未登记到 [project.scripts]"
    assert f'"{stem}"' in pyproject.split("py-modules")[1], f"{stem} 未登记到 py-modules,打包会漏掉"
