"""异步 HTTP 抓取 worker。

perception 把 (msg_id, urls) submit 进来,内部 single-worker 协程消费;
失败重试至 max_attempts,最终失败时只让 message_images.sha256 留 NULL,
log warning,不抛 —— perception 路径不能因为图抓不到而崩。

URL-level dedupe:同一 URL 在 in-flight 期间被二次 submit(常见于用户连发
两次同一张图,或反向 push_message 把同 URL 入第二条消息)时,**不重复
HTTP fetch**;两条消息的 (msg_id, idx) 都挂在同一 URL 的 pending 列表上,
fetch 一次结果 fan-out 到所有等待行。
"""

from __future__ import annotations

import asyncio
from collections import deque

import httpx
from nonebot import logger

from .image_cache import ImageCache
from .message_store import MessageStore

# 图片字节魔数 → 标准 MIME。用于 Telegram CDN 这类返回
# `application/octet-stream` 的源 —— Content-Type 不可信,从字节头识别。
_MAGIC_TO_MIME: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def _sniff_image_mime(data: bytes) -> str | None:
    """Return a normalized image MIME if the byte head matches a known magic,
    else None. WebP needs both RIFF header + WEBP at offset 8.
    """
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for magic, mime in _MAGIC_TO_MIME:
        if data.startswith(magic):
            return mime
    return None


class ImageFetcher:
    """单 worker 协程消费 queue;失败重试 max_attempts 次。

    数据结构:
    - `_queue: deque[str]` —— 待抓 URL 的 FIFO。每个 URL 在 queue 里最多一份。
    - `_pending: dict[url, list[(msg_id, idx)]]` —— 每个 in-flight / queued URL
      对应一组等待结果回填的 (msg_id, idx)。fetch 完成时 pop 整个列表批量 update。
    - 单 `httpx.AsyncClient` 实例横跨整个 worker 生命周期(start ↔ stop),
      复用连接池,省去每次 fetch 建/拆连接的开销。
    """

    def __init__(
        self,
        *,
        store: MessageStore,
        cache: ImageCache,
        timeout_s: int = 10,
        max_attempts: int = 2,
        queue_max: int = 1000,
    ) -> None:
        self._store = store
        self._cache = cache
        self._timeout = timeout_s
        self._max_attempts = max(1, max_attempts)
        self._queue_max = queue_max
        self._queue: deque[str] = deque()
        self._pending: dict[str, list[tuple[int, int]]] = {}
        self._wake: asyncio.Event = asyncio.Event()
        self._stop: asyncio.Event = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None

    def submit(self, message_id: int, urls: list[str]) -> None:
        """把每张图挂进 pending 表。同 URL 已 in-flight 时只加 (msg_id, idx),
        不重复入队。overflow 时按 URL 维度丢最老。

        只收 http(s):worker 是 httpx,喂它主机本地路径或 data: URL 只会抛
        UnsupportedProtocol 并重试到上限,白占队列还刷 warning。
        """
        new_urls: list[str] = []
        for idx, url in enumerate(urls):
            if not url:
                continue
            if not url.startswith(("http://", "https://")):
                logger.debug(f"[image_fetcher] skip non-http url (无法抓取): {url[:80]}")
                continue
            if url in self._pending:
                # 已 queued / in-flight,挂上这条 (msg_id, idx) 等同一份结果
                self._pending[url].append((message_id, idx))
                continue
            self._pending[url] = [(message_id, idx)]
            new_urls.append(url)

        for url in new_urls:
            while len(self._queue) >= self._queue_max:
                dropped = self._queue.popleft()
                pending = self._pending.pop(dropped, [])
                logger.warning(
                    f"[image_fetcher] queue full, dropped url={dropped[:80]} with {len(pending)} pending writes"
                )
            self._queue.append(url)
        if new_urls:
            self._wake.set()

    def queue_size(self) -> int:
        return len(self._queue)

    def inflight_urls(self) -> int:
        """诊断用:当前有多少 unique URL 在 pending(队列里 + worker 跑着的)。"""
        return len(self._pending)

    async def start(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._client = httpx.AsyncClient(timeout=self._timeout)
        self._worker = asyncio.create_task(self._run(), name="hermes-image-fetcher")

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._stop.set()
        self._wake.set()
        try:
            await asyncio.wait_for(self._worker, timeout=5)
        except asyncio.TimeoutError:
            self._worker.cancel()
            logger.warning("[image_fetcher] worker did not stop within 5s; cancelled")
        self._worker = None
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.warning(f"[image_fetcher] httpx client aclose failed: {exc}")
            self._client = None
        if self._queue or self._pending:
            logger.info(
                f"[image_fetcher] shutdown: queued_urls={len(self._queue)} "
                f"pending_writes={sum(len(v) for v in self._pending.values())} dropped"
            )
        self._queue.clear()
        self._pending.clear()

    async def _run(self) -> None:
        while not self._stop.is_set():
            if not self._queue:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=1)
                except asyncio.TimeoutError:
                    continue
            if self._stop.is_set():
                break
            if not self._queue:
                continue
            url = self._queue.popleft()
            await self._fetch_one(url)

    async def _fetch_one(self, url: str) -> None:
        last_err: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                assert self._client is not None, "fetcher not started"
                resp = await self._client.get(url)
                resp.raise_for_status()
                bytes_ = resp.content
                mime = self._resolve_mime(resp.headers.get("content-type"), bytes_)
                if mime is None:
                    last_err = (
                        f"non-image: ct={resp.headers.get('content-type')!r} "
                        f"head={bytes_[:16].hex() if bytes_ else 'empty'}"
                    )
                    break
                sha = self._cache.put(bytes_, mime)
                pending = self._pending.pop(url, [])
                for message_id, idx in pending:
                    self._store.update_image_sha(message_id, idx, sha, mime)
                return
            except Exception as exc:
                last_err = repr(exc)
                if attempt < self._max_attempts:
                    await asyncio.sleep(0.2 * attempt)  # 简单线性退避
        pending = self._pending.pop(url, [])
        logger.warning(
            f"[image_fetcher] fetch failed url={url[:80]} "
            f"pending={len(pending)} after {self._max_attempts} attempts: {last_err}"
        )

    @staticmethod
    def _resolve_mime(content_type: str | None, bytes_: bytes) -> str | None:
        """信任顺序:
        1. Content-Type 是 image/* → 直接用
        2. Content-Type 是 application/octet-stream(Telegram CDN 典型行为)
           或缺失 → 嗅探字节魔数
        3. 字节嗅探命中已知图片格式 → 用嗅探结果
        4. 都不是 → 返回 None,表示真不是图
        """
        ct = (content_type or "").split(";", 1)[0].strip().lower()
        if ct.startswith("image/"):
            return ct
        sniffed = _sniff_image_mime(bytes_)
        return sniffed
