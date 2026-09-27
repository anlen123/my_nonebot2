import sqlite3
from ..mapper import *


def get_rarity(cardId: int) -> str:
    try:
        rarityList = get_nonebot_plugin_masterduel_rarity(f'SELECT * FROM rarity WHERE id = "{cardId}"')
    except IndexError:
        print(f"No card found with id {cardId}")
        return None
    if rarityList:
        rarity = rarityList[0]
        if rarity.rarity == 1:
            return "N"
        elif rarity.rarity == 2:
            return "R"
        elif rarity.rarity == 3:
            return "SR"
        else:
            return "UR"
    return None
