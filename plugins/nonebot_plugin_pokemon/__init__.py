from pathlib import Path
import nonebot
from typing import List
from nonebot import get_driver
from nonebot import on_command, on_startswith, on_keyword, on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent
from nonebot.params import T_State
from openai import AsyncOpenAI
import os
import json

from .config import config

pokemon = on_regex(pattern="^(pm |宝可梦 |pokemon )")

@pokemon.handle()
async def pokemon_handler(bot: Bot, event: Event):
    cmd_msg = event.get_plaintext()
    if cmd_msg.startswith("pm"):
        cmd_msg = cmd_msg[3:]
    elif cmd_msg.startswith("宝可梦"):
        cmd_msg = cmd_msg[4:]
    elif cmd_msg.startswith("pokemon"):
        cmd_msg = cmd_msg[8:]
    cmd_msg = cmd_msg.strip()

    if not cmd_msg:
        await bot.send(event, message="请发送 pm+宝可梦名称")
        return

    pokemon_all = os.listdir(config.pokemon_path)
    poke_file = ""
    for po in pokemon_all:
        pm_name = po[5:-5]
        if pm_name == cmd_msg:
            poke_file = os.path.join(config.pokemon_path, po)
            break

    if not poke_file:
        ai_poke_name = await deepseek_poke_name(cmd_msg)
        ai_poke_name = ai_poke_name.strip()
        for po in pokemon_all:
            pm_name = po[5:-5]
            if pm_name == ai_poke_name:
                poke_file = os.path.join(config.pokemon_path, po)
                break
        
    if poke_file or poke_file != "404":
        with open(poke_file, "r", encoding="utf-8") as f:
            poke_data = f.read()
            poke_json = json.loads(poke_data)
            result = await format_pokemon_info(poke_json)
            await bot.send(event=event, message=result)
            return
        
    await bot.send(event, message="没有找到这个宝可梦")

def get_texing_des(texing:str):
    pokemon_texing_all = os.listdir(config.pokemon_texing_path)
    poke_texing_file = ""
    for po in pokemon_texing_all:
        texing_name = po[4:-5]
        if texing_name == texing:
            poke_texing_file = os.path.join(config.pokemon_texing_path, po)
            break
    if poke_texing_file:
        with open(poke_texing_file, "r", encoding="utf-8") as f:
            poke_texing_data = f.read()
            poke_texing_json = json.loads(poke_texing_data)
            return poke_texing_json['effect']
    
async def format_pokemon_info(data):
    # 基本信息
    name = data["name"]
    no = data["index"]
    pokemon_img_all = os.listdir(config.pokemon_img_path)
    for img in pokemon_img_all:
        if img.startswith(no):
            img_path = os.path.join(config.pokemon_img_path, img)
            break
    description = data["profile"].split("\n")[0]  # 取第一段描述

    # 属性
    types = "、".join(data["forms"][0]["types"])


    # 特性
    abilities = []
    for ability in data["forms"][0]["ability"]:
        ability_str = ability["name"]
        texing_desc = get_texing_des(ability_str)
        if ability["is_hidden"]:
            ability_str += "(隐藏特性)"
        texing_desc = texing_desc.replace("\n", "")
        abilities.append(f"{ability_str}【{texing_desc}】".strip())
    abilities_str = "\n".join(abilities)

    # 种族值
    stats = data["stats"][0]["data"]
    stats_str = (
        f"HP: {stats['hp']} 攻击: {stats['attack']} "
        f"防御: {stats['defense']} 特攻: {stats['sp_attack']} "
        f"特防: {stats['sp_defense']} 速度: {stats['speed']}"
    )

    # 进化信息
    evolution_info = "无进化形态"
    if "evolution_chains" in data and data["evolution_chains"]:
        chain = data["evolution_chains"][0]
        if len(chain) > 1:
            evolutions = []
            for i in range(1, len(chain)):
                evo = chain[i]
                evolutions.append(f"{evo['name']}({evo['text']})")
            evolution_info = " → ".join(evolutions)

    # 世代信息
    generations = []
    for flavor in data["flavor_texts"]:
        if flavor["versions"]:
            generations.append(flavor["name"])
    
    # 格式化消息
    message = [
        f"【{name}】\n",
        f"图鉴编号：{no}\n",
        MessageSegment.image(img_path),
        f"属性: {types}\n\n",
        f"特性: {abilities_str}\n\n",
        f"种族值:{stats_str}\n\n",
        f"描述: {description}\n",
        f"进化: {evolution_info}\n",
        f"出现世代: {generations}"
    ]

    return message


async def send_forward_msg_group(
    bot: Bot,
    event: GroupMessageEvent,
    name: str,
    msgs: List[str],
):
    def to_json(msg):
        return {
            "type": "node",
            "data": {
                "name": name,
                "uin": bot.self_id,
                "content": msg
            }
        }

    messages = [to_json(msg) for msg in msgs]
    await bot.call_api(
        "send_group_forward_msg",
        group_id=event.group_id,
        messages=messages
    )


async def deepseek_poke_name(content: str) -> list:
    client = AsyncOpenAI(api_key="77b1cd88-22d3-4c9e-9091-8394d5bbfcab",
                         base_url="https://ark.cn-beijing.volces.com/api/v3")
    messages = [
        {"role": "system", "content": "你现在扮演一个宝可梦名字纠正师，我会给你一个宝可梦的名字，但是我可能会打错字，根据我给的宝可梦名字给出可能是我想要的宝可梦的正确名字，实在不像是输入的宝可梦名字返回404，只需要给我宝可梦结果即可，无需说多余的话。"},
        {"role": "user", "content": content}
    ]
    # 请求API
    response = await client.chat.completions.create(
        model="deepseek-r1-250120",
        messages=messages,
        max_tokens=1024,
        temperature=0.7,
        stream=False
    )

    assistant_reply = response.choices[0].message.content
    return str(assistant_reply).strip()


ai_pokemon = on_regex(pattern="^(aipokemon |aipm )")


@ai_pokemon.handle()
async def ai_pokemon_handler(bot: Bot, event: Event):
    cmd_msg = event.get_plaintext()
    if cmd_msg.startswith("aipokemon"):
        cmd_msg = cmd_msg[10:]
    elif cmd_msg.startswith("aipm"):
        cmd_msg = cmd_msg[5:]
    cmd_msg = cmd_msg.strip()
    msg = await deepseek_poke_ai(cmd_msg)
    await bot.send(event=event, message=msg)
    
async def deepseek_poke_ai(content: str) -> list:
    client = AsyncOpenAI(api_key="77b1cd88-22d3-4c9e-9091-8394d5bbfcab",
                         base_url="https://ark.cn-beijing.volces.com/api/v3")
    messages = [
        {"role": "system", "content": "你现在扮演一个宝可梦大师，我会问你一些宝可梦的问题，你需要回答我的问题，并且要根据官网上的内容来回答，不能胡说八道。"},
        {"role": "user", "content": content}
    ]
    # 请求API
    response = await client.chat.completions.create(
        model="deepseek-r1-250120",
        messages=messages,
        max_tokens=1024,
        temperature=0.7,
        stream=False
    )

    assistant_reply = response.choices[0].message.content
    return str(assistant_reply).strip()