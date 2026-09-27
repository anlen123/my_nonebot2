from .Datas import Datas
from .Texts import Texts


def buildYgoCard(datas: Datas, texts: Texts):
    return YgoCard(datas.id, datas.ot, datas.alias, datas.setcode, datas.type, datas.atk, datas.def_, datas.level,
                   datas.race, datas.attribute, datas.category, texts.name, texts.desc, texts.str1, texts.str2,
                   texts.str3, texts.str4, texts.str5)


class YgoCard:

    def __init__(self, id: int, ot, alias, setcode, type, atk, def_, level, race, attribute, category, name, desc, str1,
                 str2, str3, str4, str5):
        self.id = id
        self.ot = ot
        self.alias = alias
        self.setcode = setcode
        self.type = type
        self.atk = atk
        self.def_ = def_
        self.level = level
        self.race = race
        self.attribute = attribute
        self.category = category
        self.name = name
        self.desc = desc
        self.str1 = str1
        self.str2 = str2
        self.str3 = str3
        self.str4 = str4
        self.str5 = str5

    @classmethod
    def from_tuple(cls, data_tuple):
        return cls(*data_tuple)

    @classmethod
    def from_dict(cls, data_dict):
        return cls(**data_dict)

    def __str__(self):
        return f"YgoCard(id={self.id}, ot={self.ot}, alias={self.alias}, setcode={self.setcode}, type={self.type}, atk={self.atk}, def_={self.def_}, level={self.level}, race={self.race}, attribute={self.attribute}, category={self.category}, name={self.name}, desc={self.desc}, str1={self.str1}, str2={self.str2}, str3={self.str3}, str4={self.str4}, str5={self.str5})"
