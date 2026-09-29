"""
会话管理

只维护 Hermes session key 映射;消息历史已移交 MessageBuffer。
"""

from __future__ import annotations

import time

from nonebot import logger

from ..config import plugin_config
from .storage.session_key_store import SessionKeyStore

# 上游对 X-Hermes-Session-Key 的长度上限:超出直接 400,整轮对话被打回。
# 模板是用户可配的,所以渲染结果必须自己先量一遍。
_MAX_MEMORY_KEY_LEN = 256


class SessionManager:
    """管理 Hermes session key 的生成、采纳与过期"""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._generation: dict[str, int] = {}
        self._store: SessionKeyStore | None = None

    def bind_store(self, store: SessionKeyStore) -> None:
        """绑定持久化存储并载入已有映射(启动时调一次)。

        未绑定时整个管理器退化成纯内存,行为不变——但那样重启会退回派生 key,
        既复活被 /clear 掉的会话,也会把已被上游压缩关闭的父会话重新钉上。
        """
        self._store = store
        for internal_id, (session_key, generation) in store.load_all().items():
            self._cache[internal_id] = session_key
            self._generation[internal_id] = generation
        if self._cache:
            logger.debug(f"[SESSION] 载入 {len(self._cache)} 条持久化 session key 映射")

    def _persist(self, internal_id: str) -> None:
        if self._store is None:
            return
        self._store.put(
            internal_id,
            self._cache[internal_id],
            self._generation.get(internal_id, 0),
            now=time.time(),
        )

    def _get_internal_id(
        self,
        adapter_name: str,
        is_private: bool,
        user_id: str,
        group_id: str | None = None,
    ) -> str:
        """根据配置生成统一的内部会话 ID。

        注:adapter_name / user_id / group_id 假定不含 '+';真实 adapter 名经
        get_adapter_name() 规整后均为 [a-z0-9],平台 user_id 多为数字串。
        """
        if is_private:
            return f"{adapter_name}+private+{user_id}"
        elif plugin_config.hermes_session_share_group and group_id:
            return f"{adapter_name}+group+{group_id}"
        else:
            return f"{adapter_name}+group+{group_id or 'unknown'}+{user_id}"

    def get_session_key(
        self,
        adapter_name: str,
        is_private: bool,
        user_id: str,
        group_id: str | None = None,
    ) -> str:
        """获取或创建 Hermes session key,通过 X-Hermes-Session-Id 头送给上游。"""
        internal_id = self._get_internal_id(adapter_name, is_private, user_id, group_id)
        cached = self._cache.get(internal_id)
        if cached is not None:
            return cached

        gen = self._generation.get(internal_id, 0)
        session_key = f"hermes-{internal_id}"
        if gen > 0:
            session_key = f"{session_key}-g{gen}"

        self._cache[internal_id] = session_key
        self._persist(internal_id)
        logger.debug(f"[SESSION] 新建会话: {internal_id} -> {session_key}")
        return session_key

    def get_memory_key(
        self,
        adapter_name: str,
        is_private: bool,
        user_id: str,
        group_id: str | None = None,
    ) -> str | None:
        """长期记忆作用域 key,通过 X-Hermes-Session-Key 送给上游。

        与 get_session_key 的三点关键区别:
          - 不缓存、不持久化:纯模板渲染,无状态
          - 不受 clear_session 影响:generation 不参与,/clear 只重置 transcript
          - 不受上游轮换影响:压缩换掉的是 transcript id,记忆挂在这个稳定 key 上

        上游 memory provider 把这个值当作记忆作用域名。不配它的话作用域由上游
        strategy 兜底,典型后果是所有会话共写一份,或随 transcript 轮换而重置。

        返回 None 表示本轮不发该头(功能关闭,或模板渲染失败)。
        """
        if not plugin_config.hermes_honcho_enabled:
            return None

        try:
            if is_private:
                key = plugin_config.hermes_private_session_key_format.format(
                    adapter=adapter_name,
                    user_id=user_id,
                )
            else:
                gid = group_id or "unknown"
                if plugin_config.hermes_group_sessions_per_user:
                    key = plugin_config.hermes_group_per_user_session_key_format.format(
                        adapter=adapter_name,
                        group_id=gid,
                        user_id=user_id,
                    )
                else:
                    key = plugin_config.hermes_group_session_key_format.format(
                        adapter=adapter_name,
                        group_id=gid,
                    )
        except (KeyError, IndexError, ValueError) as exc:
            # 模板是用户可改的配置,写错只应该让"记不住",不该让整轮对话失败。
            logger.error(f"[SESSION] 记忆 key 模板渲染失败({type(exc).__name__}: {exc}),本轮不发 X-Hermes-Session-Key")
            return None

        # 超长同理:发出去会被上游 400,该作用域下每一轮都失败。截断不行——两个不同
        # 作用域可能截成同一个名字,记忆会串到一起去,那比记不住更糟。
        if len(key) > _MAX_MEMORY_KEY_LEN:
            logger.error(
                f"[SESSION] 记忆 key 渲染后长度 {len(key)} 超过上游上限 {_MAX_MEMORY_KEY_LEN},"
                "本轮不发 X-Hermes-Session-Key;请缩短 HERMES_*_SESSION_KEY_FORMAT"
            )
            return None
        return key

    def adopt_session_key(self, previous_key: str, new_key: str) -> bool:
        """采纳上游轮换后的 session key,返回是否命中一条映射。

        上游自动压缩上下文时会关闭旧 session 并新建 continuation,新 id 走响应头
        X-Hermes-Session-Id 回传。不采纳就等于每轮都往一个 end_reason='compression'
        的会话里写,上游一律拒收,而且每压缩一次就再分叉一个兄弟会话。

        previous_key 认不出来时(典型是采纳与 /clear 撞车,映射已被换掉)不做任何事:
        凭 key 反推 internal_id 会把刚清空的会话又接回旧血缘。
        """
        if not new_key or new_key == previous_key:
            return False
        for internal_id, current in self._cache.items():
            if current != previous_key:
                continue
            self._cache[internal_id] = new_key
            self._persist(internal_id)
            logger.info(f"[SESSION] 采纳上游轮换: {internal_id} -> {new_key}")
            return True
        logger.debug(f"[SESSION] 上游轮换的 {previous_key} 已不在映射中,忽略")
        return False

    def clear_session(
        self,
        adapter_name: str,
        is_private: bool,
        user_id: str,
        group_id: str | None = None,
    ) -> None:
        """重置会话:递增 generation,使下次 get_session_key 返回新 key,
        Hermes 据此把后续对话当作新会话。"""
        internal_id = self._get_internal_id(adapter_name, is_private, user_id, group_id)
        self._cache.pop(internal_id, None)
        gen = self._generation.get(internal_id, 0) + 1
        self._generation[internal_id] = gen
        # 立刻把新 key 落库:generation 只活在内存的话,重启后 /clear 会失效,
        # 被清掉的会话原地复活。
        self.get_session_key(adapter_name, is_private, user_id, group_id)
        logger.info(f"[SESSION] 会话已重置: {internal_id} (generation={gen})")


