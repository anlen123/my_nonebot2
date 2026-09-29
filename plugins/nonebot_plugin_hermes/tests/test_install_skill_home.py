"""hermes-install-skill 的目标目录解析。

多 profile 部署下每个 profile 是一份独立的 HERMES_HOME,skill 要按 profile 分别装。
这个 CLI 必须认 HERMES_HOME —— 不认就会把 skill 静默装进默认 profile,而"装好了"的
输出看起来完全正常(兄弟脚本 hermes_repair_sessions.py 一直是认的)。
"""

from __future__ import annotations

import sys

import hermes_install_skill


def test_default_dest_follows_hermes_home(monkeypatch, tmp_path):
    home = tmp_path / "profiles" / "team-a"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    assert hermes_install_skill.default_dest() == home / "skills" / "nonebot-bridge"


def test_default_dest_falls_back_to_user_home(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(hermes_install_skill.Path, "home", staticmethod(lambda: tmp_path))

    assert hermes_install_skill.default_dest() == tmp_path / ".hermes" / "skills" / "nonebot-bridge"


def test_blank_hermes_home_is_ignored(monkeypatch, tmp_path):
    """空串 / 纯空白与未设置同义(与上游 _hermes_home_from_env 同口径)。"""
    monkeypatch.setenv("HERMES_HOME", "   ")
    monkeypatch.setattr(hermes_install_skill.Path, "home", staticmethod(lambda: tmp_path))

    assert hermes_install_skill.default_dest() == tmp_path / ".hermes" / "skills" / "nonebot-bridge"


def test_main_installs_into_hermes_home(monkeypatch, tmp_path, capsys):
    home = tmp_path / "profiles" / "team-a"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["hermes-install-skill"])

    assert hermes_install_skill.main() == 0
    installed = home / "skills" / "nonebot-bridge" / "SKILL.md"
    assert installed.exists()
    # 提示里的 config.yaml 路径也要是这个 profile 的,不能永远印 ~/.hermes
    assert str(home / "config.yaml") in capsys.readouterr().out


def test_explicit_dest_beats_hermes_home(monkeypatch, tmp_path):
    home = tmp_path / "profiles" / "team-a"
    home.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["hermes-install-skill", "--dest", str(elsewhere)])

    assert hermes_install_skill.main() == 0
    assert (elsewhere / "SKILL.md").exists()
    assert not (home / "skills").exists()


def test_missing_hermes_home_dir_is_flagged(monkeypatch, tmp_path, capsys):
    """HERMES_HOME 指向不存在的目录 = 大概率打错了,不能只闷头 mkdir -p。"""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "typo"))
    monkeypatch.setattr(sys, "argv", ["hermes-install-skill"])

    assert hermes_install_skill.main() == 0
    assert "HERMES_HOME" in capsys.readouterr().err
