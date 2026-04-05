import base64
import nonebot
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment

xr = on_regex(pattern=r"^xr ")


@xr.handle()
async def xr_rev(bot: Bot, event: Event):
    url = str(event.message).strip()[3:].strip()
    if not url:
        await bot.send(event=event, message=MessageSegment.text("请输入 URL，例如：xr https://example.com"))
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        url = f"https://{url}"
    # 修正中文标点误输入
    url = url.replace("。", ".").replace("，", ".").replace(",", ".")

    await bot.send(event=event, message=MessageSegment.text("正在渲染，请稍候..."))

    try:
        img_bytes = await screenshot(url)
    except Exception as e:
        nonebot.logger.warning(f"[xuanran] 截图失败 url={url}: {e}")
        await bot.send(event=event, message=MessageSegment.text(f"渲染失败：{e}"))
        return

    img_b64 = base64.b64encode(img_bytes).decode()
    await bot.send(event=event, message=MessageSegment.image(f"base64://{img_b64}"))


async def screenshot(url: str) -> bytes:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        try:
            await page.goto(url, wait_until="networkidle", timeout=20000)
            # 截取完整页面（高度自适应）
            img_bytes = await page.screenshot(full_page=True, type="png")
        finally:
            await browser.close()

    return img_bytes
