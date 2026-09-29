"""按群解析 Hermes 接入点。

Hermes 把工具集、模型、文件工作区都绑在 profile(一份独立的 HERMES_HOME)上,
所以"这个群能用什么能力"在插件侧唯一的表达方式就是"这个群连哪个接入点"。

一个 base_url 字段同时覆盖两种部署形态:
  - 多路复用:同一个 gateway 用 /p/<profile>/ 前缀服务多个 profile
  - 独立进程:每个 profile 各自监听一个端口
两者对插件而言只是 URL 不同,不需要分支。
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from ..config import HermesEndpoint, plugin_config

_DEFAULT_LABEL = "default"

# 上游 has_usable_secret 的门槛:短于此,profile 侧会判为"未配置"并拒绝请求。
_MIN_KEY_LEN = 16


@dataclass(frozen=True)
class HermesTarget:
    """一次调用要发往的 Hermes 接入点。

    不可变且随用随解析:配置可在运行期被改(测试、热更),缓存会让路由与配置漂移。
    """

    base_url: str
    api_key: str
    timeout: int
    label: str
    """诊断用标签:默认接入点为 "default",其余为路由键 `{adapter}:{group_id}`。"""


def default_target() -> HermesTarget:
    return HermesTarget(
        base_url=plugin_config.hermes_api_url.rstrip("/"),
        api_key=plugin_config.hermes_api_key,
        timeout=plugin_config.hermes_api_timeout,
        label=_DEFAULT_LABEL,
    )


def _from_entry(label: str, entry: HermesEndpoint) -> HermesTarget:
    return HermesTarget(
        base_url=entry.url.rstrip("/"),
        api_key=entry.key or plugin_config.hermes_api_key,
        timeout=entry.timeout or plugin_config.hermes_api_timeout,
        label=label,
    )


def resolve_target(adapter_name: str, is_private: bool, group_id: str | None) -> HermesTarget:
    """解析这一轮该发往哪个接入点。私聊与未配置的群一律走默认接入点。"""
    if is_private or not group_id:
        return default_target()
    label = f"{adapter_name}:{group_id}"
    entry = plugin_config.hermes_group_endpoints.get(label)
    if entry is None:
        return default_target()
    return _from_entry(label, entry)


def all_targets() -> list[HermesTarget]:
    """默认接入点 + 表内全部接入点,供体检类命令逐个探活。"""
    targets = [default_target()]
    for label, entry in plugin_config.hermes_group_endpoints.items():
        targets.append(_from_entry(label, entry))
    return targets


# --- 反向通道:调用方范围 -------------------------------------------------
#
# 出向隔离做完后,MCP 反向通道仍是缺口:一把全局 Bearer 意味着任何接入点的 agent 都能
# 往任意群推消息、读任意群历史。这里不引入第二张 token 表 —— 每个接入点已经有一把必填
# 的 key(命名 profile 必须有自己的 API_SERVER_KEY),呈上哪把就说明是哪个接入点,
# 范围随用随从路由表派生。表变了范围立刻跟着变,不会有第二处登记漂移。

_KIND_DEV = "dev"
_KIND_COMPLEMENT = "complement"
_KIND_LISTED = "listed"


@dataclass(frozen=True)
class CallerScope:
    """一个反向通道调用方能操作的群范围。

    三种形态,没有"不受限"这一档:
      - listed:     某些条目共用的 key → 那些条目的群
      - complement: 全局 key(= 默认接入点)→ 不在表内、或条目没有自己 key 的群
      - dev:        全局 key 与条目 key 都没配,维持"未配置即不校验"的开发模式

    刻意不留 master token:全局 key 的值就是默认 profile 自己的 API_SERVER_KEY,
    留一把能推任意群的钥匙等于给它发全权通行证,而这正是本层要消掉的能力。
    """

    kind: str
    labels: frozenset[str]
    endpoint_url: str = ""

    @classmethod
    def dev(cls) -> CallerScope:
        """开发模式(未配置任何 key)。直调 impl 的单测也用它。"""
        return cls(kind=_KIND_DEV, labels=frozenset())

    def allows(self, adapter: str, group_id: str) -> bool:
        if self.kind == _KIND_DEV:
            return True
        label = f"{adapter}:{group_id}"
        if self.kind == _KIND_LISTED:
            return label in self.labels
        return label not in isolated_labels()

    def describe(self) -> str:
        """给**日志**用的自述。**不含 token 本身。**

        含别的群的标签(补集要列出被排除的),所以只能进 bot 侧日志,不能回给调用方 ——
        回给调用方的版本见 describe_for_caller()。
        """
        if self.kind == _KIND_DEV:
            return "dev-mode(未配置任何 key)"
        if self.kind == _KIND_LISTED:
            return f"endpoint={self.endpoint_url} groups={sorted(self.labels)}"
        return f"endpoint={_DEFAULT_LABEL} groups=补集(排除 {sorted(isolated_labels())})"

    def describe_for_caller(self) -> str:
        """回给 MCP 调用方的范围说明:只说它自己的范围,不泄露别的群。

        listed 那档报的是调用方名下的群(本来就是它自己的);complement 那档**不能**列出被
        排除的标签(那是别的群的路由配置),改用一句话描述。
        """
        if self.kind == _KIND_DEV:
            return "all groups (no API key configured on the bridge)"
        if self.kind == _KIND_LISTED:
            return "this endpoint covers: " + ", ".join(sorted(self.labels))
        return "this token only covers groups that are NOT routed to a dedicated endpoint"


def isolated_labels() -> frozenset[str]:
    """有自己 key、因而拥有独立反向权限的条目。补集判定要排除它们。"""
    return frozenset(label for label, entry in plugin_config.hermes_group_endpoints.items() if entry.key)


def _parse_bearer(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None
    parts = authorization_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    # 容忍尾部空白:客户端偶有意外空格。
    return parts[1].strip() or None


def resolve_caller_scope(authorization_header: str | None) -> CallerScope | None:
    """认出反向通道调用方并给出它的范围。

    返回 None = 认不出,调用方必须拒(HTTP 层 401 / 工具层拒绝)。**不得回落成"不限"。**
    """
    global_key = plugin_config.hermes_api_key
    keyed = {label: entry for label, entry in plugin_config.hermes_group_endpoints.items() if entry.key}
    if not global_key and not keyed:
        return CallerScope.dev()

    token = _parse_bearer(authorization_header)
    if token is None:
        return None
    if global_key and hmac.compare_digest(token, global_key):
        return CallerScope(kind=_KIND_COMPLEMENT, labels=frozenset())
    # 逐个 compare_digest 而不是 dict 查表:命中与否不该由比较耗时泄露。
    hits = {label for label, entry in keyed.items() if hmac.compare_digest(token, entry.key)}
    if hits:
        urls = sorted({keyed[label].url.rstrip("/") for label in hits})
        return CallerScope(kind=_KIND_LISTED, labels=frozenset(hits), endpoint_url=urls[0])
    return None


def _normalize_adapter(raw: str) -> str:
    """与 utils.get_adapter_name 的归一化保持一致(测试钉住这个等价关系)。

    这里不 import utils:那会把 alconna 拉进 core 层。
    """
    return raw.lower().replace(" ", "").replace(".", "") or "unknown"


def validate_endpoints() -> list[str]:
    """返回**可核实的**配置错误(启动期 WARN 用)。只描述不修正,也不抛。

    这里的每条都是插件能从自己这侧确证的错(路由键匹配不上、url 非 http、命名 profile 没配
    key、同 URL 多把 key……),报出来必然是真问题,配 WARNING 级。无法核实、在正确配置上也
    会触发的多路复用反向通道提醒不在此列 —— 见 multiplex_reverse_channel_notices()。
    """
    problems: list[str] = []
    default_url = plugin_config.hermes_api_url.rstrip("/")
    entries = plugin_config.hermes_group_endpoints

    for label, entry in entries.items():
        adapter_part = label.split(":", 1)[0]
        if ":" not in label:
            problems.append(f"路由键 {label!r} 不是 '{{adapter}}:{{group_id}}' 形式,永远匹配不上")
        elif adapter_part != _normalize_adapter(adapter_part):
            problems.append(
                f"路由键 {label!r} 的 adapter 段未归一化(应为 {_normalize_adapter(adapter_part)!r}:"
                f"小写、去空格与点),永远匹配不上"
            )

        url = entry.url.strip()
        if not url.startswith(("http://", "https://")):
            problems.append(f"{label} 的 url {url!r} 不是 http(s) 地址")
            continue

        if url.rstrip("/") != default_url and not entry.key:
            problems.append(
                f"{label} 指向与默认接入点不同的地址却没有自己的 key:命名 profile 校验的是它自己的 "
                f"API_SERVER_KEY(请求会 401),而且该群在反向通道里也拿不到独立权限(落回默认接入点的补集)"
            )
        elif entry.key and len(entry.key) < _MIN_KEY_LEN:
            problems.append(f"{label} 的 key 短于 {_MIN_KEY_LEN} 字符,上游会判为未配置并拒绝请求")

    # 同一个接入点配了多把不同的 key —— 其中至少一把必然 401。
    keys_by_url: dict[str, set[str]] = {}
    for entry in entries.values():
        if entry.key:
            keys_by_url.setdefault(entry.url.strip().rstrip("/"), set()).add(entry.key)
    for url, keys in keys_by_url.items():
        if len(keys) > 1:
            problems.append(f"接入点 {url} 在表内配了 {len(keys)} 把不同的 key,其中至少一把会 401")

    return problems


def multiplex_reverse_channel_notices() -> list[str]:
    """多路复用 + 反向通道下**无法自动核实**的配置提醒(启动期 INFO 用)。

    上游按 profile 隔离 MCP:连接与凭证各读各自的 config.yaml,工具注册进各自的 registry
    overlay,而连接去重是**进程级按 server 名字**。所以每个 profile 要在自己的 config.yaml 里
    声明一条独占名字的 server(Bearer 用它自己那把 key),默认 profile 不能代持 —— 代持会先占住
    名字,被代持的 profile 一个 MCP 工具都拿不到。

    但插件无法从自己这侧核实对面到底怎么配:这条在**配对了的正确部署上也必然触发**,所以它是
    INFO 级提醒而非 WARNING —— 一条在每次正确启动都响的告警只会制造告警疲劳。真配错时的失败
    信号在别处:push 那一刻会有精确的 `拒绝越权` WARNING(fail-closed),那才是要盯的。
    """
    if not plugin_config.hermes_mcp_enabled:
        return []
    entries = plugin_config.hermes_group_endpoints
    multiplexed = sorted(label for label, entry in entries.items() if "/p/" in entry.url)
    if not multiplexed:
        return []
    return [
        f"{multiplexed}:多路复用 + 反向通道,每个 profile 需在自己的 config.yaml 里声明 MCP server(名字全进程唯一),已配好可忽略"
    ]
