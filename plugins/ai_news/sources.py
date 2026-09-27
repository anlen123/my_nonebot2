"""
ai_news 资讯源定义与抓取

内置源登记在 SOURCES 里，由 kind 决定解析方式：
  kind="rss"      —— RSS 2.0 / Atom，走 XML 解析
  kind="zhidx"    —— 智东西（列表页 HTML，标题挂在 a[title]）
  kind="leiphone" —— 雷锋网（列表页 HTML，带发布时间）

抓取统一带 _BASE_HEADERS；主地址抓不到内容时，若源配了 fallback_url /
fallback_kind 就退回备用地址再试一次（典型场景：RSS 失效 → 退回 HTML 列表页）。
"""

from __future__ import annotations

import html as _html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 短于这个长度的标题基本都是栏目名 / 导航项，不是新闻
MIN_TITLE_LEN = 6
# 单个源一次最多解析多少条，防止异常源把内存吃满
MAX_PARSED_ITEMS = 80

# 所有抓取都带 UA + 语言偏好；需要声明 Accept 的地址在 _BASE_HEADERS 之上叠加
_BASE_HEADERS: Dict[str, str] = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
_FEED_HEADERS: Dict[str, str] = {
    **_BASE_HEADERS,
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
}

# ── 内置资讯源 ────────────────────────────────────────────────────────────────
# limit  —— 每次最多取几条
# filter —— 通用科技源需要用 AI 关键词过滤
SOURCES: Dict[str, Dict[str, Any]] = {
    # 中文
    "zhidx": {
        "name": "智东西",
        "kind": "zhidx",
        "url": "https://www.zhidx.com/p/category/%E4%BA%BA%E5%B7%A5%E6%99%BA%E8%83%BD",
        "limit": 5,
    },
    "leiphone": {
        # RSS 更稳（带发布时间），抓不到时自动退回 HTML 的 AI 频道页
        "name": "雷锋网",
        "kind": "rss",
        "url": "https://www.leiphone.com/feed",
        "limit": 5,
        "filter": True,
        "fallback_url": "https://www.leiphone.com/category/ai",
        "fallback_kind": "leiphone",
    },
    "infoq": {
        "name": "InfoQ",
        "kind": "rss",
        "url": "https://www.infoq.cn/feed",
        "limit": 3,
        "filter": True,
    },
    "tmtpost": {
        "name": "钛媒体",
        "kind": "rss",
        "url": "https://www.tmtpost.com/rss.xml",
        "limit": 3,
        "filter": True,
    },
    "oschina": {
        "name": "开源中国",
        "kind": "rss",
        "url": "https://www.oschina.net/news/rss",
        "limit": 3,
        "filter": True,
    },
    "36kr": {
        # 36氪的 /feed 已改版成网页，抓不到 RSS，默认不启用
        "name": "36氪",
        "kind": "rss",
        "url": "https://36kr.com/feed",
        "limit": 3,
        "filter": True,
    },
    "ithome": {
        "name": "IT之家",
        "kind": "rss",
        "url": "https://www.ithome.com/rss/",
        "limit": 3,
        "filter": True,
    },
    # 英文
    "openai": {
        "name": "OpenAI",
        "kind": "rss",
        "url": "https://openai.com/news/rss.xml",
        "limit": 3,
    },
    "techcrunch": {
        "name": "TechCrunch",
        "kind": "rss",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "limit": 3,
    },
    "venturebeat": {
        "name": "VentureBeat",
        "kind": "rss",
        "url": "https://feeds.feedburner.com/venturebeat/SZYF",
        "limit": 2,
    },
    "theverge": {
        "name": "TheVerge",
        "kind": "rss",
        "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "limit": 2,
    },
    "arxiv": {
        "name": "arXiv",
        "kind": "rss",
        "url": "https://export.arxiv.org/rss/cs.AI",
        "limit": 3,
    },
    "hn": {
        "name": "HackerNews",
        "kind": "rss",
        "url": "https://hnrss.org/newest?q=AI",
        "limit": 2,
    },
    "synced": {
        "name": "机器之能",
        "kind": "rss",
        "url": "https://syncedreview.com/feed/",
        "limit": 2,
    },
}

# 没配 AI_NEWS_SOURCES 时默认启用的源，顺序即表格里的轮询顺序
DEFAULT_SOURCES = [
    "zhidx",
    "leiphone",
    "infoq",
    "tmtpost",
    "openai",
    "techcrunch",
    "arxiv",
]

