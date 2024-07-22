import sqlite3
import difflib

import pypinyin, re


def get_id_and_name_all():
    # 连接到 SQLite 数据库

    conn = sqlite3.connect('plugins/nonebot_plugin_masterduel/nonebot_plugin_masterduel/cards.cdb')

    # 创建一个 Cursor 对象，用于执行 SQL 命令
    cursor = conn.cursor()

    # 执行 SQL 命令，获取 datas 表的所有行
    cursor.execute("select id,name from texts")

    # 获取查询结果
    rows = cursor.fetchall()

    # 创建一个空列表，用于存储 Ygo_Card 对象
    textsList = []

    # 对于每一行，创建一个 Ygo_Card 对象，并添加到列表中

    # 关闭连接
    conn.close()
    return rows


def get_max_like_id(name: str):
    name = pypinyin.pinyin(name, pypinyin.NORMAL)
    name = [x[0] for x in name]
    print(name)

    rows = get_id_and_name_all()
    ret = None
    max_number = 0

    for row in rows:
        c_name = pypinyin.pinyin(row[1], pypinyin.NORMAL)
        c_name = [x[0] for x in c_name]
        # print(c_name)
        dis = difflib.SequenceMatcher(None, c_name, name).ratio()
        if dis > max_number:
            max_number = dis
            ret = row
    print(ret)
    return ret[0]


if __name__ == '__main__':
    msg = "上传游戏王卡图 121"
    print(re.findall("^上传游戏王卡图\ \d+$", msg))
