"""deepseek_balance —— AI 账户余额查询（卡片图片，带「与上次查询对比」）

用法（私聊/群聊均可）：
  /ai余额      查 DeepSeek + Aihub 全部账户，回一张余额卡片
  别名：ai余额 / 余额 / /余额 / /ai余额查询
  另外每隔 DEEPSEEK_BALANCE_INTERVAL 秒自动查一次，把卡片私聊推给 DEEPSEEK_BALANCE_TARGET

卡片上的对比信息：
  * 每个账户金额下方一行：↑ +1.23（+5.2%）变多 / ↓ -0.45（-3.1%）变少 / → 0.00（持平）
  * 每个币种分组标题右侧：该组「较上次 ↑ ¥ 1.23」的合计增减
  * 页脚写明对比基准是哪一次查询；没有历史记录时显示「首次查询，暂无对比」
  快照存在 data/deepseek_balance/balance_history.json（见 history.py），本轮查询失败
  的账户不会覆盖旧基准，下次成功仍能算出跨次差值。

文件分工：
  config.py    读 .env
  providers.py 站外供应商（Aihub）查询 + DeepSeek 响应转统一结构
  history.py   余额快照存取 + 与上次查询的差值计算
  render.py    Pillow 画卡片
  __init__.py  命令与定时任务（本文件）

依赖 auto_message 插件所用的 nonebot_plugin_apscheduler 定时器。

配置（.env.dev / .env.prod）：
  DEEPSEEK_API_KEY=sk-xxx            # DeepSeek API Key（必填）
  DEEPSEEK_BALANCE_INTERVAL=7200     # 检查间隔（秒），默认 7200（2 小时）
  DEEPSEEK_BALANCE_TARGET=1761512493 # 接收余额通知的 QQ 号
  DEEPSEEK_BALANCE_LOW=10            # 余额预警线，低于该值卡片标黄/红，默认 10
  DEEPSEEK_BALANCE_HISTORY=          # 余额快照文件路径，默认 data/deepseek_balance/balance_history.json
  AIHUB_API_KEY=sk-xxx               # Aihub 供应商密钥
"""

import asyncio
import base64
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import nonebot
from nonebot import on_command, require
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment, permission

from .config import load_config
from .history import annotate, load_snapshot, make_snapshot, save_snapshot
from .providers import (
    PROVIDERS,
    deepseek_entry,
    entry,
    fetch_all as fetch_provider_balances,
    _http_detail,
    _proxy_candidate,
)
from .render import render_error, render_multi, _human_interval

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

# ── 加载配置 ──────────────────────────────────────────────────────────────────
_cfg = load_config()
API_KEY = _cfg["deepseek_api_key"]
INTERVAL = _cfg["deepseek_balance_interval"]
TARGET = _cfg["deepseek_balance_target"]
LOW = _cfg["deepseek_balance_low"]
HISTORY_PATH = _cfg["balance_history_path"]

# 快照的「读-改-写」必须串行：手动查询与定时推送可能同时进行
_HISTORY_LOCK = asyncio.Lock()

BALANCE_URL = "https://api.deepseek.com/user/balance"
_DIRECT_TIMEOUT = 30          # 直连超时（秒）
_PROXY_TIMEOUT = 15           # 走代理时短一点：代理不通就没必要再等
_COOLDOWN = 10.0              # 手动查询的冷却（秒），防止连着刷图


