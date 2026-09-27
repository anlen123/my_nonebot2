import re
import sqlite3
import traceback
from pathlib import Path

import emoji
import httpx
from nonebot.log import logger

_DB_FILE = Path(__file__).parent / "emojimix.db"
_BASE_URL = "https://www.gstatic.com/android/keyboard/emojikitchen/"


class EmojiMixError(Exception):
    """emoji 合成相关错误的基类。"""


class UnsupportedEmojiError(EmojiMixError):
    """不支持的 emoji。"""

    def __init__(self, emoji_char: str) -> None:
        self.emoji = emoji_char
        super().__init__(f"不支持的emoji：{emoji_char}")


class ComboNotFoundError(EmojiMixError):
    """组合不存在。"""


class DownloadError(EmojiMixError):
    """图片下载失败。"""


class EmojiMixService:
    """Emoji Mix 核心服务，负责查询组合数据并获取合成图片。

    使用 emojimix.db 中的 SQLite 数据库存储组合映射，
    启动时仅加载 emoji 编码映射 (613 条) 用于正则构建，
    组合查询按需从数据库读取，不占用内存。
    """

    def __init__(self) -> None:
        self._emoji_map: dict[int, str] = {}  # base_codepoint -> full_code_string
        self._db: sqlite3.Connection = sqlite3.connect(
            f"file:{_DB_FILE}?mode=ro", uri=True
        )
        self._load_emoji_map()
        self._build_patterns()

    def _load_emoji_map(self) -> None:
        """从数据库中提取所有支持的 emoji 编码，构建码点映射。"""
        rows = self._db.execute(
            "SELECT DISTINCT code FROM ("
            "  SELECT code1 AS code FROM combos"
            "  UNION"
            "  SELECT code2 AS code FROM combos"
            ")"
        ).fetchall()

        for (code,) in rows:
            hex_parts = code.split("-")
            base_cp = int(hex_parts[0][1:], 16)  # 跳过 'u' 前缀
            self._emoji_map[base_cp] = code

        logger.info(
            f"EmojiMix: 加载了 {len(self._emoji_map)} 个 emoji, "
            f"组合数据从 SQLite 按需查询"
        )

    def _build_patterns(self) -> None:
        """构建 emoji 匹配正则表达式。

        从 emoji 库中筛选支持合成的 emoji 字符，
        按长度降序排序确保带 FE0F 的版本优先匹配。
        """
        supported = set(self._emoji_map.keys())
        emojis_list = sorted(
            (e for e in emoji.EMOJI_DATA if len(e) <= 2 and ord(e[0]) in supported),
            key=len,
            reverse=True,
        )
        emoji_pat = "(" + "|".join(re.escape(e) for e in emojis_list) + ")"

        self._explicit_pattern = re.compile(
            rf"^\s*(?P<code1>{emoji_pat})\s*\+\s*(?P<code2>{emoji_pat})\s*$"
        )
        self._auto_pattern = re.compile(
            rf"(?P<code1>{emoji_pat})\s*(?P<code2>{emoji_pat})"
        )

    def _char_to_code(self, emoji_char: str) -> str | None:
        """将 emoji 字符转换为数据中使用的编码格式。

        无论用户输入是否带 FE0F 变体选择符，
        都通过基础码点映射到数据库中的完整编码。
        """
        base_cp = ord(emoji_char[0])
        return self._emoji_map.get(base_cp)

    @property
    def supported_codepoints(self) -> set[int]:
        """返回所有支持的 emoji 基础码点集合。"""
        return set(self._emoji_map.keys())

    @property
    def explicit_pattern(self) -> re.Pattern[str]:
        """显式合成模式正则: emoji1 + emoji2"""
        return self._explicit_pattern

    @property
    def auto_pattern(self) -> re.Pattern[str]:
        """自动合成模式正则: 两个相邻 emoji"""
        return self._auto_pattern

    def get_combo_url(self, emoji1: str, emoji2: str) -> str | None:
        """查找两个 emoji 的组合图片 URL。

        从 SQLite 数据库中查询，会尝试两种排列顺序，
        返回第一个匹配的 URL，如果没有匹配则返回 None。
        """
        code1 = self._char_to_code(emoji1)
        code2 = self._char_to_code(emoji2)
        if not code1 or not code2:
            return None

        row = self._db.execute(
            "SELECT date, code1, code2 FROM combos "
            "WHERE (code1=? AND code2=?) OR (code1=? AND code2=?) LIMIT 1",
            (code1, code2, code2, code1),
        ).fetchone()

        if row:
            date, c1, c2 = row
            return f"{_BASE_URL}{date}/{c1}/{c1}_{c2}.png"

        return None

    async def mix_emoji(self, emoji1: str, emoji2: str) -> bytes:
        """合成两个 emoji，返回图片二进制数据。

        Raises:
            UnsupportedEmojiError: emoji 不在支持列表中
            ComboNotFoundError: 组合不存在
            DownloadError: 图片下载失败
        """
        code1 = self._char_to_code(emoji1)
        if not code1:
            raise UnsupportedEmojiError(emoji1)
        code2 = self._char_to_code(emoji2)
        if not code2:
            raise UnsupportedEmojiError(emoji2)

        row = self._db.execute(
            "SELECT date, code1, code2 FROM combos "
            "WHERE (code1=? AND code2=?) OR (code1=? AND code2=?) LIMIT 1",
            (code1, code2, code2, code1),
        ).fetchone()

        if not row:
            raise ComboNotFoundError

        date, c1, c2 = row
        url = f"{_BASE_URL}{date}/{c1}/{c1}_{c2}.png"

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.content
                raise ComboNotFoundError
        except ComboNotFoundError:
            raise
        except Exception as e:
            logger.warning(traceback.format_exc())
            raise DownloadError from e


# 全局单例
emoji_mix_service = EmojiMixService()
