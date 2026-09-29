"""Telegram file_id → URL resolve 短期缓存测试。

Regression target:perception (priority=1) + main handler (priority=98)
两个 matcher 对同一 event 各跑一次 _extract_image_urls,如果不缓存就要
hit Telegram getFile API 两次。本测试用 mock bot 验证 60s 窗口内只跑一次。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nonebot_plugin_hermes.handlers.message import (
    _resolve_telegram_file_url,
    _resolved_url_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个测试独立 cache。"""
    _resolved_url_cache.clear()
    yield
    _resolved_url_cache.clear()


def _make_bot(self_id: str = "bot42", token: str = "TOKEN", file_path: str = "photo/abc.jpg") -> MagicMock:
    bot = MagicMock()
    bot.self_id = self_id
    bot.bot_config = MagicMock()
    bot.bot_config.token = token
    file_obj = MagicMock()
    file_obj.file_path = file_path
    bot.get_file = AsyncMock(return_value=file_obj)
    return bot


@pytest.mark.asyncio
async def test_resolve_returns_correct_url():
    bot = _make_bot(self_id="bot1", token="TKN", file_path="photo/x.jpg")
    url = await _resolve_telegram_file_url(bot, "file_id_1")
    assert url == "https://api.telegram.org/file/botTKN/photo/x.jpg"
    bot.get_file.assert_called_once_with(file_id="file_id_1")


@pytest.mark.asyncio
async def test_resolve_caches_within_ttl_same_bot_same_file_id():
    """同 (bot, file_id) 连续两次 resolve,Telegram API 只被打一次。"""
    bot = _make_bot()
    url1 = await _resolve_telegram_file_url(bot, "fid_dup")
    url2 = await _resolve_telegram_file_url(bot, "fid_dup")
    assert url1 == url2
    assert bot.get_file.call_count == 1, "缓存未命中,Telegram API 被调用了多次"


@pytest.mark.asyncio
async def test_resolve_does_not_share_across_different_bots():
    """两个不同 bot self_id 即使 file_id 相同也分开 resolve(不同 token)。"""
    bot_a = _make_bot(self_id="bot_a", token="TKN_A", file_path="photo/x.jpg")
    bot_b = _make_bot(self_id="bot_b", token="TKN_B", file_path="photo/x.jpg")
    url_a = await _resolve_telegram_file_url(bot_a, "fid")
    url_b = await _resolve_telegram_file_url(bot_b, "fid")
    assert "TKN_A" in url_a
    assert "TKN_B" in url_b
    assert bot_a.get_file.call_count == 1
    assert bot_b.get_file.call_count == 1


@pytest.mark.asyncio
async def test_resolve_failure_does_not_cache():
    """resolve 失败(API 抛 / 缺 file_path)不应留下缓存条目,否则下次还要继续失败。"""
    bot = MagicMock()
    bot.self_id = "bot42"
    bot.bot_config = MagicMock(token="TKN")
    bot.get_file = AsyncMock(side_effect=Exception("network down"))
    url1 = await _resolve_telegram_file_url(bot, "fid_fail")
    assert url1 is None
    # 第二次应该再试一次 API(失败时不能 sticky 缓存)
    url2 = await _resolve_telegram_file_url(bot, "fid_fail")
    assert url2 is None
    assert bot.get_file.call_count == 2


@pytest.mark.asyncio
async def test_resolve_caches_ttl_expiry(monkeypatch):
    """TTL 过期后应该重新 resolve。"""
    import nonebot_plugin_hermes.handlers.message as mod

    fake_now = [0.0]

    def fake_monotonic():
        return fake_now[0]

    monkeypatch.setattr(mod.time, "monotonic", fake_monotonic)

    bot = _make_bot()
    fake_now[0] = 1000.0
    await _resolve_telegram_file_url(bot, "fid_ttl")
    assert bot.get_file.call_count == 1
    # 推到 TTL 内,仍命中缓存
    fake_now[0] = 1000.0 + mod._RESOLVED_URL_TTL_S - 1
    await _resolve_telegram_file_url(bot, "fid_ttl")
    assert bot.get_file.call_count == 1
    # 推过 TTL,缓存失效,应该重新 resolve
    fake_now[0] = 1000.0 + mod._RESOLVED_URL_TTL_S + 1
    await _resolve_telegram_file_url(bot, "fid_ttl")
    assert bot.get_file.call_count == 2
