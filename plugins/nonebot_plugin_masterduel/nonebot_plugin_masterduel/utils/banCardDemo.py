import httpx
import json
from objprint import op
import sqlite3
import sys, io, re

nonebot_plugin_masterduel_root_dir = "D:\\nb2\\my_nonebot2\\plugins\\nonebot_plugin_masterduel\\nonebot_plugin_masterduel"


def set_nonebot_plugin_masterduel(sql: str):
    print(sql)
    conn = sqlite3.connect(f'{nonebot_plugin_masterduel_root_dir}\\nonebot_plugin_masterduel.cdb')
    cursor = conn.cursor()
    cursor.execute(sql)
    conn.commit()
    conn.close()


def write_ban_cards():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    with open('lflist.conf', 'r', encoding='utf-8') as f:
        content = f.read()

    content = '\n'.join([_ for _ in content.split('\n')[1:] if _])
    # print(content)
    datas = re.split("(!\d{4}\.\d{1,2}(?:\s[^\n]+)?)", content)[1:]
    for i in range(0, len(datas), 2):
        if 'TCG' not in datas[i]:
            # print(datas[i])
            date_str = (re.split("[!# \n]", datas[i])[1])
            date_ban = date_str.split(".")[0] + '.' + date_str.split(".")[1].zfill(2)
            cards = (datas[i + 1]).split("\n")
            for card in cards:
                if str(card.split(" ")[0]).isdigit():
                    card_info = card.split(" ")
                    # print(card_info)
                    set_nonebot_plugin_masterduel(
                        f'insert into ban (id,type,date,ban_type) values ("{card_info[0]}","1","{date_ban}","{card_info[1]}")')
