import json
import sqlite3
import requests


def set_rarity(sql: str):
    # 连接到 SQLite 数据库
    print(sql)
    conn = sqlite3.connect('../nonebot_plugin_masterduel.cdb')

    # 创建一个 Cursor 对象，用于执行 SQL 命令
    cursor = conn.cursor()

    # 执行 SQL 命令，获取 datas 表的所有行
    cursor.execute(sql)

    conn.commit()

    # 关闭连接
    conn.close()


def create_rarity():
    sql = """
    create table rarity
    (
        id     integer not null,
        rarity integer
    );
    """
    # 连接到 SQLite 数据库
    print(sql)
    conn = sqlite3.connect('../nonebot_plugin_masterduel.cdb')

    # 创建一个 Cursor 对象，用于执行 SQL 命令
    cursor = conn.cursor()

    # 执行 SQL 命令，获取 datas 表的所有行
    cursor.execute(sql)

    conn.commit()

    # 关闭连接
    conn.close()


def delete_rarity():
    sql = """
    drop table rarity;
    """
    # 连接到 SQLite 数据库
    print(sql)
    conn = sqlite3.connect('/plugins/nonebot_plugin_masterduel/nonebot_plugin_masterduel/nonebot_plugin_masterduel.cdb')

    # 创建一个 Cursor 对象，用于执行 SQL 命令
    cursor = conn.cursor()

    # 执行 SQL 命令，获取 datas 表的所有行
    cursor.execute(sql)

    conn.commit()

    # 关闭连接
    conn.close()


if __name__ == '__main__':
    try:
        delete_rarity()
    except:
        pass
    try:
        create_rarity()
    except:
        pass
    # resp = requests.get(
    #     "https://raw.githubusercontent.com/pixeltris/YgoMaster/91d3e5f9d58c63bb8c47ef33f1da3a81951139d9/YgoMaster/Data/CardList.json")


    # cardJson = json.loads(resp.text)

    # resp = requests.get(
    #     "https://raw.githubusercontent.com/pixeltris/YgoMaster/91d3e5f9d58c63bb8c47ef33f1da3a81951139d9/YgoMaster/Data/YdkIds.txt")

    # txt = resp.text.split("\n")

    with open("CardList.json","r") as f :
        cardJson = json.loads(f.read())

    with open("YdkIds.txt", "r") as f:
        txt = f.read().split("\n")

    for x in txt:
        if x:
            x = x.strip()
            if x:
                print(x)
                xL = x.split(" ")
                cardId = xL[0]
                rarity = xL[1]

                rarityId = cardJson.get(rarity)
                # print(rarityId)
                if rarityId:
                    print(rarityId)
                    set_rarity(f'insert into rarity(id,rarity) values ({int(cardId)},"{rarityId}")')