# ── 查询 ──────────────────────────────────────────────────────────────────────
async def fetch_balance() -> Dict[str, Any]:
    """请求 DeepSeek 余额接口，失败时抛异常。

    默认直连（trust_env=False）：requests 会读取 Windows 系统代理注册表
    （本机 HKCU 指向 127.0.0.1:7892），代理没启动时 DeepSeek 查询就会
    ProxyError 失败；httpx 不读注册表，只在 trust_env=True 时读环境变量，
    所以这里统一用 httpx + trust_env=False 直连，避开系统代理。
    直连失败时再退回本机代理重试一次，兼容必须走代理的网络环境。
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

    async def _request(proxy: Optional[str], timeout: float) -> Dict[str, Any]:
        async with httpx.AsyncClient(
            proxy=proxy, trust_env=False, timeout=timeout, follow_redirects=False
        ) as client:
            resp = await client.get(BALANCE_URL, headers=headers)
        resp.raise_for_status()
        return resp.json()

    try:
        return await _request(None, _DIRECT_TIMEOUT)
    except Exception as direct_err:  # noqa: BLE001 —— 直连失败原因五花八门（代理/超时/网络），统一兜底
        proxy = _proxy_candidate()
        if not proxy:
            raise
        try:
            data = await _request(proxy, _PROXY_TIMEOUT)
        except Exception:  # noqa: BLE001 —— 代理也没通：报直连的原因对排查更有用
            raise direct_err from None
        nonebot.logger.info("[deepseek_balance] DeepSeek 直连失败，经代理查询成功")
        return data


def _image_msg(png: bytes) -> Message:
    """把 PNG 字节包成一张 base64 图片消息"""
    return Message(
        MessageSegment.image(f"base64://{base64.b64encode(png).decode()}")
    )


# ── 查询结果 → 卡片行 ─────────────────────────────────────────────────────────
def _failed_entry(title: str, unit: str, reason: str) -> Dict[str, Any]:
    """构造一条失败行（没有余额、状态 error，detail 即失败原因）"""
    return entry(title, None, unit, "error", reason)


def _failure_detail(exc: Exception) -> str:
    """把异常翻成卡片上能看懂的一句话。

    HTTP 状态码比异常类名有信息量（401 说明密钥失效，403 说明被拦），优先用它。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return _http_detail(exc.response.status_code)
    return f"请求失败（{type(exc).__name__}）"


async def _deepseek_entry() -> Dict[str, Any]:
    """查 DeepSeek 并转成卡片行；没配 Key 或查询失败都返回一条失败行"""
    if not API_KEY:
        return _failed_entry("DeepSeek", "RMB", "未配置 DEEPSEEK_API_KEY")
    try:
        return deepseek_entry(await fetch_balance(), low_threshold=LOW)
    except Exception as e:  # noqa: BLE001 —— 单家失败只影响它自己那一行
        nonebot.logger.warning(f"[ai_balance] DeepSeek 查询失败：{e}")
        return _failed_entry("DeepSeek", "RMB", _failure_detail(e))


async def _build_entries() -> List[Dict[str, Any]]:
    """并发查询 DeepSeek + 站外供应商，任何一家失败只影响它自己那一行"""
    keys = _cfg.get("balance_keys") or {}
    # 先起任务再查 DeepSeek，两家并行，总耗时是较慢的那一家
    providers_task = asyncio.ensure_future(fetch_provider_balances(keys))
    entries: List[Dict[str, Any]] = [await _deepseek_entry()]

    try:
        entries.extend(await providers_task)
    except Exception as e:  # noqa: BLE001 —— 整批异常时按配置逐家补一行，卡片结构不变
        nonebot.logger.warning(f"[ai_balance] 供应商批量查询失败：{e}")
        entries.extend(
            _failed_entry(spec["title"], spec["unit"], f"查询异常（{type(e).__name__}）")
            for spec in PROVIDERS
        )
    return entries


def _ok_count(entries: List[Dict[str, Any]]) -> int:
    """统计查询正常的账户数（定时推送与手动查询共用同一份口径）"""
    return sum(1 for item in entries if item.get("status") == "ok")


async def _compare_with_history(
    entries: List[Dict[str, Any]], *, source: str
) -> Optional[float]:
    """把本轮余额与上次快照对比（差值写进每行 extra），并把本轮存为新基准。

    返回上次查询的时间戳（没有历史时 None），供卡片页脚显示对比基准。
    加锁：手动查询与定时推送可能同时跑，避免两次「读-改-写」互相覆盖。
    存快照失败只记日志 —— 对比是附加功能，不能连累这次查询。
    """
    async with _HISTORY_LOCK:
        snapshot = load_snapshot(HISTORY_PATH)
        annotate(entries, snapshot)
        baseline_at = snapshot.get("updated_at")
        baseline = float(baseline_at) if isinstance(baseline_at, (int, float)) else None
        try:
            save_snapshot(
                HISTORY_PATH,
                make_snapshot(entries, snapshot, source=source),
            )
        except OSError as e:
            nonebot.logger.warning(f"[ai_balance] 余额快照写入失败：{e}")
        return baseline


