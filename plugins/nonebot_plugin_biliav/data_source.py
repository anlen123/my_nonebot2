import httpx
import json
import nonebot,requests,re

from nonebot.adapters.onebot.v11 import MessageSegment, Message, Bot, Event

url = 'https://api.bilibili.com/x/web-interface/view'

global_config = nonebot.get_driver().config
config = global_config.dict()
b_comments = config.get('b_comments',"True")
if b_comments != "True" and b_comments != "False":
    b_comments = "True"

b_comments = eval(b_comments)


import math
def bv2av(bv):
    url = f'https://www.bilibili.com/video/{bv}'
    headers = {'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    html = requests.get(url,headers=headers)
    html.encoding = 'utf-8'
    content = html.text
    aid_regx = '"aid":(.*?),"bvid":"{}"'.format(bv)
    video_aid = re.findall(aid_regx, content)[0]
    return video_aid


async def get_av_data(av):
    av= str(av)
    if av[0:2] == "BV":
        avcode= bv2av(av)
        print(f"av:{avcode}")
    else:
        avcode = av.replace("av","")
    new_url =  url + f"?aid={avcode}"
    print(f"url:{new_url}")
    async with httpx.AsyncClient() as client:
        headers = {'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'}
        r = await client.get(new_url, headers=headers)
    rd=  json.loads(r.text)
    print(rd)
    if rd['code']=="0":
        if not rd["data"]:
            return None
    try:
        title = rd['data']['title']
        pic = rd['data']['pic']
        stat = rd['data']['stat']
        view = stat['view']
        danmaku = stat['danmaku']
        reply = stat['reply']
        fav = stat['favorite']
        coin = stat['coin']
        share = stat['share']
        like = stat['like']
        link = f"https://www.bilibili.com/video/av{avcode}"
        desc = rd['data']['desc']

        msg =  "标题:" + title + "\n" + MessageSegment.image(pic) + f"播放:{view} 弹幕:{danmaku} 评论:{reply} 收藏:{fav} 硬币:{coin} 分享:{share} 点赞:{like} \n点击连接进入: \n{link}\n简介: {desc}"

        print(msg)
        return msg
    except:
        return "错误!!! 没有此av或BV号。"
