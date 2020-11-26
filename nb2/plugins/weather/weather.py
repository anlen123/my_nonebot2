from nonebot import on_command
from nonebot.rule import to_me
from nonebot.adapters.cqhttp import Bot, Event
import asyncio

# on_command 注册一个消息类型的命令处理器
# "天气" 指定 command 参数 - 命令名
# rule 补充事件响应器的匹配规则
# priority 事件响应器优先级
# block 是否阻止事件传递
weather = on_command("天气", rule=to_me(), priority=5)

print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")
print("天气插件启动了")


# 识别参数 并且给state 赋值
@weather.handle()
async def handle_first_receive(bot: Bot, event: Event, state: dict):
    print("=============" + event.message + "============")
    args = str(event.message).strip()  # 首次发送命令时跟随的参数，例：/天气 上海，则args为上海
    if args:
        state["city"] = args  # 如果用户发送了参数则直接赋值


# 调用
# @matcher.got(key, [prompt="请输入key"], [args_parser=function]):
# 指示 NoneBot 当 state 中不存在 key 时向用户发送 prompt 等待用户回复并赋值给 state[key]
@weather.got(key="city", prompt="你想查询哪个城市的天气呢？")
async def handle_city(bot: Bot, event: Event, state: dict):
    city = state["city"]
    if city not in ["上海", "北京"]:
        await weather.reject("你想查询的城市暂不支持，请重新输入！")
    city_weather = await get_weather(city)
    print(city_weather+"=============")
    await weather.finish(city_weather)


# 业务逻辑
async def get_weather(city: str):
    return f"{city}的天气是..."
