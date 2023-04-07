from pathlib import Path

import nonebot, aiohttp, json
from typing import List
from nonebot import get_driver
from .config import Config
from nonebot import on_command, on_startswith, on_keyword, on_message
from nonebot.plugin import on_notice, on_regex
from nonebot.rule import Rule, regex, to_me
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, Message, GroupMessageEvent, PrivateMessageEvent
from nonebot.params import T_State
import re, uuid, sqlite3, time


async def chat_gpt(uuidd, msg, lastAnswer):
    url = f"http://d.qiner520.com/app/info?msg={msg}&version=1.0.4&uuid={uuidd}&role=0"
    if lastAnswer:
        url = url + f"&lastAnswer={lastAnswer}"
    print('url: ', url)
    headers = {
        'Host': 'd.qiner520.com',
        'Accept': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'X-Requested-With': 'dulang.chatgpt.aiplus',
        'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            response_text = await response.text()

    ret = response_text
    print(ret)
    rett = json.loads(ret)['data']['msg']
    print(rett)
    return rett


chat_gpt1 = on_regex(pattern="^gpt")


@chat_gpt1.handle()
async def chat_gpt1_rev(event: Event):
    create_answers_table()
    content = event.get_plaintext()[3:]
    print(content)
    if content == "" or content is None:
        await chat_gpt2.finish(MessageSegment.text("内容不能为空！"))
        return
    res = ""
    session_id = await get_session_id(event)
    print(session_id)
    ans = await get_answers_by_session_id(session_id)
    if ans:  # 旧的会话，拿最新的时间，2个小时后会话失效
        print(ans)
        ans = ans[0]
        modified_time = ans[4]
        now_time = int(time.time())
        # 超出时间，删除，并创建新会话
        if int(int(now_time) - int(modified_time)) > 60 * 60 * 2:
            await delete_by_session_id(session_id)
            ci = 0
            while not res:
                try:
                    print(f"第{ci+1}此尝试...")
                    ci = ci + 1
                    if ci > 4:
                        res = "网络超时！！稍后重试"
                        break
                    res = await new_chat_session(content, res, session_id)
                    time.sleep(5)
                except:
                    pass
        else:
            ci = 0
            while not res:
                try:
                    print(f"第{ci+1}此尝试...")
                    ci = ci + 1
                    if ci > 4:
                        res = "网络超时！！稍后重试"
                        break
                    res = await chat_gpt(ans[1], content, ans[3])
                    time.sleep(5)
                except:
                    pass
            if res:
                await update_answer(str(ans[1]), str(session_id), res, str(int(time.time())))

    else:  # 新的会话
        ci = 0
        while not res:
            try:
                print(f"第{ci + 1}此尝试...")
                ci = ci + 1
                if ci > 4:
                    res = "网络超时！！稍后重试"
                    break
                res = await new_chat_session(content, res, session_id)
                time.sleep(5)
            except:
                pass

    if res:
        await chat_gpt1.finish(MessageSegment.text(res))


async def new_chat_session(content, res, session_id):
    print("new_chat_session: ", content, res, session_id)
    random_uuid = uuid.uuid4()
    await insert_answer(str(random_uuid), str(session_id), None, str(int(time.time())))
    res = await chat_gpt(random_uuid, content, '')
    if res:
        await update_answer(str(random_uuid), str(session_id), res, str(int(time.time())))
    return res


# chat_gpt2 = on_regex(pattern="^ai")


# @chat_gpt2.handle()
# async def chat_gpt2_rev(event: Event):
    # content = event.get_plaintext()[2:]
    # print(content)
    # if content == "" or content is None:
        # await chat_gpt2.finish(MessageSegment.text("内容不能为空！"))
        # return
    # res = ""
    # try:
        # res = await chat_ai_bai_piao(content)
    # except Exception as error:
        # await chat_gpt2.finish(str(error))
        # return

    # if res:
        # await chat_gpt2.finish(MessageSegment.text(res))


async def chat_ai_bai_piao(msg):
    # msg = "请问你怎么看待同性恋爱"
    url = f"http://d.qiner520.com/app/info?msg={msg}"

    headers = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Proxy-Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36'
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            response_text = await response.text()
            return json.loads(response_text)['data']['msg']


clear = on_regex(pattern="^clear$")


@clear.handle()
async def clear_rev(event: Event):
    create_answers_table()
    session_id = await get_session_id(event)
    await delete_by_session_id(session_id)
    await clear.send(message="清除成功！！")


async def get_session_id(event):
    if isinstance(event, PrivateMessageEvent):
        session_id = f"{event.user_id}"
        return session_id
    if isinstance(event, GroupMessageEvent):
        session_id = f"{event.group_id}:{event.user_id}"
        return session_id
    return None


DB_PATH = "chat_db.sqlite3"


def create_answers_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chat_gpt';")
    result = c.fetchone()
    if result is None:
        print('表不存在')
        c.execute('''CREATE TABLE chat_gpt
                     (id INTEGER PRIMARY KEY,
                     uuid TEXT,
                     session_id TEXT,
                     last_answer TEXT,
                     modified_time TEXT)''')
    else:
        print('表存在')

    conn.commit()
    conn.close()


async def insert_answer(uuid, session_id, last_answer, modified_time):
    print("插入：", end='')
    print(uuid, session_id, last_answer, modified_time)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    query = '''INSERT INTO chat_gpt (uuid, session_id, last_answer, modified_time)
               VALUES (?, ?, ?, ?)'''
    c.execute(query, (uuid, session_id, last_answer, modified_time))

    conn.commit()
    conn.close()


def delete_user_answers(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = '''DELETE FROM chat_gpt WHERE user_id = ?'''
    c.execute(query, (user_id,))
    conn.commit()
    conn.close()


async def delete_by_session_id(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    query = '''DELETE FROM chat_gpt WHERE session_id = ?'''
    c.execute(query, (session_id,))

    conn.commit()
    conn.close()


async def get_answers_by_session_id(session_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    query = '''SELECT * FROM chat_gpt WHERE session_id = ? ORDER BY modified_time DESC'''
    print(session_id)
    c.execute(query, (session_id,))
    answers = c.fetchall()

    conn.close()
    return answers


async def update_answer(uuid: str, session_id: str, last_answer: str, modified_time: str):
    print("更新：", end='')
    print(uuid, session_id, last_answer, modified_time)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    query = '''UPDATE chat_gpt SET last_answer = ?, modified_time = ? WHERE uuid = ? AND session_id = ?'''
    c.execute(query, (last_answer, modified_time, uuid, session_id))

    conn.commit()
    conn.close()

