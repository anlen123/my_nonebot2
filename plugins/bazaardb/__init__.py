"""
bazaardb -- BazaarDB 物品 & 怪物 & 用户查询插件

用法：
  bz <关键词>            查询物品/怪物，例如：bz 光纤 / bz 多尔王
  巴扎查分 <用户名>      查询用户排位分数，例如：巴扎查分 xikala
  巴扎绑定 <游戏账号>    绑定当前QQ到游戏账号（群维度）
  巴扎排名               查询本群所有绑定成员的当前排名
"""

import asyncio
import base64
import importlib.util
import json
import aiohttp
from pathlib import Path
from typing import Dict, List, Optional

import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, GroupMessageEvent

# 脚本目录（与插件同目录）
SCRAPER_DIR = Path(__file__).parent

# 缓存目录
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

bz          = on_regex(pattern=r"^巴扎 ")
bz_user     = on_regex(pattern=r"^巴扎查分 ")
bz_bind     = on_regex(pattern=r"^巴扎绑定 ")
bz_unbind   = on_regex(pattern=r"^巴扎解绑$")
bz_rank     = on_regex(pattern=r"^巴扎排名$")
bz_alias    = on_regex(pattern=r"^巴扎别名&")

# ── 别名持久化：{ "xxx": "yyy" } ─────────────────────────────────────────────
ALIAS_FILE = Path(__file__).parent / "aliases.json"