def validate_memory_key_templates() -> list[str]:
    """启动期试渲染三个记忆 key 模板,返回问题描述列表(空 = 全部可用)。

    没有这一步,模板写错只在真的有人说话时才暴露,而且是每轮刷一条 error 日志;
    有了它,启动日志里一次性说清哪个模板不可用。只描述不修正,也不抛。
    """
    samples = (
        ("HERMES_GROUP_SESSION_KEY_FORMAT", plugin_config.hermes_group_session_key_format, {"group_id": "sample"}),
        (
            "HERMES_GROUP_PER_USER_SESSION_KEY_FORMAT",
            plugin_config.hermes_group_per_user_session_key_format,
            {"group_id": "sample", "user_id": "sample"},
        ),
        ("HERMES_PRIVATE_SESSION_KEY_FORMAT", plugin_config.hermes_private_session_key_format, {"user_id": "sample"}),
    )
    problems: list[str] = []
    for name, template, fields in samples:
        try:
            rendered = template.format(adapter="sample", **fields)
        except (KeyError, IndexError, ValueError) as exc:
            problems.append(f"{name} 渲染失败({type(exc).__name__}: {exc}),该场景不会发 X-Hermes-Session-Key")
            continue
        if len(rendered) > _MAX_MEMORY_KEY_LEN:
            problems.append(
                f"{name} 渲染后 {len(rendered)} 字符,超过上游上限 {_MAX_MEMORY_KEY_LEN},该场景不会发 X-Hermes-Session-Key"
            )
    return problems


# 全局会话管理器
session_manager = SessionManager()