def _render_card(
    entries: List[Dict[str, Any]],
    *,
    source: str,
    log_prefix: str,
    baseline: Optional[float] = None,
) -> bytes:
    """渲染多供应商卡片。

    渲染本身炸了就退回错误卡片 —— 无论定时推送还是命令，都必须有图可发。
    两种场景的日志措辞不同（定时渲染失败 / 渲染失败），故由调用方给出前缀。
    baseline 是上次查询的时间戳，卡片页脚据此写明对比基准。
    """
    try:
        return render_multi(
            entries,
            source=source,
            baseline=datetime.fromtimestamp(baseline) if baseline else None,
        )
    except Exception as e:  # noqa: BLE001 —— 渲染失败不能静默，退回提示卡片
        nonebot.logger.error(f"[ai_balance] {log_prefix}渲染失败：{e}")
        return render_error(f"渲染失败 {type(e).__name__}: {e}")


# ── 定时推送：AI 余额（DeepSeek + Aihub） ─────────────────────────────────────
async def check_ai_balance() -> None:
    """定时任务：查询全部 AI 账户余额并把卡片私聊推给目标用户"""
    entries = await _build_entries()
    baseline = await _compare_with_history(entries, source="定时推送")
    png = _render_card(entries, source="定时推送", log_prefix="定时", baseline=baseline)
    base_text = (
        f"对比基准 {datetime.fromtimestamp(baseline):%m-%d %H:%M}" if baseline else "首次查询，无对比基准"
    )
    nonebot.logger.info(
        f"[ai_balance] 定时查询完成：{_ok_count(entries)}/{len(entries)} 家正常，{base_text}"
    )

    try:
        bot: Bot = nonebot.get_bot()
        await bot.send_private_msg(user_id=int(TARGET), message=_image_msg(png))
        nonebot.logger.info(f"[ai_balance] 余额卡片已发送给 {TARGET}")
    except Exception as e:  # noqa: BLE001 —— 通知失败只记日志，定时任务本身不能中断
        nonebot.logger.warning(f"[ai_balance] 发送通知失败：{e}")


# ── 手动命令：/ai余额（DeepSeek + Aihub） ─────────────────────────────────────
ai_balance_cmd = on_command(
    "/ai余额",
    aliases={"ai余额", "余额", "/余额", "/ai余额查询"},
    permission=permission.PRIVATE | permission.GROUP,
    priority=5,
    block=True,
)

_last_ai_manual = 0.0


@ai_balance_cmd.handle()
async def _handle_ai_balance() -> None:
    global _last_ai_manual
    now = time.time()
    waited = now - _last_ai_manual
    if waited < _COOLDOWN:
        # finish() 会抛出结束异常，后面的代码不会执行
        await ai_balance_cmd.finish(
            f"查询太频繁了，{int(_COOLDOWN - waited) + 1} 秒后再试～"
        )
    _last_ai_manual = now

    await ai_balance_cmd.send("正在查询 AI 账户余额（DeepSeek + Aihub）...")
    entries = await _build_entries()
    baseline = await _compare_with_history(entries, source="手动查询")
    png = _render_card(entries, source="手动查询", log_prefix="", baseline=baseline)
    nonebot.logger.info(f"[ai_balance] 查询完成：{_ok_count(entries)}/{len(entries)} 家正常")
    await ai_balance_cmd.finish(_image_msg(png))


# ── 注册定时任务 ──────────────────────────────────────────────────────────────
if not API_KEY:
    nonebot.logger.warning(
        "[deepseek_balance] DEEPSEEK_API_KEY 未配置，插件不会运行"
    )
else:
    scheduler.add_job(
        check_ai_balance,
        trigger="interval",
        seconds=INTERVAL,
        id="ai_balance_check",
        replace_existing=True,
        misfire_grace_time=600,
        coalesce=True,
    )
    nonebot.logger.info(
        f"[ai_balance] 已注册定时任务（AI 余额）：间隔 {INTERVAL}s"
        f"（{_human_interval(INTERVAL)}），通知目标 {TARGET}，"
        f"预警线 {LOW}"
    )
