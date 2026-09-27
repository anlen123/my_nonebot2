"""哔哩哔哩视频信息查询：av/BV 号 → 标题、封面、统计与简介。

上游接口：GET https://api.bilibili.com/x/web-interface/view?aid=<avid>

两个要点：
  * BV 号先转 av 号 —— 视频页 HTML 里带着 aid，正则抠出来即可，不必自己算校验；
  * 结果文案（含「错误!!! 没有此av或BV号。」）由本模块给出，调用方原样发出去。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import httpx
import nonebot
import requests
from nonebot.adapters.onebot.v11 import MessageSegment

API_URL = "https://api.bilibili.com/x/web-interface/view"
VIDEO_PAGE = "https://www.bilibili.com/video"

# 抓视频页与调接口用的 UA 各自保留（原本就不同），免得上游按 UA 下发不同内容
_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_API_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)


def bv2av(bv: str) -> str:
    """BV 号转 av 号：从视频页 HTML 里抠 aid；页面里找不到时抛 IndexError（与旧行为一致）。"""
    response = requests.get(f"{VIDEO_PAGE}/{bv}", headers={"User-Agent": _WEB_UA})
    response.encoding = "utf-8"
    return re.findall(f'"aid":(.*?),"bvid":"{bv}"', response.text)[0]


async def get_av_data(av: str) -> Optional[str]:
    """av/BV 号 → 可直接发送的文案；接口说没有这个视频时返回 None。

    Returns:
        含标题、封面、统计、链接与简介的文案；视频不存在时 None；
        返回结构对不上预期（含不存在的视频）时给错误提示文案。
    """
    av = str(av)
    if av[0:2] == "BV":
        avcode = bv2av(av)
    else:
        avcode = av.replace("av", "")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_URL}?aid={avcode}", headers={"user-agent": _API_UA}
        )
    payload: Dict[str, Any] = json.loads(response.text)

    if payload["code"] == "0" and not payload["data"]:
        return None

    try:
        data = payload["data"]
        stat = data["stat"]
        link = f"{VIDEO_PAGE}/av{avcode}"
        return (
            "标题:" + data["title"] + "\n" + MessageSegment.image(data["pic"])
            + f"播放:{stat['view']} 弹幕:{stat['danmaku']} 评论:{stat['reply']} 收藏:{stat['favorite']} 硬币:{stat['coin']} 分享:{stat['share']} 点赞:{stat['like']} \n点击连接进入: \n{link}\n简介: {data['desc']}"
        )
    except Exception as exc:  # noqa: BLE001 —— 字段对不上就当视频不存在，回错误文案
        nonebot.logger.warning(f"[biliav] 解析视频信息失败 aid={avcode}：{exc}")
        return "错误!!! 没有此av或BV号。"