# 通用科技源（InfoQ / 36氪 / IT之家）用的 AI 关键词
AI_KEYWORDS: List[str] = [
    "ai", "人工智能", "大模型", "大语言模型", "语言模型", "生成式", "机器学习", "深度学习",
    "神经网络", "算法", "智能体", "agent", "多模态", "推理", "算力", "gpu", "英伟达", "nvidia",
    "预训练", "微调", "蒸馏", "openai", "chatgpt", "gpt", "claude", "anthropic", "gemini",
    "llm", "copilot", "deepseek", "qwen", "通义", "豆包", "kimi", "文心", "混元", "智谱",
    "月之暗面", "百川", "阶跃", "aigc", "midjourney", "stable diffusion", "sora", "机器人",
    "具身智能", "自动驾驶", "智能驾驶", "hugging face", "transformer", "昇腾", "寒武纪",
    "智能眼镜", "ai 手机", "ai手机", "ai pc", "ai芯片", "ai 芯片", "智算",
]


@dataclass
class NewsItem:
    """一条资讯。published 是本机时区（naive），源没给发布时间时为 None。"""

    source: str          # 来源显示名
    title: str
    url: str
    published: Optional[datetime] = None


# ── 工具函数 ──────────────────────────────────────────────────────────────────
_ASCII_KW = re.compile(r"^[a-z0-9 .+:/-]+$")


def _clean_text(text: str) -> str:
    """去掉 HTML 实体与标签、压掉多余空白，返回单行纯文本"""
    text = _html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_ai_related(title: str, keywords: Sequence[str] = AI_KEYWORDS) -> bool:
    """标题里是否出现了任一 AI 关键词（通用科技源用它过滤）"""
    low = title.lower()
    for kw in keywords:
        kw = kw.lower()
        if not kw:
            continue
        if _ASCII_KW.match(kw):
            # 英文关键词按词边界匹配，避免 "ai" 命中 "training" 这类误伤
            if re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", low):
                return True
        elif kw in low:
            return True
    return False


def _parse_date(raw: str) -> Optional[datetime]:
    """解析 RFC822 / ISO8601 时间，统一换算成本地时间（naive）"""
    if not raw:
        return None
    raw = raw.strip()

    now = datetime.now()

    def _localize(dt: Optional[datetime]) -> Optional[datetime]:
        if not dt:
            return None
        if dt.tzinfo:
            dt = dt.astimezone().replace(tzinfo=None)
        # 少数源的时间会「跑到未来」，统一收敛到当前时间
        return min(dt, now)

    try:
        dt = parsedate_to_datetime(raw)
        if dt:
            return _localize(dt)
    except (TypeError, ValueError, IndexError):
        pass  # RFC822 解不出来就往下试 ISO8601

    text = raw.replace("Z", "+00:00")
    for candidate in (text, text[:19], text[:10]):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return _localize(dt)
    return None


def _norm_title(title: str) -> str:
    """标题归一化（小写 + 去标点空白），用于跨源去重"""
    t = _clean_text(title).lower()
    t = re.sub(r"[\s，。！？、：；「」【】《》（）()\[\]“”\"'’‘|·,.:;!?—\-_/\\]+", "", t)
    return t


# ── 各类型解析 ────────────────────────────────────────────────────────────────
def _tag(elem: ET.Element) -> str:
    """取标签名并剥掉 XML 命名空间前缀"""
    return elem.tag.split("}")[-1] if isinstance(elem.tag, str) else ""


def _child_text(elem: ET.Element, names: Iterable[str]) -> str:
    """取子元素文本：先看直接文本，再看其子节点的 tail（兼容带内联标签/CDATA 的源）"""
    for child in elem:
        if _tag(child) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            for sub in child:
                if sub.tail and sub.tail.strip():
                    return sub.tail.strip()
    return ""


