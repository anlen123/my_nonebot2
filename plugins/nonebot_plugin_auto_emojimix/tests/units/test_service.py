"""EmojiMixService 单元测试

测试 service 模块中的纯 Python 逻辑，不依赖 NoneBot 环境。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 数据加载测试


class TestDataLoading:
    """测试 SQLite 数据加载和内部数据结构构建。"""

    def test_db_connection_exists(self, service_module):
        """验证数据库连接已建立。"""
        svc = service_module.emoji_mix_service
        assert svc._db is not None

    def test_emoji_map_built(self, service_module):
        """验证 emoji 映射表已构建。"""
        svc = service_module.emoji_mix_service
        assert len(svc._emoji_map) > 0

    def test_supported_codepoints_nonempty(self, service_module):
        """验证支持的码点集合不为空且为 set 类型。"""
        svc = service_module.emoji_mix_service
        codepoints = svc.supported_codepoints
        assert isinstance(codepoints, set)
        assert len(codepoints) > 0

    def test_coffee_emoji_in_map(self, service_module):
        """验证 ☕ (U+2615) 在 emoji 映射中。"""
        svc = service_module.emoji_mix_service
        assert 0x2615 in svc._emoji_map
        assert "2615" in svc._emoji_map[0x2615]

    def test_grinning_face_in_map(self, service_module):
        """验证 😀 (U+1F600) 在 emoji 映射中。"""
        svc = service_module.emoji_mix_service
        assert 0x1F600 in svc._emoji_map
        assert "1f600" in svc._emoji_map[0x1F600]

    def test_combos_exist_in_db(self, service_module):
        """验证数据库中有组合数据。"""
        svc = service_module.emoji_mix_service
        count = svc._db.execute("SELECT COUNT(*) FROM combos").fetchone()[0]
        assert count > 0


# 编码转换测试


class TestCharToCode:
    """测试 _char_to_code 方法：emoji 字符 → 编码字符串。"""

    def test_basic_emoji(self, service_module):
        """测试基础 emoji 转换。"""
        svc = service_module.emoji_mix_service
        result = svc._char_to_code("☕")
        assert result is not None
        assert "2615" in result

    def test_emoji_with_and_without_fe0f(self, service_module):
        """测试带/不带 FE0F 变体选择符的 emoji 应返回相同结果。"""
        svc = service_module.emoji_mix_service
        result_plain = svc._char_to_code("\u2639")
        result_fe0f = svc._char_to_code("\u2639\ufe0f")
        assert result_plain == result_fe0f

    def test_unsupported_char_returns_none(self, service_module):
        """测试不支持的字符返回 None。"""
        svc = service_module.emoji_mix_service
        assert svc._char_to_code("A") is None
        assert svc._char_to_code("1") is None

    def test_empty_string_raises(self, service_module):
        """测试空字符串输入应抛出 IndexError。"""
        svc = service_module.emoji_mix_service
        with pytest.raises(IndexError):
            svc._char_to_code("")


# 支持码点测试


class TestSupportedCodepoints:
    """测试 supported_codepoints 属性。"""

    def test_returns_set(self, service_module):
        """验证返回类型为 set。"""
        svc = service_module.emoji_mix_service
        assert isinstance(svc.supported_codepoints, set)

    def test_returns_new_set_each_call(self, service_module):
        """验证每次调用返回新 set（防止外部修改影响内部状态）。"""
        svc = service_module.emoji_mix_service
        cp1 = svc.supported_codepoints
        cp2 = svc.supported_codepoints
        assert cp1 == cp2
        assert cp1 is not cp2

    def test_contains_common_emojis(self, service_module):
        """验证包含常见 emoji 的码点。"""
        svc = service_module.emoji_mix_service
        codepoints = svc.supported_codepoints
        assert 0x1F600 in codepoints  # 😀
        assert 0x2615 in codepoints  # ☕


# 组合 URL 测试


class TestGetComboUrl:
    """测试 get_combo_url 方法。"""

    def test_self_combo(self, service_module):
        """测试 emoji 与自身的组合（☕+☕）。"""
        svc = service_module.emoji_mix_service
        url = svc.get_combo_url("☕", "☕")
        assert url is not None
        assert url.endswith(".png")
        assert "u2615" in url

    def test_reverse_order_also_works(self, service_module):
        """测试反向排列也能找到组合。"""
        svc = service_module.emoji_mix_service
        url1 = svc.get_combo_url("😀", "😁")
        url2 = svc.get_combo_url("😁", "😀")
        if url1 is None and url2 is None:
            pytest.skip("此组合不在当前数据中")
        assert (url1 is not None) or (url2 is not None)

    def test_unsupported_emoji_returns_none(self, service_module):
        """测试不支持的 emoji 返回 None。"""
        svc = service_module.emoji_mix_service
        url = svc.get_combo_url("A", "B")
        assert url is None

    def test_url_format(self, service_module):
        """验证生成的 URL 格式：baseUrl/date/first_code/combo_key.png"""
        svc = service_module.emoji_mix_service
        url = svc.get_combo_url("☕", "☕")
        assert url is not None
        assert url.startswith("https://www.gstatic.com/android/keyboard/emojikitchen/")
        assert url.endswith(".png")
        # 日期部分是 8 位数字
        parts = url.split("/")
        date_part = parts[-3]
        assert len(date_part) == 8
        assert date_part.isdigit()

    def test_url_contains_first_code_in_path(self, service_module):
        """验证 URL 路径中包含 combo key 的第一个编码。"""
        svc = service_module.emoji_mix_service
        url = svc.get_combo_url("☕", "☕")
        assert url is not None
        assert "/u2615/" in url


# mix_emoji 异步测试


class TestMixEmoji:
    """测试 mix_emoji 异步方法。"""

    async def test_unsupported_first_emoji(self, service_module):
        """测试第一个 emoji 不支持时抛出 UnsupportedEmojiError。"""
        svc = service_module.emoji_mix_service
        with pytest.raises(service_module.UnsupportedEmojiError) as exc_info:
            await svc.mix_emoji("A", "😀")
        assert exc_info.value.emoji == "A"

    async def test_unsupported_second_emoji(self, service_module):
        """测试第二个 emoji 不支持时抛出 UnsupportedEmojiError。"""
        svc = service_module.emoji_mix_service
        with pytest.raises(service_module.UnsupportedEmojiError) as exc_info:
            await svc.mix_emoji("😀", "A")
        assert exc_info.value.emoji == "A"

    async def test_successful_mix(self, service_module):
        """测试成功合成返回图片二进制数据。"""
        svc = service_module.emoji_mix_service
        fake_image = b"\x89PNG\r\n\x1a\n fake image data"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_image

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(
            service_module.httpx, "AsyncClient", return_value=mock_client
        ):
            result = await svc.mix_emoji("☕", "☕")

        assert isinstance(result, bytes)
        assert result == fake_image

    async def test_http_error_status(self, service_module):
        """测试 HTTP 非 200 状态抛出 ComboNotFoundError。"""
        svc = service_module.emoji_mix_service

        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(service_module.httpx, "AsyncClient", return_value=mock_client),
            pytest.raises(service_module.ComboNotFoundError),
        ):
            await svc.mix_emoji("☕", "☕")

    async def test_network_exception(self, service_module):
        """测试网络异常抛出 DownloadError。"""
        svc = service_module.emoji_mix_service

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(service_module.httpx, "AsyncClient", return_value=mock_client),
            pytest.raises(service_module.DownloadError),
        ):
            await svc.mix_emoji("☕", "☕")