def _load_aliases() -> Dict[str, str]:
    if ALIAS_FILE.exists():
        try:
            return json.loads(ALIAS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_aliases(data: Dict[str, str]):
    ALIAS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

_aliases: Dict[str, str] = _load_aliases()

# ── 绑定数据持久化（JSON 文件）────────────────────────────────────────────────
# 结构：{ "群号": { "QQ号": "游戏账号", ... }, ... }
BIND_FILE = Path(__file__).parent / "bindings.json"

def _load_bindings() -> Dict[str, Dict[str, str]]:
    if BIND_FILE.exists():
        try:
            return json.loads(BIND_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_bindings(data: Dict[str, Dict[str, str]]):
    BIND_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# 内存缓存
_bindings: Dict[str, Dict[str, str]] = _load_bindings()


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _load_scraper(filename: str):
    path = SCRAPER_DIR / filename
    spec = importlib.util.spec_from_file_location(filename[:-3], path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _send_image(bot: Bot, event, img_bytes: bytes):
    img_b64 = base64.b64encode(img_bytes).decode()
    await bot.send(event=event, message=MessageSegment.image(f"base64://{img_b64}"))


# ── bz <关键词>：物品 / 怪物查询 ─────────────────────────────────────────────

def _item_cache_path(keyword: str) -> Path:
    safe = keyword.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"bazaardb_{safe}.png"


def _query_item_sync(keyword: str) -> bytes | None:
    scraper = _load_scraper("bazaardb_scraper.py")
    asyncio.run(scraper.scrape_and_export(keyword, str(CACHE_DIR)))
    safe = keyword.replace("/", "_").replace("\\", "_")
    png  = CACHE_DIR / f"bazaardb_{safe}.png"
    return png.read_bytes() if png.exists() else None


@bz.handle()
async def bz_rev(bot: Bot, event: Event):
    keyword = str(event.message).strip()[3:].strip()
    if not keyword:
        await bot.send(event=event, message=MessageSegment.text("请输入关键词，例如：巴扎 光纤"))
        return

    # 别名解析
    real_keyword = _aliases.get(keyword, keyword)
    if real_keyword != keyword:
        nonebot.logger.info(f"[bazaardb] 别名 {keyword} -> {real_keyword}")
    keyword = real_keyword

    cache_file = _item_cache_path(keyword)
    if cache_file.exists():
        nonebot.logger.info(f"[bazaardb] 命中缓存 keyword={keyword}")
        await _send_image(bot, event, cache_file.read_bytes())
        return

    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(None, _query_item_sync, keyword)
    except Exception as e:
        nonebot.logger.warning(f"[bazaardb] 查询失败 keyword={keyword}: {e}")
        await bot.send(event=event, message=MessageSegment.text(f"查询失败：{e}"))
        return

    if img_bytes is None:
        await bot.send(event=event, message=MessageSegment.text(f"未找到「{keyword}」相关物品或怪物"))
        return

    cache_file.write_bytes(img_bytes)
    nonebot.logger.info(f"[bazaardb] 已缓存 keyword={keyword}")
    await _send_image(bot, event, img_bytes)


# ── 巴扎别名 <xxx> <yyy>：设置/查看/删除别名 ─────────────────────────────────

@bz_alias.handle()
async def bz_alias_rev(bot: Bot, event: Event):
    args = str(event.message).strip()[5:].strip().split()

    # 巴扎别名 xxx yyy  -> 设置别名
    if len(args) == 2:
        src, dst = args[0], args[1]
        _aliases[src] = dst
        _save_aliases(_aliases)
        nonebot.logger.info(f"[bazaardb] 设置别名 {src} -> {dst}")
        await bot.send(event=event, message=MessageSegment.text(
            f"✅ 别名设置成功：巴扎 {src} → 实际查询「{dst}」"
        ))
        return

    # 巴扎别名 xxx     -> 查看该别名
    if len(args) == 1:
        src = args[0]
        if src in _aliases:
            await bot.send(event=event, message=MessageSegment.text(
                f"📌 当前别名：{src} → {_aliases[src]}"
            ))
        else:
            await bot.send(event=event, message=MessageSegment.text(
                f"「{src}」未设置别名"
            ))
        return

    # 无参数 -> 列出所有别名
    if not _aliases:
        await bot.send(event=event, message=MessageSegment.text("当前没有设置任何别名"))
        return
    lines = ["📋 当前所有别名："] + [f"  {k} → {v}" for k, v in _aliases.items()]
    await bot.send(event=event, message=MessageSegment.text("\n".join(lines)))


# ── 巴扎查分 <用户名>：用户排位查询 ──────────────────────────────────────────

def _user_cache_path(username: str) -> Path:
    safe = username.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"bazaar_{safe}_s14.png"


def _query_user_sync(username: str) -> bytes | None:
    scraper = _load_scraper("bazaar_user_scraper.py")
    asyncio.run(scraper.scrape_and_export(username, "14", str(CACHE_DIR)))
    safe = username.replace("/", "_").replace("\\", "_")
    png  = CACHE_DIR / f"bazaar_{safe}_s14.png"
    return png.read_bytes() if png.exists() else None


@bz_user.handle()
async def bz_user_rev(bot: Bot, event: Event):
    username = str(event.message).strip()[5:].strip()
    if not username:
        await bot.send(event=event, message=MessageSegment.text("请输入用户名，例如：巴扎查分 xikala"))
        return

    try:
        img_bytes = await asyncio.get_event_loop().run_in_executor(None, _query_user_sync, username)
    except Exception as e:
        nonebot.logger.warning(f"[bazaardb] 查分失败 username={username}: {e}")
        await bot.send(event=event, message=MessageSegment.text(f"查询失败：{e}"))
        return

    if img_bytes is None:
        await bot.send(event=event, message=MessageSegment.text(f"未找到「{username}」的排位记录（仅记录传奇段位以上）"))
        return

    nonebot.logger.info(f"[bazaardb] 查分成功 username={username}")
    await _send_image(bot, event, img_bytes)


# ── 巴扎绑定 <游戏账号>：绑定QQ到游戏账号 ────────────────────────────────────

@bz_bind.handle()
async def bz_bind_rev(bot: Bot, event: Event):
    if not isinstance(event, GroupMessageEvent):
        await bot.send(event=event, message=MessageSegment.text("请在群聊中使用此命令"))
        return

    game_account = str(event.message).strip()[5:].strip()
    if not game_account:
        await bot.send(event=event, message=MessageSegment.text("请输入游戏账号，例如：巴扎绑定 xikala"))
        return

    group_id = str(event.group_id)
    qq_id    = str(event.user_id)

    group_map = _bindings.setdefault(group_id, {})
    old_account = group_map.get(qq_id)
    group_map[qq_id] = game_account
    _save_bindings(_bindings)
    is_update = old_account and old_account != game_account
    nonebot.logger.info(f"[bazaardb] 绑定 group={group_id} qq={qq_id} -> {game_account} (旧={old_account})")

    # 查询用户当前排位数据
    rating_info = None
    try:
        async with aiohttp.ClientSession() as session:
            rating_info = await _fetch_latest_rating(session, game_account)
    except Exception:
        pass

    # 获取群名称
    group_name = group_id
    try:
        group_info = await bot.get_group_info(group_id=int(group_id))
        group_name = group_info.get("group_name", group_id)
    except Exception:
        pass

    # 获取 QQ 昵称
    nick = qq_id
    try:
        member_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(qq_id))
        nick = member_info.get("card") or member_info.get("nickname") or qq_id
    except Exception:
        pass

    if rating_info:
        rank_str  = f"#{rating_info['position']:,}" if rating_info.get("position") else "#-"
        score     = rating_info["rating"]
        header = "✅ 绑定更新！" if is_update else "✅ 绑定成功！"
        if is_update:
            header += f"\n🔄 {old_account} → {game_account}"
        msg = (
            f"{header}\n"
            f"🆔 游戏 ID: {game_account}\n"
            f"🔢 QQ 号: {qq_id}\n"
            f"👤 平台昵称: {nick}\n"
            f"👥 群组: {group_name}({group_id})\n"
            f"🏆 全服排名: {rank_str}\n"
            f"⭐ 天梯分数: {score}"
        )
    else:
        header = "✅ 绑定更新！" if is_update else "✅ 绑定成功！"
        if is_update:
            header += f"\n🔄 {old_account} → {game_account}"
        msg = (
            f"{header}\n"
            f"🆔 游戏 ID: {game_account}\n"
            f"🔢 QQ 号: {qq_id}\n"
            f"👤 平台昵称: {nick}\n"
            f"👥 群组: {group_name}({group_id})\n"
            f"⚠️ 暂无传奇段位排位记录"
        )

    await bot.send(event=event, message=MessageSegment.text(msg))


# ── 巴扎解绑：解除当前QQ的绑定 ───────────────────────────────────────────────

@bz_unbind.handle()
async def bz_unbind_rev(bot: Bot, event: Event):
    if not isinstance(event, GroupMessageEvent):
        await bot.send(event=event, message=MessageSegment.text("请在群聊中使用此命令"))
        return

    group_id = str(event.group_id)
    qq_id    = str(event.user_id)

    group_map = _bindings.get(group_id, {})
    if qq_id not in group_map:
        await bot.send(event=event, message=MessageSegment.text("你还没有绑定任何账号"))
        return

    account = group_map.pop(qq_id)
    _save_bindings(_bindings)

    nonebot.logger.info(f"[bazaardb] 解绑 group={group_id} qq={qq_id} account={account}")
    await bot.send(event=event, message=MessageSegment.text(f"✅ 解绑成功！已移除 {qq_id} → {account} 的绑定"))


# ── 巴扎排名：查询群内所有绑定成员排名 ───────────────────────────────────────

BASE_URL   = "https://bazaar.mrmao.life"
SEASON_ID  = "14"
MEDALS     = ["🥇", "🥈", "🥉"]

# 上次排名快照持久化：{ "群号": { "游戏账号": rating, ... } }
SNAPSHOT_FILE = Path(__file__).parent / "rank_snapshot.json"

def _load_snapshot() -> Dict[str, Dict[str, int]]:
    if SNAPSHOT_FILE.exists():
        try:
            return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_snapshot(data: Dict[str, Dict[str, int]]):
    SNAPSHOT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

_snapshots: Dict[str, Dict[str, int]] = _load_snapshot()


async def _fetch_latest_rating(session: aiohttp.ClientSession, username: str) -> Optional[dict]:
    """拉取用户最新一条排位记录，返回 {rating, position} 或 None"""
    url = f"{BASE_URL}/api/rating-history?username={username}&seasonId={SEASON_ID}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None)
            if data and isinstance(data, list):
                latest = data[-1]
                return {
                    "rating":   latest.get("rating", 0),
                    "position": latest.get("position", None),
                }
    except Exception as e:
        nonebot.logger.warning(f"[bazaardb] 拉取 {username} 失败: {e}")
    return None


@bz_rank.handle()
async def bz_rank_rev(bot: Bot, event: Event):
    if not isinstance(event, GroupMessageEvent):
        await bot.send(event=event, message=MessageSegment.text("请在群聊中使用此命令"))
        return

    group_id  = str(event.group_id)
    group_map = _bindings.get(group_id, {})

    if not group_map:
        await bot.send(event=event, message=MessageSegment.text(
            "本群还没有人绑定账号，请先发送「巴扎绑定 <游戏账号>」进行绑定"
        ))
        return

    # 并发拉取所有成员数据
    async with aiohttp.ClientSession() as session:
        tasks = {
            qq: asyncio.create_task(_fetch_latest_rating(session, account))
            for qq, account in group_map.items()
        }
        results: Dict[str, Optional[dict]] = {}
        for qq, task in tasks.items():
            results[qq] = await task

    # 尝试获取群成员昵称
    nick_map: Dict[str, str] = {}
    try:
        members = await bot.get_group_member_list(group_id=int(group_id))
        for m in members:
            uid = str(m["user_id"])
            nick_map[uid] = m.get("card") or m.get("nickname") or uid
    except Exception:
        pass

    # 上次快照
    last_snapshot = _snapshots.get(group_id, {})

    # 构建排行列表
    ranked = []
    no_data = []
    new_snapshot: Dict[str, int] = {}

    for qq, account in group_map.items():
        r = results.get(qq)
        if r:
            rating = r["rating"]
            new_snapshot[account] = rating
            # 与上次快照对比
            last_rating = last_snapshot.get(account)
            if last_rating is not None:
                delta = rating - last_rating
            else:
                delta = None
            ranked.append({
                "qq":       qq,
                "account":  account,
                "nick":     nick_map.get(qq, qq),
                "rating":   rating,
                "position": r["position"],
                "delta":    delta,
            })
        else:
            no_data.append(account)

    # 保存新快照
    _snapshots[group_id] = new_snapshot
    _save_snapshot(_snapshots)

    # 按 rating 降序排列
    ranked.sort(key=lambda x: x["rating"], reverse=True)

    total    = len(group_map)
    on_board = len(ranked)

    # 构建每条消息行
    def _delta_str(delta) -> str:
        if delta is None:
            return ""
        if delta > 0:
            return f" ▲+{delta}"
        if delta < 0:
            return f" ▼{delta}"
        return " →0"

    header = f"📅 群内绑定成员顺位 (共 {on_board}/{total} 人上榜)"
    if no_data:
        header += f"\n⚠️ 无传奇记录：{', '.join(no_data)}"

    detail_lines = []
    for i, item in enumerate(ranked):
        pos_str = f"#{item['position']}" if item['position'] else "#-"
        medal   = MEDALS[i] if i < 3 else f"{i + 1}."
        delta_s = _delta_str(item["delta"])
        detail_lines.append(
            f"{medal} {item['account']}({item['nick']}) - {pos_str} ({item['rating']}分{delta_s})"
        )

    # 用合并消息发送
    def _node(text: str) -> dict:
        return {
            "type": "node",
            "data": {
                "name":    "巴扎排名",
                "uin":     bot.self_id,
                "content": text,
            }
        }

    # 前三名每人单独一个气泡，4名及以后合并成一个气泡
    top3    = detail_lines[:3]
    rest    = detail_lines[3:]
    messages = [_node(header)]
    messages += [_node(line) for line in top3]
    if rest:
        messages.append(_node("\n".join(rest)))
    await bot.call_api("send_group_forward_msg", group_id=int(group_id), messages=messages)

