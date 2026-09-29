"""storage_vacuum cron 注册测试。

Regression: 上一版用了 `@scheduler.scheduled_job(..., replace_existing=True)`,
但 apscheduler `scheduled_job` 装饰器内部已经 hard-coded
`replace_existing=True` 作为 positional 传给 add_job(base.py:560),再传
kwarg 会撞 `TypeError: BaseScheduler.add_job() got multiple values for
argument 'replace_existing'`,bot 启动直接挂。本测试确保下次有人手抖加
回这个 kwarg 时单测先红。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_register_storage_vacuum_does_not_pass_replace_existing_kwarg():
    """注册函数应能无异常完成;模拟 scheduler.scheduled_job 验证不传
    replace_existing kwarg(那是 apscheduler 装饰器自己保留的位置)。"""
    from nonebot_plugin_hermes.tasks.storage_vacuum import register_storage_vacuum

    fake_decorator_calls = []

    def fake_scheduled_job(*args, **kwargs):
        fake_decorator_calls.append((args, kwargs))

        def _decorator(fn):
            return fn

        return _decorator

    fake_scheduler = MagicMock()
    fake_scheduler.scheduled_job = fake_scheduled_job

    with patch("nonebot_plugin_hermes.tasks.storage_vacuum.scheduler", fake_scheduler):
        register_storage_vacuum()

    assert len(fake_decorator_calls) == 1
    _args, kwargs = fake_decorator_calls[0]
    assert "replace_existing" not in kwargs, (
        "@scheduled_job 装饰器内部 hard-coded replace_existing=True,这里再传一遍"
        "会让 apscheduler add_job 抛 'got multiple values for argument'。"
    )


@pytest.mark.asyncio
async def test_vacuum_body_no_ops_when_singletons_unset(monkeypatch):
    """vacuum job body 在 _mcp 单例未就绪时应早返回,不抛异常。"""
    from nonebot_plugin_hermes import mcp as _mcp
    from nonebot_plugin_hermes.tasks.storage_vacuum import register_storage_vacuum

    captured = {}

    def fake_scheduled_job(*args, **kwargs):
        def _decorator(fn):
            captured["fn"] = fn
            return fn

        return _decorator

    fake_scheduler = MagicMock()
    fake_scheduler.scheduled_job = fake_scheduled_job

    with patch("nonebot_plugin_hermes.tasks.storage_vacuum.scheduler", fake_scheduler):
        register_storage_vacuum()

    monkeypatch.setattr(_mcp, "message_store", None)
    monkeypatch.setattr(_mcp, "image_cache", None)
    # 不抛即通过
    await captured["fn"]()
