import nonebot
from typing import List
from nonebot.rule import Rule
from nonebot.plugin import on_message, on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment, GroupMessageEvent
import aiohttp, re, os, random, cv2, asyncio, base64, platform, shutil, subprocess

logger = nonebot.logger

from PIL import Image

from nonebot import require

require("nonebot_plugin_apscheduler")
global_config = nonebot.get_driver().config
config = global_config.dict()

imgRoot = config.get('imgroot') if config.get('imgroot') else f"{os.environ['HOME']}/"
proxy_aiohttp = config.get('aiohttp') if config.get('aiohttp') else ""
pixiv_cookies = config.get('pixiv_cookies') if config.get('pixiv_cookies') else ""
ffmpeg = config.get('ffmpeg') if config.get('ffmpeg') else "/usr/bin/ffmpeg"
headersCook = {'referer': 'https://www.pixiv.net',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.163 Safari/537.36', }
if pixiv_cookies:
    headersCook['cookie'] = pixiv_cookies

PIXIV_R18 = config.get('pixiv_r18', 'True')
if PIXIV_R18 and (PIXIV_R18 == 'True' or PIXIV_R18 == 'False'):
    PIXIV_R18 = eval(PIXIV_R18)
elif PIXIV_R18:
    try:
        PIXIV_R18 = eval(PIXIV_R18)
        if not isinstance(PIXIV_R18, list):
            print("配置错误！！pixiv_r18应该是列表")
        else:
            for x in PIXIV_R18:
                if not (isinstance(x, int) or (isinstance(x, str) and str(x).isdigit())):
                    print("配置错误！！pixiv_r18中应该是int类型或者str的数值类型")
        PIXIV_R18 = [int(_) for _ in PIXIV_R18]
    except:
        print("配置错误！！")

BAN_PIXIV_R18 = eval(config.get('ban_pixiv_r18', '[]'))
BAN_PIXIV_R18 = [int(_) for _ in BAN_PIXIV_R18]

pathHome = f"{imgRoot}QQbotFiles\pixiv"
if not os.path.exists(pathHome):
    os.makedirs(pathHome)

pathZipHome = f"{imgRoot}QQbotFiles\pixivZip"
if not os.path.exists(pathZipHome):
    os.makedirs(pathZipHome)


# ── 支持的链接形式 ──────────────────────────────────────────────────────────
#   https://www.pixiv.net/artworks/149547276
#   https://www.pixiv.net/i/149547276          （短链，手机客户端分享常用）
#   https://www.pixiv.net/en/artworks/149547276
#   https://www.pixiv.net/member_illust.php?illust_id=149547276
#   illust_id=149547276
PIXIV_ID_RE = re.compile(r"(?:artworks/|/i/|illust_id=)(\d{4,})", re.I)


def extract_pid(text: str):
    """从消息文本里提取 pixiv 作品 ID"""
    if not text:
        return None
    if "pixiv.net" not in text and "illust_id" not in text:
        return None
    match = PIXIV_ID_RE.search(text)
    return match.group(1) if match else None


def message_text(event: Event) -> str:
    """纯文本和原始消息都取一遍，兼容不同客户端把链接塞进 segment 的情况"""
    parts = []
    for getter in ("get_plaintext", "get_message"):
        try:
            value = getattr(event, getter)()
        except Exception:
            continue
        if value:
            parts.append(str(value))
    return "\n".join(parts)


async def api_json(session, url: str, with_cookie: bool = True, timeout: int = 25):
    """请求 pixiv 接口；cookie 失效（401/403）时自动改用匿名重试"""
    headers = dict(headersCook)
    if not with_cookie or not pixiv_cookies:
        headers.pop("cookie", None)
    status, data = None, None
    try:
        async with session.get(url, headers=headers, proxy=proxy_aiohttp,
                               timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            status = resp.status
            if status not in (401, 403):
                data = await resp.json(content_type=None)
    except Exception as exc:
        logger.warning(f"[pixiv] 请求失败 {url}: {type(exc).__name__} {exc}")
        return {"error": True, "exception": str(exc)}

    if data is None:
        if with_cookie and pixiv_cookies:
            logger.warning(f"[pixiv] cookie 已失效（HTTP {status}），改用匿名请求")
            return await api_json(session, url, with_cookie=False, timeout=timeout)
        return {"error": True, "status": status}
    return data


def isPixivURL() -> Rule:
    async def isPixivURL_(bot: "Bot", event: "Event") -> bool:
        if event.get_type() != "message":
            return False
        return extract_pid(message_text(event)) is not None

    return Rule(isPixivURL_)


pixivURL = on_message(rule=isPixivURL())


async def validate_r18(bot: Bot, event: Event, PID: str) -> bool:
    if not await pan_R18(PID):
        return True
    if isinstance(PIXIV_R18, bool):
        if not PIXIV_R18:
            await bot.send(event=event, message="不支持R18，请修改配置后操作！")
            return False
        else:
            if isinstance(event, GroupMessageEvent):
                flag = any(True if str(_) == str(event.group_id) else False for _ in BAN_PIXIV_R18)
                if flag:
                    await bot.send(event=event, message="不支持R18，请修改配置后操作！")
                    return False
                return True
    elif isinstance(PIXIV_R18, list):
        if isinstance(event, GroupMessageEvent):
            flag = any(True if str(_) == str(event.group_id) else False for _ in PIXIV_R18)
            if not flag:
                await bot.send(event=event, message="不支持R18，请修改配置后操作！")
            return flag

    return True


@pixivURL.handle()
async def pixiv_URL(bot: Bot, event: Event):
    PID = extract_pid(message_text(event))
    logger.info(f"[pixiv] 检测到作品链接 PID={PID}")
    if not PID:
        return
    if not await validate_r18(bot, event, PID):
        return
    xx = (await check_GIF(PID))
    if xx != "NO":
        await GIF_send(xx, PID, event, bot)
    else:
        await send(PID, event, bot)


pixiv = on_regex(pattern="^pixiv\ ")


@pixiv.handle()
async def pixiv_rev(bot: Bot, event: Event):
    PID = str(event.get_plaintext()).strip()[6:].strip()
    if not await validate_r18(bot, event, PID):
        return
    xx = (await check_GIF(PID))
    if xx != "NO":
        print("是动图")
        await GIF_send(xx, PID, event, bot)
    else:
        print("不是动图")
        await send(PID, event, bot)


async def fetch(session, url, name, attempts=3):
    """下载单张图，失败自动重试（pixiv 大图 + 代理很容易超时/断流）"""
    logger.info(f"[pixiv] 下载图片 {url}")
    for index in range(attempts):
        try:
            async with session.get(
                    url=url, headers=headersCook, proxy=proxy_aiohttp,
                    timeout=aiohttp.ClientTimeout(total=300, sock_connect=30, sock_read=150),
            ) as response:
                code = response.status
                if code == 200:
                    content = await response.content.read()
                    if content:
                        with open(f"{imgRoot}QQbotFiles\pixiv\\" + name, mode='wb') as f:
                            f.write(content)
                        return True
                    logger.warning(f"[pixiv] 下载内容为空 {name}")
                else:
                    logger.warning(f"[pixiv] 下载失败 HTTP {code} {name}")
        except Exception as exc:
            logger.warning(f"[pixiv] 下载出错 {index + 1}/{attempts} {name}: {type(exc).__name__} {exc}")
        if index < attempts - 1:
            await asyncio.sleep(2)
    return False


async def main(PID):
    """返回下载到本地的文件名列表（多图作品会全部下载）"""
    async with aiohttp.ClientSession() as session:
        content = await api_json(session, f"https://www.pixiv.net/ajax/illust/{PID}")
        if content.get('error'):
            logger.warning(f"[pixiv] 获取作品 {PID} 失败: {str(content)[:200]}")
            return None
        body = content.get('body') or {}

        # 每项为 (原图地址, 备用地址)；原图太大/超时时自动退回 master1200
        entries = []
        single = (body.get('urls') or {}).get('original')
        page_count = int(body.get('pageCount') or 1)
        if single and page_count <= 1:
            entries.append((single, (body.get('urls') or {}).get('regular')))
        else:
            # 多图作品：illust 接口的 urls.original 只给第一张(p0)，必须走 pages 接口
            # 登录限定 / 未登录时 illust 接口不返回地址，同样走 pages 接口
            logger.info(f"[pixiv] 多图作品（{page_count} 张）或 illust 无地址，改用 pages 接口")
            pages = await api_json(session, f"https://www.pixiv.net/ajax/illust/{PID}/pages")
            for item in (pages.get('body') or []):
                u = item.get('urls') or {}
                if u.get('original'):
                    entries.append((u['original'], u.get('regular')))
            if not entries and single:
                entries.append((single, (body.get('urls') or {}).get('regular')))
            urls = [e[0] for e in entries]

            if not urls:
                logger.info("[pixiv] 尝试第三方 api")
                resp = await session.get(
                    url=f"https://api.obfs.dev/api/pixiv/illust?id={PID}",
                    headers=headersCook, proxy=proxy_aiohttp,
                    timeout=aiohttp.ClientTimeout(total=25),
                )
                try:
                    cc = await resp.json(content_type=None)
                except Exception:
                    cc = {"error": True}
                if not cc.get('error'):
                    illust = cc.get('illust') or {}
                    single_page = illust.get('meta_single_page') or {}
                    if single_page.get('original_image_url'):
                        entries = [(single_page['original_image_url'], None)]
                    else:
                        urls = [(p.get('image_urls') or {}).get('original')
                                for p in (illust.get('meta_pages') or [])]
                        urls = [u for u in urls if u]
                        entries = [(u, None) for u in urls]

        if not entries:
            logger.warning(f"[pixiv] 作品 {PID} 没拿到图片地址（这类作品需要有效的登录 cookie）")
            return None

        names = []
        for original, fallback in entries[:20]:
            name = original[original.rfind('/') + 1:] or f"{PID}.jpg"
            if await fetch(session, original, name):
                names.append(name)
                continue
            if fallback:
                name2 = fallback[fallback.rfind('/') + 1:]
                logger.warning(f"[pixiv] 原图失败，改用 master1200：{name2}")
                if await fetch(session, fallback, name2, attempts=2):
                    names.append(name2)
        return names or None


async def get_Img_ByDay(url):
    async with aiohttp.ClientSession() as session:
        if url == 'day':
            url = 'https://www.pixiv.net/ranking.php'
        else:
            url = f'https://www.pixiv.net/ranking.php?mode={url}'
        response = await session.get(url=url, headers=headersCook, proxy=proxy_aiohttp)
        text = (await response.content.read()).decode()
        img_list = set(re.findall('\<a href\=\"\/artworks\/(.*?)\"', text))
        return list(img_list)


pixivRank = on_regex(pattern="^pixivRank\ ")


@pixivRank.handle()
async def pixiv_rev(bot: Bot, event: Event):
    info = str(event.get_plaintext()).strip()[10:].strip()
    dic = {"1": "day", "7": "weekly", "30": "monthly"}
    if info in dic.keys():
        img_list = random.choices(await get_Img_ByDay(dic[info]), k=5)
        names = []
        for img in img_list:
            names.append(await main(img))
        if not names:
            await bot.send(event=event, message="发生了异常情况")
        else:
            msg = Message()
            for name in names:
                if name:
                    for t in name:
                        path = f"{imgRoot}QQbotFiles\pixiv\\{t}"
                        size = os.path.getsize(path)
                        if size // 1024 // 1024 >= 10:
                            await ya_suo(path)
                        msg += MessageSegment.image(await base64_path(path))
            try:
                if isinstance(event, GroupMessageEvent):
                    await send_forward_msg_group(bot, event, 'qqbot', msg)
                else:
                    if msg:
                        await bot.send(event=event, message=msg)
            except:
                await bot.send(event=event, message="查询失败, 帐号有可能发生风控，请检查")
    else:
        await bot.send(event=event, message=Message("参数错误\n样例: 'pixivRank 1' , 1:day,7:weekly,30:monthly"))


async def base64_path(path: str):
    ff = "空"
    print(path)
    with open(path, "rb") as f:
        ff = base64.b64encode(f.read()).decode()
    return f"base64://{ff}"


def _img_path(name: str) -> str:
    return f"{imgRoot}QQbotFiles\pixiv\\{name}"


def _resize_once(path: str) -> bool:
    """把图片尺寸减半（原地覆盖），失败返回 False"""
    try:
        image = cv2.imread(path)
        if image is None:
            return False
        shape = image.shape
        res = cv2.resize(image, (shape[1] // 2, shape[0] // 2), interpolation=cv2.INTER_AREA)
        return bool(cv2.imwrite(path, res))
    except Exception as exc:
        logger.warning(f"[pixiv] 压缩失败 {path}: {type(exc).__name__} {exc}")
        return False


async def prepare_images(names):
    """发送前瘦身：单张 >=10MB 压缩；整批 >20MB 再继续压最大的，避免 QQ 风控/超限"""
    for name in names:
        path = _img_path(name)
        if os.path.getsize(path) // 1024 // 1024 >= 10:
            await ya_suo(path)
    for _ in range(8):
        sizes = {n: os.path.getsize(_img_path(n)) for n in names}
        if sum(sizes.values()) // 1024 // 1024 < 20:
            break
        biggest = max(sizes, key=sizes.get)
        if sizes[biggest] // 1024 < 600:  # 已经很小，再压就没法看了
            break
        if not _resize_once(_img_path(biggest)):
            break


async def send(PID: str, event: Event, bot: Bot):
    names = await main(PID)
    if not names:
        if not pixiv_cookies and await pan_R18(PID):
            await bot.send(event=event, message="这是 R-18 作品，需要配置有效的 pixiv 登录 cookie（PIXIV_COOKIES）才能发送")
        else:
            await bot.send(event=event, message="没有这个PID的图片")
    else:
        await prepare_images(names)
        msg = Message()
        for name in names:
            msg += MessageSegment.image(await base64_path(_img_path(name)))
        flag = False
        try:
            if isinstance(event, GroupMessageEvent):
                print("1")
                await send_forward_msg_group(bot, event, 'qqbot', msg)
                print(event.user_id)
                await bot.send_private_msg(user_id=event.user_id, message=msg)
            else:
                print("2")
                await bot.send(event=event, message=msg)
            print("5")
        except Exception as ee:
            logger.warning(f"[pixiv] 首次发送失败，重试一次: {type(ee).__name__} {ee}")
            try:
                msg = Message()
                for name in names:
                    msg += MessageSegment.image(await base64_path(_img_path(name)))
                if isinstance(event, GroupMessageEvent):
                    print("3")
                    await send_forward_msg_group(bot, event, 'qqbot', msg)
                    flag = True
                else:
                    print("4")
                    await bot.send(event=event, message=msg)
                    flag = True
                
            except:
                await bot.send(event=event, message="查询失败, 帐号有可能发生风控，请检查!!!")
                
            if not flag:
                await bot.send(event=event, message=msg)


# 压缩图片大小
async def ya_suo(path):
    guard = 0
    while os.path.getsize(path) // 1024 // 1024 >= 10 and guard < 6:
        guard += 1
        if not _resize_once(path):
            break


# 非动图返回 "NO", 动图返回下载地址
async def check_GIF(PID: str) -> str:
    """非动图返回 "NO"，动图返回 zip 下载地址"""
    url = f'https://www.pixiv.net/ajax/illust/{PID}/ugoira_meta'
    async with aiohttp.ClientSession() as session:
        content = await api_json(session, url)
        if content.get('error'):
            return "NO"
        return (content.get('body') or {}).get('originalSrc') or "NO"


async def GIF_send(url: str, PID: str, event: Event, bot: Bot):
    """动图（ugoira）：下载 zip → 解包 → ffmpeg 合成 gif → 过大自动缩小 → 发送"""
    path_pre = f"{imgRoot}QQbotFiles/pixivZip/{PID}"
    gif_path = f"{path_pre}/{PID}.gif"

    if not os.path.exists(gif_path):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url=url, headers=headersCook, proxy=proxy_aiohttp,
                                       timeout=aiohttp.ClientTimeout(total=180)) as response:
                    if response.status != 200:
                        await bot.send(event=event, message=f"动图下载失败（HTTP {response.status}）")
                        return
                    content = await response.content.read()

            os.makedirs(path_pre, exist_ok=True)
            zip_path = f"{path_pre}.zip"
            with open(zip_path, "wb") as f:
                f.write(content)
            shutil.unpack_archive(zip_path, path_pre)
            try:
                os.remove(zip_path)
            except OSError:
                pass

            frames = sorted(n for n in os.listdir(path_pre) if n.lower().endswith((".jpg", ".png")))
            if not frames:
                await bot.send(event=event, message="动图解包失败")
                return
            for index, name in enumerate(frames):  # 重命名成 000000.jpg 供 ffmpeg 读取
                source = os.path.join(path_pre, name)
                target = os.path.join(path_pre, f"{index:06d}.jpg")
                if source != target:
                    os.replace(source, target)

            subprocess.run(
                [ffmpeg, "-y", "-r", str(max(len(frames), 1)),
                 "-i", os.path.join(path_pre, "%06d.jpg"), gif_path],
                capture_output=True, timeout=300,
            )
        except Exception as exc:
            logger.warning(f"[pixiv] 合成动图失败 PID={PID}: {type(exc).__name__} {exc}")
            await bot.send(event=event, message="动图合成失败")
            return

    if not os.path.exists(gif_path):
        await bot.send(event=event, message="动图生成失败")
        return

    guard = 0
    while os.path.getsize(gif_path) // 1024 // 1024 >= 15 and guard < 4:
        guard += 1
        tmp = f"{path_pre}/{PID}_temp.gif"
        try:
            subprocess.run([ffmpeg, "-y", "-i", gif_path, "-vf", "scale=iw/2:ih/2", tmp],
                           capture_output=True, timeout=300)
            os.replace(tmp, gif_path)
        except Exception as exc:
            logger.warning(f"[pixiv] 动图压缩失败: {exc}")
            break

    try:
        await bot.send(event=event, message=MessageSegment.image(await base64_path(gif_path)))
    except Exception as exc:
        logger.warning(f"[pixiv] 动图发送失败: {exc}")
        await bot.send(event=event, message="查询失败, 帐号有可能发生风控，请检查")


async def run(cmd: str):
    os.system(
        cmd)  # print(cmd)  # proc = await asyncio.create_subprocess_shell(  #     cmd,  #     stdout=asyncio.subprocess.PIPE,  #     stderr=asyncio.subprocess.PIPE)

    # stdout, stderr = await proc.communicate()  # return (stdout + stderr).decode()


# 合并消息
async def send_forward_msg_group(bot: Bot, event: GroupMessageEvent, name: str, content):
    """合并转发：整条消息作为一个节点，兼容 NapCat / Lagrange"""
    node = {"type": "node", "data": {"name": name, "uin": str(bot.self_id), "content": str(content)}}
    await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=[node])


async def pan_R18(PID) -> bool:
    """判断作品是否为 R-18"""
    url = f"https://www.pixiv.net/ajax/illust/{PID}"
    async with aiohttp.ClientSession() as session:
        content = await api_json(session, url)
        if content.get('error'):
            return False
        body = content.get('body') or {}
        if body.get('xRestrict') == 1:
            return True
        for item in (body.get('tags') or {}).get('tags') or []:
            tag = str(item.get('tag', '')).upper().replace('_', '-')
            if tag in {'R-18', 'R18', 'R-18G', 'R18G'}:
                return True
        return False