def parse_rss(xml_bytes: bytes, source_name: str) -> List[NewsItem]:
    """解析 RSS 2.0 / Atom 字节流；XML 坏了就返回空列表"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    items: List[NewsItem] = []
    for elem in root.iter():
        if _tag(elem) not in ("item", "entry"):
            continue

        title = _clean_text(_child_text(elem, ("title",)))
        if not title:
            continue

        link = _child_text(elem, ("link",)) or _child_text(elem, ("id",))
        if not link:
            # Atom 的 link 是空元素，地址在 href 属性里
            for child in elem:
                if _tag(child) == "link" and child.get("href"):
                    link = child.get("href", "").strip()
                    break

        raw_date = _child_text(elem, ("pubDate", "published", "updated", "date", "dc:date"))
        items.append(
            NewsItem(
                source=source_name,
                title=title,
                url=link.strip(),
                published=_parse_date(raw_date),
            )
        )
        if len(items) >= MAX_PARSED_ITEMS:
            break
    return items


_ZHIDX_RE = re.compile(
    r'href="(https://www\.zhidx\.com/p/\d+\.html)"[^>]*?title="([^"]{4,160})"'
)


def parse_zhidx(html_text: str, source_name: str) -> List[NewsItem]:
    """解析智东西列表页（列表页没有发布时间）"""
    items: List[NewsItem] = []
    seen = set()
    for url, title in _ZHIDX_RE.findall(html_text):
        if url in seen:
            continue
        seen.add(url)
        clean = _clean_text(title)
        if len(clean) < MIN_TITLE_LEN or clean in ("智东西", "快讯"):
            continue
        items.append(NewsItem(source=source_name, title=clean, url=url))
    return items


_LEIPHONE_RE = re.compile(
    r'<a\s+href="(https://www\.leiphone\.com/category/[^"]+\.html)"[^>]*?title="([^"]{4,200})"'
    r'[^>]*class="headTit"',
    re.S,
)
_LEIPHONE_TIME_RE = re.compile(r'<div class="time">([^<]+)</div>')
_LEIPHONE_DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})")


def parse_leiphone(html_text: str, source_name: str) -> List[NewsItem]:
    """解析雷锋网 AI 频道列表页；时间只给「月日时分」，需要补年份"""
    items: List[NewsItem] = []
    seen = set()
    for match in _LEIPHONE_RE.finditer(html_text):
        url, title = match.group(1), _clean_text(match.group(2))
        if url in seen or len(title) < MIN_TITLE_LEN:
            continue
        seen.add(url)

        published = None
        # 时间在同一条目的标题链接后面，就近截一段搜即可
        tail = html_text[match.end(): match.end() + 1200]
        time_match = _LEIPHONE_TIME_RE.search(tail)
        if time_match:
            date_match = _LEIPHONE_DATE_RE.search(time_match.group(1))
            if date_match:
                month, day, hour, minute = (int(x) for x in date_match.groups())
                now = datetime.now()
                try:
                    published = datetime(now.year, month, day, hour, minute)
                except ValueError:
                    published = None
                if published and published > now + timedelta(days=1):
                    # 12 月看到 1 月的稿子会落到明年：跨年就按去年算
                    published = published.replace(year=now.year - 1)
        items.append(NewsItem(source=source_name, title=title, url=url, published=published))
    return items


# ── 抓取 ──────────────────────────────────────────────────────────────────────
def _parse_by_kind(kind: str, resp: httpx.Response, source_name: str) -> List[NewsItem]:
    """按 kind 分派解析器，未知 kind 一律按 RSS 处理"""
    if kind == "zhidx":
        return parse_zhidx(resp.text, source_name)
    if kind == "leiphone":
        return parse_leiphone(resp.text, source_name)
    return parse_rss(resp.content, source_name)


async def fetch_source(
    client: httpx.AsyncClient,
    spec: Dict[str, Any],
    timeout: float,
) -> List[NewsItem]:
    """抓一个源并解析成条目（HTTP 异常照常抛出，由调用方决定是否换 client 重试）"""
    resp = await client.get(
        spec["url"],
        timeout=timeout,
        headers=_FEED_HEADERS,
        follow_redirects=True,
    )
    resp.raise_for_status()

    items = _parse_by_kind(spec.get("kind", "rss"), resp, spec["name"])

    if not items and spec.get("fallback_url"):
        # 主地址抓不到内容时退回备用地址（例如 RSS 失效退回 HTML 列表页）
        fallback = await client.get(
            spec["fallback_url"],
            timeout=timeout,
            headers=_BASE_HEADERS,
            follow_redirects=True,
        )
        fallback.raise_for_status()
        items = _parse_by_kind(spec.get("fallback_kind", "rss"), fallback, spec["name"])

    if spec.get("filter"):
        items = [item for item in items if is_ai_related(item.title)]

    return items[: int(spec.get("limit", 5))]


def merge_items(groups: Sequence[Sequence[NewsItem]], max_items: int) -> List[NewsItem]:
    """按来源轮流取（保证中英文/各站点都能上表），并去掉重复标题与重复链接"""
    queues = [list(group) for group in groups]
    picked: List[NewsItem] = []
    seen_titles, seen_urls = set(), set()

    while len(picked) < max_items and any(queues):
        for queue in queues:
            if len(picked) >= max_items:
                break
            for idx, item in enumerate(queue):
                key = _norm_title(item.title)
                if key in seen_titles or (item.url and item.url in seen_urls):
                    queue.pop(idx)
                    break
                queue.pop(idx)
                picked.append(item)
                seen_titles.add(key)
                if item.url:
                    seen_urls.add(item.url)
                break
    return picked
