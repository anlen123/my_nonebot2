"""bilibili_live 专用网络层：绕开系统 DNS 直连 B 站，并对偶发失败自动重试。

为什么需要它：
  * 这台机器的 DNS 被本地代理接管，``api.live.bilibili.com`` 之类会被解析成
    198.18.x.x 的 fake-ip，流量随之走代理出口，实测会出现整段时间连不上；
  * 用 DoH（阿里 223.5.5.5）自己解析出真实 IP 再连接，同一个时间窗里成功率更高
    （实测 4/4 vs 3/4），而且不依赖系统 DNS 是否正常；
  * 解析失败时自动退回系统 DNS，绝不因为 DoH 挂了就整个插件不可用。

用法：
    from .direct_net import new_direct_session
    async with new_direct_session(headers=HEADERS) as session:
        ...
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from typing import Any, Optional

import aiohttp

DOH_ENDPOINT = "https://223.5.5.5/resolve?name={host}&type=A"
DOH_TIMEOUT = 8          # 单次 DoH 查询超时（秒）
DOH_ATTEMPTS = 2         # DoH 查询重试次数
DNS_CACHE_TTL = 600      # 解析结果缓存（秒），B 站 IP 很少变
HTTP_ATTEMPTS = 3        # 接口请求重试次数
RETRY_BACKOFF = (0.8, 1.6)   # 每次重试前的等待（秒）


class _DirectResolver(aiohttp.abc.AbstractResolver):
    """先问 DoH 拿真实 IP，失败再退回系统 DNS。"""

    def __init__(self) -> None:
        self._fallback = aiohttp.resolver.ThreadedResolver()
        self._cache: dict[str, tuple[list[str], float]] = {}
        self._lock = asyncio.Lock()

    async def _doh_lookup(self, host: str) -> list[str]:
        """向 DoH 要 A 记录；问不到就返回空列表。"""
        headers = {"accept": "application/dns-json"}
        for attempt in range(DOH_ATTEMPTS):
            try:
                async with aiohttp.ClientSession(trust_env=False) as session:
                    async with session.get(
                        DOH_ENDPOINT.format(host=host),
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=DOH_TIMEOUT),
                    ) as response:
                        payload = await response.json(content_type=None)
                ips = [
                    item["data"]
                    for item in payload.get("Answer") or []
                    if item.get("type") == 1 and item.get("data")
                ]
                if ips:
                    return ips
            except Exception:  # noqa: BLE001 — DoH 失败不是错误，退回系统 DNS 就行
                pass
            if attempt + 1 < DOH_ATTEMPTS:
                await asyncio.sleep(0.6)
        return []

    async def _real_ips(self, host: str) -> list[str]:
        cached = self._cache.get(host)
        if cached and time.time() - cached[1] < DNS_CACHE_TTL:
            return cached[0]
        async with self._lock:
            cached = self._cache.get(host)
            if cached and time.time() - cached[1] < DNS_CACHE_TTL:
                return cached[0]
            ips = await self._doh_lookup(host)
            if ips:
                self._cache[host] = (ips, time.time())
            return ips

    async def resolve(self, host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        try:
            ipaddress.ip_address(host)
            is_literal = True
        except ValueError:
            is_literal = host in ("localhost",) or host.endswith(".local")
        if not is_literal:
            ips = await self._real_ips(host)
            if ips:
                return [
                    {"hostname": host, "host": ip, "port": port, "family": 2, "proto": 6, "flags": 0}
                    for ip in ips
                ]
        return await self._fallback.resolve(host, port, family)

    async def close(self) -> None:
        await self._fallback.close()


# 解析器全局单例：让 DoH 结果在多次请求之间共享缓存
_resolver: Optional[_DirectResolver] = None


def get_resolver() -> _DirectResolver:
    global _resolver
    if _resolver is None:
        _resolver = _DirectResolver()
    return _resolver


def new_direct_session(**kwargs: Any) -> aiohttp.ClientSession:
    """建一个走直连解析的 aiohttp 会话（不覆盖调用方传入的 connector）。"""
    if "connector" not in kwargs:
        kwargs["connector"] = aiohttp.TCPConnector(
            resolver=get_resolver(),
            ttl_dns_cache=DNS_CACHE_TTL,
            limit=16,
            enable_cleanup_closed=True,
        )
    kwargs.setdefault("trust_env", False)
    return aiohttp.ClientSession(**kwargs)


async def fetch_json(
    url: str,
    params: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    attempts: int = HTTP_ATTEMPTS,
) -> Any:
    """GET 一个 JSON 接口，偶发失败自动重试（这台机器到 B 站握手时好时坏）。"""
    last_error: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            async with new_direct_session(headers=headers) as session:
                async with session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as response:
                    return await response.json(content_type=None)
        except Exception as exc:  # noqa: BLE001 — 重试到上限再交给调用方
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
    if last_error is not None:
        raise last_error
    return None


async def fetch_bytes(
    url: str,
    headers: dict[str, str],
    timeout: int,
    attempts: int = HTTP_ATTEMPTS,
) -> Optional[bytes]:
    """下载一个二进制资源（封面图这类），同样带重试。"""
    for attempt in range(attempts):
        try:
            async with new_direct_session() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as response:
                    return await response.read()
        except Exception:  # noqa: BLE001
            if attempt + 1 < attempts:
                await asyncio.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
    return None
