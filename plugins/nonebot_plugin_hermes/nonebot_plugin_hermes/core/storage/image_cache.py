"""文件系统图字节缓存。

按 SHA256 内容寻址命名,LRU 按 atime 淘汰直至总大小符合配额。

并发安全:put 用 tmpfile + 原子 rename,并发写同 sha 不损坏。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from nonebot import logger

_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
# 已知扩展 → 标准 MIME 的反向表。未知扩展回退到 application/octet-stream。
_EXT_TO_MIME = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "bin": "application/octet-stream",
}
# get_bytes 扫文件时按这个顺序试扩展名,bin 放最后是因为正常图都用具体 ext。
_KNOWN_EXTS = ("jpg", "png", "webp", "gif", "bin")


def _mime_to_ext(mime: str) -> str:
    normalized = (mime or "").split(";", 1)[0].strip().lower()
    return _MIME_TO_EXT.get(normalized, "bin")


def _ext_to_mime(ext: str) -> str:
    return _EXT_TO_MIME.get(ext.lower(), "application/octet-stream")


class ImageCache:
    """SHA256 命名的扁平目录,LRU 按 atime。"""

    def __init__(self, cache_dir: Path, quota_bytes: int) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._quota = quota_bytes

    def put(self, raw_bytes: bytes, mime_type: str) -> str:
        """存字节,返回 sha256。已存在的 sha 跳过写。"""
        sha = hashlib.sha256(raw_bytes).hexdigest()
        ext = _mime_to_ext(mime_type)
        path = self._dir / f"{sha}.{ext}"
        if path.exists():
            return sha
        tmp = self._dir / f"{sha}.{ext}.tmp.{os.getpid()}"
        try:
            tmp.write_bytes(raw_bytes)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning(f"[image_cache] write {sha} failed: {exc}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return sha

    def get_bytes(self, sha256: str) -> tuple[bytes, str] | None:
        """读字节;sha 未知 / 文件已被外部删 → None。读到后 touch atime。"""
        for ext in _KNOWN_EXTS:
            path = self._dir / f"{sha256}.{ext}"
            if not path.exists():
                continue
            try:
                bytes_ = path.read_bytes()
            except OSError:
                return None
            # 把 atime 推到当下,用于 LRU 排序
            try:
                os.utime(path, None)
            except OSError:
                pass
            return bytes_, _ext_to_mime(ext)
        return None

    def evict_if_over_quota(self) -> int:
        """目录总大小超 quota 时按 atime 老到新删,返回删除字节数。"""
        entries: list[tuple[Path, int, float]] = []
        total = 0
        for p in self._dir.iterdir():
            if not p.is_file():
                continue
            name = p.name
            # 跳过 tmp 写中间态(.tmp.<pid>)
            if ".tmp." in name:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append((p, st.st_size, st.st_atime))
            total += st.st_size

        if total <= self._quota:
            return 0

        entries.sort(key=lambda e: e[2])  # atime asc → 最老在前
        deleted = 0
        for p, size, _atime in entries:
            try:
                p.unlink()
                deleted += size
                total -= size
            except OSError as exc:
                logger.warning(f"[image_cache] unlink {p.name} failed: {exc}")
                continue
            if total <= self._quota:
                break
        return deleted

    def total_size_bytes(self) -> int:
        """当前缓存总大小(诊断用)。"""
        total = 0
        for p in self._dir.iterdir():
            if p.is_file() and ".tmp." not in p.name:
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
        return total
